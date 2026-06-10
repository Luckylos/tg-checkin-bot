from __future__ import annotations

from typing import Any

from .models import FlowSpec, FlowStep, MatchRules, RepeatPolicy


def parse_flow(raw: Any, *, label: str = "flow") -> FlowSpec:
    if raw in (None, ""):
        return FlowSpec()
    if isinstance(raw, dict):
        return _parse_structured_flow(raw, label=label)
    raise ValueError(f"{label} must be a mapping")


def _parse_structured_flow(raw: dict[str, Any], *, label: str) -> FlowSpec:
    mode = str(raw.get("mode") or "auto").strip().lower()
    if mode not in {"auto", "manual"}:
        raise ValueError(f"{label}: mode must be auto or manual")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError(f"{label}.steps must be a non-empty list")
    return FlowSpec(
        steps=tuple(_parse_structured_step(item, label=f"{label}.steps[{idx}]") for idx, item in enumerate(steps_raw, start=1)),
        repeat=_parse_repeat(raw.get("repeat") or {}, label=f"{label}.repeat"),
        rules=_parse_rules(raw.get("rules") or {}, label=f"{label}.rules"),
        mode=mode,
    )


def _parse_repeat(raw: Any, *, label: str) -> RepeatPolicy:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a mapping")
    count = int(raw.get("count", 1))
    if count <= 0:
        raise ValueError(f"{label}.count must be > 0")
    interval_seconds = float(raw.get("interval_seconds", raw.get("interval", 0)))
    jitter_seconds = float(raw.get("jitter_seconds", raw.get("jitter", 0)))
    if interval_seconds < 0:
        raise ValueError(f"{label}.interval_seconds must be >= 0")
    if jitter_seconds < 0:
        raise ValueError(f"{label}.jitter_seconds must be >= 0")
    success_quota_raw = raw.get("success_quota")
    success_quota = int(success_quota_raw) if success_quota_raw not in (None, "") else None
    if success_quota is not None and success_quota <= 0:
        raise ValueError(f"{label}.success_quota must be > 0")
    max_runtime_raw = raw.get("max_runtime_seconds")
    max_runtime_seconds = float(max_runtime_raw) if max_runtime_raw not in (None, "") else None
    if max_runtime_seconds is not None and max_runtime_seconds <= 0:
        raise ValueError(f"{label}.max_runtime_seconds must be > 0")
    return RepeatPolicy(
        count=count,
        interval_seconds=interval_seconds,
        jitter_seconds=jitter_seconds,
        stop_on_success=bool(raw.get("stop_on_success", True)),
        success_quota=success_quota,
        max_runtime_seconds=max_runtime_seconds,
    )


def _parse_rules(raw: Any, *, label: str) -> MatchRules:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a mapping")
    unknown_policy = str(raw.get("unknown_policy") or "abort").strip().lower()
    if unknown_policy not in {"retry", "abort"}:
        raise ValueError(f"{label}.unknown_policy must be retry or abort")
    max_unknown = int(raw.get("max_unknown_replies", 1))
    if max_unknown <= 0:
        raise ValueError(f"{label}.max_unknown_replies must be > 0")
    return MatchRules(
        abort_on_text=_parse_text_list(raw.get("abort_on_text"), label=f"{label}.abort_on_text"),
        success_on_text=_parse_text_list(raw.get("success_on_text"), label=f"{label}.success_on_text"),
        retry_on_text=_parse_text_list(raw.get("retry_on_text"), label=f"{label}.retry_on_text"),
        unknown_policy=unknown_policy,  # type: ignore[arg-type]
        max_unknown_replies=max_unknown,
    )


def _parse_structured_step(item: Any, *, label: str) -> FlowStep:
    if not isinstance(item, dict):
        raise ValueError(f"{label} must be a mapping")
    action = str(item.get("action") or "send").strip().lower()
    if action not in {"send", "click", "wait"}:
        raise ValueError(f"{label}: action must be send, click, or wait")
    text = str(item.get("text", item.get("send", "")) or "")
    button = str(item.get("button") or "")
    if action == "send" and not text:
        raise ValueError(f"{label}: send step requires text")
    if action == "click" and not button:
        raise ValueError(f"{label}: click step requires button")
    timeout_seconds = float(item.get("timeout_seconds", item.get("timeout", 20)))
    delay_seconds = float(item.get("delay_seconds", item.get("delay", 0)))
    if timeout_seconds <= 0:
        raise ValueError(f"{label}: timeout_seconds must be > 0")
    if delay_seconds < 0:
        raise ValueError(f"{label}: delay_seconds must be >= 0")
    expectation_item = dict(item)
    expectation_item["_allow_expect_mapping"] = True
    return FlowStep(
        action=action,  # type: ignore[arg-type]
        text=text,
        button=button,
        expect_any=_parse_expectations(expectation_item, label=label),
        timeout_seconds=timeout_seconds,
        delay_seconds=delay_seconds,
    )


def _parse_expectations(item: dict[str, Any], *, label: str) -> tuple[str, ...]:
    expects: list[str] = []
    if item.get("expect") not in (None, ""):
        expects.append(str(item["expect"]))
    if "expect_any" in item:
        raw_any = item["expect_any"]
        if isinstance(raw_any, str):
            expects.append(raw_any)
        elif isinstance(raw_any, list):
            expects.extend(str(value) for value in raw_any if str(value))
        elif isinstance(raw_any, dict):
            if not bool(item.get("_allow_expect_mapping", False)):
                raise ValueError(f"{label}: expect_any must be string or list")
            allowed_keys = {"text", "buttons"}
            unknown_keys = sorted(set(raw_any) - allowed_keys)
            if unknown_keys:
                raise ValueError(f"{label}.expect_any.{unknown_keys[0]} must be string or list")
            for key in ("text", "buttons"):
                expects.extend(_parse_text_list(raw_any.get(key), label=f"{label}.expect_any.{key}"))
        else:
            raise ValueError(f"{label}: expect_any must be string or list")
    return tuple(expects)


def _parse_text_list(raw: Any, *, label: str) -> tuple[str, ...]:
    if raw in (None, ""):
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(value) for value in raw if str(value))
    raise ValueError(f"{label} must be string or list")
