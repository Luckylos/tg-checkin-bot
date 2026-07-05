from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from .flow_transport import FlowTransport, TelegramClientLike
from .models import FlowStep, JobConfig, MatchRules
from .telegram import command_entities

ReplyStatus = Literal["abort", "success", "retry", "unknown"]
FlowStatus = Literal[
    "DONE_SUCCESS",
    "DONE_COUNT_REACHED",
    "STOPPED_ABORT_TEXT",
    "STOPPED_UNKNOWN_REPLY",
    "FAILED_TIMEOUT",
    "FAILED_TELEGRAM_ERROR",
]


class TelegramMessage(Protocol):
    id: int
    out: bool
    raw_text: str | None
    buttons: Any


class FlowTransportLike(Protocol):
    async def send_message(self, entity, message: str, *, formatting_entities=None) -> Any: ...
    async def latest_message_id(self, entity) -> int: ...
    async def wait_for_reply(self, entity, *, after_id: int, timeout: float): ...


@dataclass(frozen=True)
class ReplyClassification:
    status: ReplyStatus
    reason: str
    matched_text: str | None = None


@dataclass(frozen=True)
class FlowStepResult:
    index: int
    sent: str
    reply_id: int
    reply_text: str
    classification: ReplyClassification
    round: int = 1


@dataclass(frozen=True)
class FlowResult:
    job_name: str
    steps: tuple[FlowStepResult, ...]
    status: FlowStatus = "DONE_SUCCESS"
    round: int = 1
    matched_text: str | None = None
    reason: str = "success"


class FlowExecutionError(RuntimeError):
    """Raised when a configured bot flow cannot safely continue."""


def classify_reply(text: str, rules: MatchRules) -> ReplyClassification:
    matched = _find_first_match(text, rules.abort_on_text)
    if matched:
        return ReplyClassification("abort", "abort_text_matched", matched)

    matched = _find_first_match(text, rules.success_on_text)
    if matched:
        return ReplyClassification("success", "success_text_matched", matched)

    matched = _find_first_match(text, rules.retry_on_text)
    if matched:
        return ReplyClassification("retry", "retry_text_matched", matched)

    return ReplyClassification("unknown", "no_rule_matched", None)


def _find_first_match(text: str, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate and candidate in text:
            return candidate
    return None


class BotFlowRunner:
    def __init__(self, client_or_transport: TelegramClientLike | FlowTransportLike, *, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("tg-checkin.flow")
        if hasattr(client_or_transport, "latest_message_id") and hasattr(client_or_transport, "wait_for_reply"):
            self.transport: FlowTransportLike = cast(FlowTransportLike, client_or_transport)
        else:
            self.transport = FlowTransport(cast(TelegramClientLike, client_or_transport))

    async def run(self, job: JobConfig, entity) -> FlowResult:
        if not job.flow:
            raise FlowExecutionError(f"{job.name}: empty flow")
        if job.flow.repeat.count <= 0:
            self.logger.info("skipping noop flow job=%s (repeat.count=0)", job.name)
            return FlowResult(job.name, (), status="DONE_COUNT_REACHED", round=0, reason="count_is_zero")
        self.logger.info(
            "starting flow job=%s chat_id=%s steps=%s count=%s",
            job.name,
            job.chat_id,
            len(job.flow),
            job.flow.repeat.count,
        )
        started_at = asyncio.get_running_loop().time()
        results: list[FlowStepResult] = []
        unknown_replies = 0
        success_count = 0

        for round_no in range(1, job.flow.repeat.count + 1):
            if self._runtime_exceeded(job, started_at):
                return FlowResult(job.name, tuple(results), status="FAILED_TIMEOUT", round=round_no, reason="max_runtime_exceeded")

            round_result = await self._run_round(job, entity, round_no)
            results.extend(round_result.steps)

            if round_result.status == "STOPPED_ABORT_TEXT":
                return FlowResult(
                    job.name,
                    tuple(results),
                    status="STOPPED_ABORT_TEXT",
                    round=round_no,
                    matched_text=round_result.matched_text,
                    reason=round_result.reason,
                )

            if round_result.status == "DONE_SUCCESS":
                success_count += 1
                if job.flow.repeat.stop_on_success:
                    return FlowResult(job.name, tuple(results), status="DONE_SUCCESS", round=round_no, reason="success")
                if job.flow.repeat.success_quota and success_count >= job.flow.repeat.success_quota:
                    return FlowResult(job.name, tuple(results), status="DONE_SUCCESS", round=round_no, reason="success_quota_reached")

            if round_result.status == "STOPPED_UNKNOWN_REPLY":
                unknown_replies += 1
                if job.flow.rules.unknown_policy == "abort" or unknown_replies >= job.flow.rules.max_unknown_replies:
                    return FlowResult(
                        job.name,
                        tuple(results),
                        status="STOPPED_UNKNOWN_REPLY",
                        round=round_no,
                        reason="unknown_reply_limit_reached",
                    )

            if round_no < job.flow.repeat.count:
                await self._sleep_between_rounds(job)

        return FlowResult(job.name, tuple(results), status="DONE_COUNT_REACHED", round=job.flow.repeat.count, reason="count_reached")

    async def _run_round(self, job: JobConfig, entity, round_no: int) -> FlowResult:
        last_id = await self.transport.latest_message_id(entity)
        results: list[FlowStepResult] = []
        previous_reply: TelegramMessage | None = None
        for index, step in enumerate(job.flow, start=1):
            reply = await self._run_step(job, entity, index, step, after_id=last_id, round_no=round_no, previous_reply=previous_reply)
            previous_reply = reply
            last_id = int(reply.id)
            text = render_reply_text(reply)
            classification = classify_reply(text, job.flow.rules)
            result = FlowStepResult(index=index, sent=step.send, reply_id=last_id, reply_text=text, classification=classification, round=round_no)
            results.append(result)

            if classification.status == "abort":
                self.logger.info("flow abort job=%s round=%s step=%s matched=%r", job.name, round_no, index, classification.matched_text)
                return FlowResult(job.name, tuple(results), status="STOPPED_ABORT_TEXT", round=round_no, matched_text=classification.matched_text, reason=classification.reason)
            if classification.status == "success":
                return FlowResult(job.name, tuple(results), status="DONE_SUCCESS", round=round_no, matched_text=classification.matched_text, reason=classification.reason)
            if classification.status == "retry":
                return FlowResult(job.name, tuple(results), status="DONE_COUNT_REACHED", round=round_no, matched_text=classification.matched_text, reason=classification.reason)

            if step.expect_any and not any(expected in text for expected in step.expect_any):
                raise FlowExecutionError(
                    f"{job.name}: flow round {round_no} step {index} unexpected reply; expected one of {step.expect_any!r}, got {text[:200]!r}"
                )

            if step.delay_seconds > 0:
                await asyncio.sleep(step.delay_seconds)

        if job.flow.rules.has_explicit_rules:
            return FlowResult(job.name, tuple(results), status="STOPPED_UNKNOWN_REPLY", round=round_no, reason="no_rule_matched")
        return FlowResult(job.name, tuple(results), status="DONE_SUCCESS", round=round_no, reason="steps_completed")

    async def _run_step(
        self,
        job: JobConfig,
        entity,
        index: int,
        step: FlowStep,
        *,
        after_id: int,
        round_no: int,
        previous_reply: TelegramMessage | None,
    ) -> TelegramMessage:
        send_text = self._resolve_send_text(step, previous_reply)
        entities = command_entities(send_text, job.parse_bot_command) if send_text else None
        self.logger.info(
            "flow step job=%s round=%s step=%s/%s action=%s send=%r expect_any=%s",
            job.name,
            round_no,
            index,
            len(job.flow),
            step.action,
            send_text,
            step.expect_any,
        )
        if send_text:
            await self.transport.send_message(entity, send_text, formatting_entities=entities)
        reply = await self.transport.wait_for_reply(entity, after_id=after_id, timeout=step.timeout_seconds)
        if reply is None:
            raise FlowExecutionError(f"{job.name}: flow round {round_no} step {index} timed out waiting for reply after {send_text!r}")
        text = render_reply_text(reply)
        self.logger.info("flow reply job=%s round=%s step=%s id=%s text=%r", job.name, round_no, index, reply.id, text[:300])
        return reply

    def _resolve_send_text(self, step: FlowStep, previous_reply: TelegramMessage | None) -> str:
        if step.action != "click" or not step.button:
            return step.send
        button_text = find_button_text(previous_reply, step.button)
        return button_text or step.button

    def _runtime_exceeded(self, job: JobConfig, started_at: float) -> bool:
        limit = job.flow.repeat.max_runtime_seconds
        return limit is not None and (asyncio.get_running_loop().time() - started_at) >= limit

    async def _sleep_between_rounds(self, job: JobConfig) -> None:
        repeat = job.flow.repeat
        delay = repeat.interval_seconds
        if repeat.jitter_seconds > 0:
            delay += random.uniform(0, repeat.jitter_seconds)
        if delay > 0:
            await asyncio.sleep(delay)


def render_reply_text(message: TelegramMessage) -> str:
    parts = [message.raw_text or ""]
    parts.extend(iter_button_texts(message))
    return "\n".join(part for part in parts if part)


def find_button_text(message: TelegramMessage | None, needle: str) -> str | None:
    if not message or not needle:
        return None
    for text in iter_button_texts(message):
        if needle in text:
            return text
    return None


def iter_button_texts(message: TelegramMessage) -> tuple[str, ...]:
    if not message.buttons:
        return ()
    texts: list[str] = []
    for row in message.buttons:
        for button in row:
            text = str(getattr(button, "text", ""))
            if text:
                texts.append(text)
    return tuple(texts)
