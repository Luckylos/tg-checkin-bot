from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_CRON = "0 10 0 * * *"  # daily 00:10:00
DEFAULT_STAGGER_SECONDS = 1800
STAGGER_MODES = {"stable", "random", "off"}


@dataclass(frozen=True)
class JobConfig:
    name: str
    enabled: bool
    chat_id: int
    message: str
    parse_bot_command: bool
    cron: str
    delay_seconds: float
    run_on_start: bool
    stagger_seconds: int
    stagger_mode: str


@dataclass(frozen=True)
class AppSettings:
    api_id: int
    api_hash: str
    session_string: str
    config_path: str = "/config/config.yml"
    reload_seconds: int = 60
    control_enabled: bool = True


def normalize_chat_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("chat_id must be an integer")
    if isinstance(value, int):
        return value
    raw = str(value).strip()
    if not raw or not raw.lstrip("-").isdigit():
        raise ValueError(f"chat_id must be numeric, got: {value!r}")
    return int(raw)
