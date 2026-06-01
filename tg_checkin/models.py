from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_CRON = "0 10 0 * * *"  # daily 00:10:00
DEFAULT_STAGGER_SECONDS = 1800
STAGGER_MODES = {"stable", "random", "off"}


@dataclass(frozen=True)
class FlowStep:
    send: str
    expect_any: tuple[str, ...] = field(default_factory=tuple)
    timeout_seconds: float = 20.0
    delay_seconds: float = 0.0


@dataclass(frozen=True)
class JobConfig:
    name: str
    enabled: bool
    chat_id: int | str
    message: str
    parse_bot_command: bool
    cron: str
    delay_seconds: float
    run_on_start: bool
    stagger_seconds: int
    stagger_mode: str
    flow: tuple[FlowStep, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AppSettings:
    api_id: int
    api_hash: str
    session_string: str
    config_path: str = "/config/config.yml"
    reload_seconds: int = 60
    control_enabled: bool = True


def normalize_chat_id(value: Any) -> int | str:
    if isinstance(value, bool):
        raise ValueError("chat_id must be an integer")
    if isinstance(value, int):
        return value
    raw = str(value).strip()
    if not raw:
        raise ValueError("chat_id must not be empty")
    if raw.startswith("@"):
        raw = raw[1:]
    if raw.lstrip("-").isdigit():
        return int(raw)
    if all(ch.isalnum() or ch == "_" for ch in raw):
        return raw
    raise ValueError(f"chat_id must be numeric or Telegram username, got: {value!r}")
