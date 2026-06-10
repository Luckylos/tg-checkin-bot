from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

DEFAULT_CRON = "0 10 0 * * *"  # daily 00:10:00
DEFAULT_STAGGER_SECONDS = 1800
STAGGER_MODES = {"stable", "random", "off"}

FlowAction = Literal["send", "click", "wait"]
UnknownPolicy = Literal["retry", "abort"]


@dataclass(frozen=True)
class RepeatPolicy:
    count: int = 1
    interval_seconds: float = 0.0
    jitter_seconds: float = 0.0
    stop_on_success: bool = True
    success_quota: int | None = None
    max_runtime_seconds: float | None = None


@dataclass(frozen=True)
class MatchRules:
    abort_on_text: tuple[str, ...] = field(default_factory=tuple)
    success_on_text: tuple[str, ...] = field(default_factory=tuple)
    retry_on_text: tuple[str, ...] = field(default_factory=tuple)
    unknown_policy: UnknownPolicy = "abort"
    max_unknown_replies: int = 1

    @property
    def has_explicit_rules(self) -> bool:
        return bool(self.abort_on_text or self.success_on_text or self.retry_on_text)


@dataclass(frozen=True)
class FlowStep:
    action: FlowAction = "send"
    text: str = ""
    button: str = ""
    expect_any: tuple[str, ...] = field(default_factory=tuple)
    timeout_seconds: float = 20.0
    delay_seconds: float = 0.0

    @property
    def send(self) -> str:
        if self.action == "click":
            return self.button
        return self.text


@dataclass(frozen=True)
class FlowSpec:
    steps: tuple[FlowStep, ...] = field(default_factory=tuple)
    repeat: RepeatPolicy = field(default_factory=RepeatPolicy)
    rules: MatchRules = field(default_factory=MatchRules)
    mode: str = "auto"

    def __bool__(self) -> bool:
        return bool(self.steps)

    def __iter__(self) -> Iterator[FlowStep]:
        return iter(self.steps)

    def __len__(self) -> int:
        return len(self.steps)

    def __getitem__(self, index: int) -> FlowStep:
        return self.steps[index]

    def __eq__(self, other: object) -> bool:
        if other == ():
            return self.steps == ()
        return super().__eq__(other)


@dataclass(frozen=True)
class JobConfig:
    name: str
    account_name: str
    enabled: bool
    chat_id: int | str
    task_type: str
    message: str
    parse_bot_command: bool
    cron: str
    delay_seconds: float
    run_on_start: bool
    stagger_seconds: int
    stagger_mode: str
    flow: FlowSpec = field(default_factory=FlowSpec)


@dataclass(frozen=True)
class AppSettings:
    config_path: str = "/config/config.yml"
    reload_seconds: int = 60
    control_enabled: bool = True


@dataclass(frozen=True)
class AccountSettings:
    name: str
    api_id: int
    api_hash: str
    session_string: str
    enabled: bool = True


def normalize_chat_id(value: Any) -> int | str:
    if isinstance(value, bool):
        raise ValueError("chat_id must be an integer or Telegram username")
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
