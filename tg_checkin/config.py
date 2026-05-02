from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .models import (
    DEFAULT_CRON,
    DEFAULT_STAGGER_SECONDS,
    STAGGER_MODES,
    AppSettings,
    JobConfig,
    normalize_chat_id,
)


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")
    data.setdefault("groups", [])
    return data


def save_config(path: str, config: dict[str, Any]) -> None:
    target = Path(path)
    tmp = target.with_name(f".{target.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, target)


def parse_jobs(config: dict[str, Any]) -> list[JobConfig]:
    default_delay = float(config.get("default_delay_seconds", 3))
    default_cron = str(config.get("default_cron") or DEFAULT_CRON).strip() or DEFAULT_CRON
    default_stagger = int(config.get("default_stagger_seconds", DEFAULT_STAGGER_SECONDS))
    default_stagger_mode = str(config.get("default_stagger_mode") or "stable").strip().lower()
    if default_stagger < 0:
        raise ValueError("default_stagger_seconds must be >= 0")
    if default_stagger_mode not in STAGGER_MODES:
        raise ValueError("default_stagger_mode must be stable, random, or off")

    groups = config.get("groups", [])
    if not isinstance(groups, list):
        raise ValueError("config groups must be a list")

    jobs: list[JobConfig] = []
    for idx, item in enumerate(groups, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"groups[{idx}] must be a mapping")
        name = str(item.get("name") or f"job-{idx}")
        chat_value = item.get("chat_id", item.get("chat"))
        if chat_value is None:
            raise ValueError(f"{name}: missing chat_id")
        if "message" not in item:
            raise ValueError(f"{name}: missing message")

        raw_cron = item.get("cron")
        cron = str(raw_cron or default_cron).strip()
        uses_default_cron = raw_cron in (None, "") or cron == default_cron
        stagger_seconds = int(item.get("stagger_seconds", default_stagger if uses_default_cron else 0))
        stagger_mode = str(item.get("stagger_mode") or default_stagger_mode).strip().lower()
        if stagger_seconds < 0:
            raise ValueError(f"{name}: stagger_seconds must be >= 0")
        if stagger_mode not in STAGGER_MODES:
            raise ValueError(f"{name}: stagger_mode must be stable, random, or off")
        if stagger_mode == "off":
            stagger_seconds = 0

        jobs.append(
            JobConfig(
                name=name,
                enabled=bool(item.get("enabled", True)),
                chat_id=normalize_chat_id(chat_value),
                message=str(item["message"]),
                parse_bot_command=bool(item.get("parse_bot_command", True)),
                cron=cron,
                delay_seconds=float(item.get("delay_seconds", default_delay)),
                run_on_start=bool(item.get("run_on_start", False)),
                stagger_seconds=stagger_seconds,
                stagger_mode=stagger_mode,
            )
        )
    return jobs


def env_int(name: str, *, required: bool = True, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw in (None, ""):
        if required:
            raise RuntimeError(f"missing required env: {name}")
        return default
    return int(raw)


def load_settings_from_env() -> AppSettings:
    api_id = env_int("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    session_string = os.getenv("TG_SESSION_STRING")
    if not api_hash:
        raise RuntimeError("missing required env: TG_API_HASH")
    if not session_string:
        raise RuntimeError("missing required env: TG_SESSION_STRING")
    return AppSettings(
        api_id=api_id,  # type: ignore[arg-type]
        api_hash=api_hash,
        session_string=session_string,
        config_path=os.getenv("CONFIG_PATH", "/config/config.yml"),
        reload_seconds=int(os.getenv("CONFIG_RELOAD_SECONDS", "60")),
        control_enabled=os.getenv("CONTROL_BOT_ENABLED", "true").lower() not in {"0", "false", "no"},
    )
