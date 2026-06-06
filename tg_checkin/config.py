from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .flow_config import parse_flow
from .models import (
    DEFAULT_CRON,
    DEFAULT_STAGGER_SECONDS,
    STAGGER_MODES,
    AppSettings,
    JobConfig,
    normalize_chat_id,
)

INHERITED_TASK_FIELDS = {
    "message",
    "flow",
    "parse_bot_command",
    "cron",
    "delay_seconds",
    "run_on_start",
    "stagger_seconds",
    "stagger_mode",
}


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
    os.chmod(tmp, 0o600)
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
    seen_names: set[str] = set()
    for idx, item in enumerate(groups, start=1):
        for job in _parse_group_jobs(
            idx=idx,
            item=item,
            default_delay=default_delay,
            default_cron=default_cron,
            default_stagger=default_stagger,
            default_stagger_mode=default_stagger_mode,
        ):
            if job.name in seen_names:
                raise ValueError(f"duplicate job name: {job.name}")
            seen_names.add(job.name)
            jobs.append(job)
    return jobs


def _parse_group_jobs(
    *,
    idx: int,
    item: Any,
    default_delay: float,
    default_cron: str,
    default_stagger: int,
    default_stagger_mode: str,
) -> list[JobConfig]:
    if not isinstance(item, dict):
        raise ValueError(f"groups[{idx}] must be a mapping")
    group_name = str(item.get("name") or f"job-{idx}")
    chat_value = item.get("chat_id", item.get("chat"))
    if chat_value is None:
        raise ValueError(f"{group_name}: missing chat_id")
    chat_id = normalize_chat_id(chat_value)

    tasks = item.get("tasks")
    if tasks is None:
        if "message" not in item and "flow" not in item:
            raise ValueError(f"{group_name}: missing message or flow")
        return [
            _build_job(
                source=item,
                name=group_name,
                chat_id=chat_id,
                group_enabled=bool(item.get("enabled", True)),
                default_delay=default_delay,
                default_cron=default_cron,
                default_stagger=default_stagger,
                default_stagger_mode=default_stagger_mode,
            )
        ]

    if not isinstance(tasks, list):
        raise ValueError(f"{group_name}: tasks must be a list")
    if not tasks:
        raise ValueError(f"{group_name}: tasks must not be empty")

    group_enabled = bool(item.get("enabled", True))
    jobs: list[JobConfig] = []
    for task_idx, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f"{group_name}.tasks[{task_idx}] must be a mapping")
        task_name = str(task.get("name") or f"task-{task_idx}")
        source = _merge_task_defaults(item, task)
        if "message" not in source and "flow" not in source:
            raise ValueError(f"{group_name}/{task_name}: missing message or flow")
        jobs.append(
            _build_job(
                source=source,
                name=f"{group_name}/{task_name}",
                chat_id=chat_id,
                group_enabled=group_enabled,
                default_delay=default_delay,
                default_cron=default_cron,
                default_stagger=default_stagger,
                default_stagger_mode=default_stagger_mode,
            )
        )
    return jobs


def _merge_task_defaults(group: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    merged = {key: group[key] for key in INHERITED_TASK_FIELDS if key in group}
    merged.update(task)
    return merged


def _build_job(
    *,
    source: dict[str, Any],
    name: str,
    chat_id: int | str,
    group_enabled: bool,
    default_delay: float,
    default_cron: str,
    default_stagger: int,
    default_stagger_mode: str,
) -> JobConfig:
    raw_cron = source.get("cron")
    cron = str(raw_cron or default_cron).strip()
    uses_default_cron = raw_cron in (None, "") or cron == default_cron
    stagger_seconds = int(source.get("stagger_seconds", default_stagger if uses_default_cron else 0))
    stagger_mode = str(source.get("stagger_mode") or default_stagger_mode).strip().lower()
    if stagger_seconds < 0:
        raise ValueError(f"{name}: stagger_seconds must be >= 0")
    if stagger_mode not in STAGGER_MODES:
        raise ValueError(f"{name}: stagger_mode must be stable, random, or off")
    if stagger_mode == "off":
        stagger_seconds = 0

    flow = parse_flow(source.get("flow"), label=name + ".flow")
    message = str(source.get("message", ""))
    task_type = str(source.get("type") or ("flow" if flow else "message")).strip().lower()
    if task_type not in {"message", "flow"}:
        raise ValueError(f"{name}: type must be message or flow")
    if task_type == "message" and not message:
        raise ValueError(f"{name}: message task requires message")
    if task_type == "flow" and not flow:
        raise ValueError(f"{name}: flow task requires flow")

    return JobConfig(
        name=name,
        enabled=group_enabled and bool(source.get("enabled", True)),
        chat_id=chat_id,
        task_type=task_type,
        message=message,
        parse_bot_command=bool(source.get("parse_bot_command", True)),
        cron=cron,
        delay_seconds=float(source.get("delay_seconds", default_delay)),
        run_on_start=bool(source.get("run_on_start", False)),
        stagger_seconds=stagger_seconds,
        stagger_mode=stagger_mode,
        flow=flow,
    )


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
