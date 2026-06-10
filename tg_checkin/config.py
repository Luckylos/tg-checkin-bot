from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .flow_config import parse_flow
from .models import (
    DEFAULT_ACCOUNT_NAME,
    DEFAULT_CRON,
    DEFAULT_STAGGER_SECONDS,
    STAGGER_MODES,
    AccountSettings,
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

    jobs: list[JobConfig] = []
    seen_names: set[str] = set()

    accounts = config.get("accounts")
    if accounts is None:
        jobs.extend(
            _parse_group_collection(
                groups=config.get("groups", []),
                account_name=DEFAULT_ACCOUNT_NAME,
                prefix_names=False,
                default_delay=default_delay,
                default_cron=default_cron,
                default_stagger=default_stagger,
                default_stagger_mode=default_stagger_mode,
            )
        )
    else:
        if not isinstance(accounts, list):
            raise ValueError("config accounts must be a list")
        if config.get("groups"):
            jobs.extend(
                _parse_group_collection(
                    groups=config.get("groups", []),
                    account_name=DEFAULT_ACCOUNT_NAME,
                    prefix_names=False,
                    default_delay=default_delay,
                    default_cron=default_cron,
                    default_stagger=default_stagger,
                    default_stagger_mode=default_stagger_mode,
                )
            )
        for account_idx, account in enumerate(accounts, start=1):
            if not isinstance(account, dict):
                raise ValueError(f"accounts[{account_idx}] must be a mapping")
            account_name = normalize_account_name(account.get("name") or f"account-{account_idx}")
            if not bool(account.get("enabled", True)):
                continue
            jobs.extend(
                _parse_group_collection(
                    groups=account.get("groups", []),
                    account_name=account_name,
                    prefix_names=True,
                    default_delay=default_delay,
                    default_cron=default_cron,
                    default_stagger=default_stagger,
                    default_stagger_mode=default_stagger_mode,
                )
            )

    for job in jobs:
        if job.name in seen_names:
            raise ValueError(f"duplicate job name: {job.name}")
        seen_names.add(job.name)
    return jobs


def _parse_group_collection(
    *,
    groups: Any,
    account_name: str,
    prefix_names: bool,
    default_delay: float,
    default_cron: str,
    default_stagger: int,
    default_stagger_mode: str,
) -> list[JobConfig]:
    if not isinstance(groups, list):
        raise ValueError(f"{account_name}: groups must be a list")
    jobs: list[JobConfig] = []
    for idx, item in enumerate(groups, start=1):
        jobs.extend(
            _parse_group_jobs(
                idx=idx,
                item=item,
                account_name=account_name,
                prefix_names=prefix_names,
                default_delay=default_delay,
                default_cron=default_cron,
                default_stagger=default_stagger,
                default_stagger_mode=default_stagger_mode,
            )
        )
    return jobs


def _parse_group_jobs(
    *,
    idx: int,
    item: Any,
    account_name: str,
    prefix_names: bool,
    default_delay: float,
    default_cron: str,
    default_stagger: int,
    default_stagger_mode: str,
) -> list[JobConfig]:
    if not isinstance(item, dict):
        raise ValueError(f"{account_name}.groups[{idx}] must be a mapping")
    group_name = str(item.get("name") or f"job-{idx}")
    chat_value = item.get("chat_id", item.get("chat"))
    if chat_value is None:
        raise ValueError(f"{account_name}/{group_name}: missing chat_id")
    chat_id = normalize_chat_id(chat_value)
    job_prefix = f"{account_name}/{group_name}" if prefix_names else group_name

    tasks = item.get("tasks")
    if tasks is None:
        if "message" not in item and "flow" not in item:
            raise ValueError(f"{account_name}/{group_name}: missing message or flow")
        return [
            _build_job(
                source=item,
                name=job_prefix,
                account_name=account_name,
                chat_id=chat_id,
                group_enabled=bool(item.get("enabled", True)),
                default_delay=default_delay,
                default_cron=default_cron,
                default_stagger=default_stagger,
                default_stagger_mode=default_stagger_mode,
            )
        ]

    if not isinstance(tasks, list):
        raise ValueError(f"{account_name}/{group_name}: tasks must be a list")
    if not tasks:
        raise ValueError(f"{account_name}/{group_name}: tasks must not be empty")

    group_enabled = bool(item.get("enabled", True))
    jobs: list[JobConfig] = []
    for task_idx, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f"{account_name}/{group_name}.tasks[{task_idx}] must be a mapping")
        task_name = str(task.get("name") or f"task-{task_idx}")
        source = _merge_task_defaults(item, task)
        if "message" not in source and "flow" not in source:
            raise ValueError(f"{account_name}/{group_name}/{task_name}: missing message or flow")
        jobs.append(
            _build_job(
                source=source,
                name=f"{job_prefix}/{task_name}",
                account_name=account_name,
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
    account_name: str,
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
        account_name=account_name,
    )


def env_int(name: str, *, required: bool = True, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw in (None, ""):
        if required:
            raise RuntimeError(f"missing required env: {name}")
        return default
    return int(raw)


def env_str(name: str, *, required: bool = True, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw in (None, ""):
        if required:
            raise RuntimeError(f"missing required env: {name}")
        return default
    return raw


def load_settings_from_env() -> AppSettings:
    return AppSettings(
        api_id=env_int("TG_API_ID", required=False),
        api_hash=env_str("TG_API_HASH", required=False),
        session_string=env_str("TG_SESSION_STRING", required=False),
        config_path=os.getenv("CONFIG_PATH", "/config/config.yml"),
        reload_seconds=int(os.getenv("CONFIG_RELOAD_SECONDS", "60")),
        control_enabled=os.getenv("CONTROL_BOT_ENABLED", "true").lower() not in {"0", "false", "no"},
    )


def parse_accounts(config: dict[str, Any], settings: AppSettings, *, require_secrets: bool = True) -> list[AccountSettings]:
    accounts = config.get("accounts")
    if accounts is None:
        if settings.api_id is None or not settings.api_hash or not settings.session_string:
            if require_secrets:
                raise RuntimeError("missing TG_API_ID/TG_API_HASH/TG_SESSION_STRING for legacy single-account mode")
            return []
        return [
            AccountSettings(
                name=DEFAULT_ACCOUNT_NAME,
                api_id=settings.api_id,
                api_hash=settings.api_hash,
                session_string=settings.session_string,
                enabled=True,
            )
        ]

    if not isinstance(accounts, list):
        raise ValueError("config accounts must be a list")
    parsed: list[AccountSettings] = []
    seen: set[str] = set()
    if config.get("groups"):
        if settings.api_id is None or not settings.api_hash or not settings.session_string:
            if require_secrets:
                raise RuntimeError("top-level groups require TG_API_ID/TG_API_HASH/TG_SESSION_STRING")
        else:
            parsed.append(
                AccountSettings(
                    name=DEFAULT_ACCOUNT_NAME,
                    api_id=settings.api_id,
                    api_hash=settings.api_hash,
                    session_string=settings.session_string,
                    enabled=True,
                )
            )
            seen.add(DEFAULT_ACCOUNT_NAME)
    for idx, item in enumerate(accounts, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"accounts[{idx}] must be a mapping")
        name = normalize_account_name(item.get("name") or f"account-{idx}")
        if name in seen:
            raise ValueError(f"duplicate account name: {name}")
        seen.add(name)
        enabled = bool(item.get("enabled", True))
        if not enabled:
            continue
        api_id = _account_int(item, "api_id", default_env="TG_API_ID", require=require_secrets)
        api_hash = _account_str(item, "api_hash", default_env="TG_API_HASH", require=require_secrets)
        session = _account_str(item, "session_string", default_env="TG_SESSION_STRING", require=require_secrets)
        if api_id is None or api_hash is None or session is None:
            continue
        parsed.append(AccountSettings(name=name, api_id=api_id, api_hash=api_hash, session_string=session, enabled=enabled))
    if require_secrets and not parsed:
        raise RuntimeError("no enabled Telegram accounts configured")
    return parsed


def normalize_account_name(value: Any) -> str:
    name = str(value).strip()
    if not name:
        raise ValueError("account name must not be empty")
    if "/" in name:
        raise ValueError("account name must not contain '/'")
    return name


def _account_env_name(item: dict[str, Any], field: str, default_env: str) -> str | None:
    env_key = item.get(f"{field}_env")
    if env_key:
        return str(env_key)
    prefix = item.get("env_prefix")
    if prefix:
        return f"{str(prefix).strip().upper()}_{field.upper()}"
    return default_env if item.get(field) in (None, "") else None


def _account_str(item: dict[str, Any], field: str, *, default_env: str, require: bool) -> str | None:
    raw = item.get(field)
    if raw not in (None, ""):
        return str(raw)
    env_name = _account_env_name(item, field, default_env)
    if not env_name:
        if require:
            raise RuntimeError(f"{item.get('name')}: missing {field}")
        return None
    return env_str(env_name, required=require)


def _account_int(item: dict[str, Any], field: str, *, default_env: str, require: bool) -> int | None:
    raw = item.get(field)
    if raw not in (None, ""):
        return int(raw)
    env_name = _account_env_name(item, field, default_env)
    if not env_name:
        if require:
            raise RuntimeError(f"{item.get('name')}: missing {field}")
        return None
    return env_int(env_name, required=require)
