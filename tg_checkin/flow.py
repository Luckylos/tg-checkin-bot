from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from .models import FlowStep, JobConfig
from .telegram import command_entities


class TelegramMessage(Protocol):
    id: int
    out: bool
    raw_text: str | None
    buttons: object | None


class TelegramClientLike(Protocol):
    async def send_message(self, entity, message: str, *, formatting_entities=None): ...
    async def get_messages(self, entity, *, limit: int): ...
    def iter_messages(self, entity, *, limit: int): ...


@dataclass(frozen=True)
class FlowStepResult:
    index: int
    sent: str
    reply_id: int
    reply_text: str


@dataclass(frozen=True)
class FlowResult:
    job_name: str
    steps: tuple[FlowStepResult, ...]


class FlowExecutionError(RuntimeError):
    """Raised when a configured bot flow cannot safely continue."""


class BotFlowRunner:
    def __init__(self, client: TelegramClientLike, *, logger: logging.Logger | None = None) -> None:
        self.client = client
        self.logger = logger or logging.getLogger("tg-checkin.flow")

    async def run(self, job: JobConfig, entity) -> FlowResult:
        if not job.flow:
            raise FlowExecutionError(f"{job.name}: empty flow")
        self.logger.info("starting flow job=%s chat_id=%s steps=%s", job.name, job.chat_id, len(job.flow))
        last_id = await self._latest_message_id(entity)
        results: list[FlowStepResult] = []
        for index, step in enumerate(job.flow, start=1):
            reply = await self._run_step(job, entity, index, step, after_id=last_id)
            last_id = int(reply.id)
            text = render_reply_text(reply)
            results.append(FlowStepResult(index=index, sent=step.send, reply_id=last_id, reply_text=text))
            if step.delay_seconds > 0:
                await asyncio.sleep(step.delay_seconds)
        return FlowResult(job_name=job.name, steps=tuple(results))

    async def _run_step(self, job: JobConfig, entity, index: int, step: FlowStep, *, after_id: int) -> TelegramMessage:
        entities = command_entities(step.send, job.parse_bot_command)
        self.logger.info(
            "flow step job=%s step=%s/%s send=%r expect_any=%s",
            job.name,
            index,
            len(job.flow),
            step.send,
            step.expect_any,
        )
        await self.client.send_message(entity, step.send, formatting_entities=entities)
        reply = await self._wait_for_reply(entity, after_id=after_id, timeout=step.timeout_seconds)
        if reply is None:
            raise FlowExecutionError(f"{job.name}: flow step {index} timed out waiting for reply after {step.send!r}")
        text = render_reply_text(reply)
        self.logger.info("flow reply job=%s step=%s id=%s text=%r", job.name, index, reply.id, text[:300])
        if step.expect_any and not any(expected in text for expected in step.expect_any):
            raise FlowExecutionError(
                f"{job.name}: flow step {index} unexpected reply; expected one of {step.expect_any!r}, got {text[:200]!r}"
            )
        return reply

    async def _latest_message_id(self, entity) -> int:
        messages = await self.client.get_messages(entity, limit=1)
        if not messages:
            return 0
        return int(messages[0].id)

    async def _wait_for_reply(self, entity, *, after_id: int, timeout: float):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            async for message in self.client.iter_messages(entity, limit=10):
                if message.id > after_id and not message.out:
                    return message
            await asyncio.sleep(1)
        return None


def render_reply_text(message: TelegramMessage) -> str:
    parts = [message.raw_text or ""]
    if message.buttons:
        for row in message.buttons:
            parts.extend(str(getattr(button, "text", "")) for button in row)
    return "\n".join(part for part in parts if part)
