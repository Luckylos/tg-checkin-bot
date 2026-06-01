from __future__ import annotations

from typing import Any

from .models import FlowStep


def parse_flow(raw: Any, *, label: str = "flow") -> tuple[FlowStep, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{label} must be a non-empty list")
    return tuple(_parse_flow_step(item, label=f"{label}[{idx}]") for idx, item in enumerate(raw, start=1))


def _parse_flow_step(item: Any, *, label: str) -> FlowStep:
    if not isinstance(item, dict):
        raise ValueError(f"{label} must be a mapping")
    send = str(item.get("send") or "")
    if not send:
        raise ValueError(f"{label}: missing send")
    expect_any = _parse_expectations(item, label=label)
    timeout_seconds = float(item.get("timeout_seconds", item.get("timeout", 20)))
    delay_seconds = float(item.get("delay_seconds", item.get("delay", 0)))
    if timeout_seconds <= 0:
        raise ValueError(f"{label}: timeout_seconds must be > 0")
    if delay_seconds < 0:
        raise ValueError(f"{label}: delay_seconds must be >= 0")
    return FlowStep(send=send, expect_any=expect_any, timeout_seconds=timeout_seconds, delay_seconds=delay_seconds)


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
        else:
            raise ValueError(f"{label}: expect_any must be string or list")
    return tuple(expects)
