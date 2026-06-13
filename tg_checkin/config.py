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
    return data


def save_config(path: str, config: dict[str, Any]) -> None:
    target = Path(path)
    tmp = target.with_name(f".{target.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def parse_jobs(config: dict[str, Any]) -> list[JobConfig]:
    defaults = _parse_defaults(config)
    accounts = _account_items(config)

    jobs: list[JobConfig] = []
    seen_names: set[str] = set()
    seen_accounts: set[str] = set()
    for account_idx, account in enumerate(accounts, start=1):
        account_name = normalize_account_name(account.get("name") or f"account-{account_idx}")
        if account_name in seen_accounts:
            raise ValueError(f"duplicate account name: {account_name}")
        seen_accounts.add(account_name)
        if not bool(account.get("enabled", True)):
            continue
        groups = account.get("groups", [])
        if not isinstance(groups, list):
            raise ValueError(f"{account_name}: groups must be a list")
        for group_idx, group in enumerate(groups, start=1):
            for job in _parse_group_jobs(
                account_name=account_name,
                idx=group_idx,
                item=group,
                default_delay=defaults["delay_seconds"],
                default_cron=defaults["cron"],
                default_stagger=defaults["stagger_seconds"],
                default_stagger_mode=defaults["stagger_mode"],
            ):
                if job.name in seen_names:
                    raise ValueError(f"duplicate job name: {job.name}")
                seen_names.add(job.name)
                jobs.append(job)
    return jobs


def parse_accounts(config: dict[str, Any], *, require_secrets: bool = True) -> list[AccountSettings]:
    parsed: list[AccountSettings] = []
    seen: set[str] = set()
    for idx, item in enumerate(_account_items(config), start=1):
        account_name = normalize_account_name(item.get("name") or f"account-{idx}")
        if account_name in seen:
            raise ValueError(f"duplicate account name: {account_name}")
        seen.add(account_name)
        enabled = bool(item.get("enabled", True))
        if not enabled:
            continue
        api_id = _account_int(item, "api_id", require=require_secrets)
        api_hash = _account_str(item, "api_hash", require=require_secrets)
        session = _account_str(item, "session_string", require=require_secrets)
        if api_id is None or api_hash is None or session is None:
            continue
        parsed.append(AccountSettings(name=account_name, api_id=api_id, api_hash=api_hash, session_string=session, enabled=True))
    if require_secrets and not parsed:
        raise RuntimeError("no enabled Telegram accounts configured")
    return parsed


def load_settings_from_env() -> AppSettings:
    proxy_type = os.getenv("TELEGRAM_PROXY_TYPE")
    proxy_host = os.getenv("TELEGRAM_PROXY_HOST")
    proxy_port_raw = os.getenv("TELEGRAM_PROXY_PORT")
    return AppSettings(
        config_path=os.getenv("CONFIG_PATH", "/config/config.yml"),
        reload_seconds=int(os.getenv("CONFIG_RELOAD_SECONDS", "60")),
        control_enabled=os.getenv("CONTROL_BOT_ENABLED", "true").lower() not in {"0", "false", "no"},
        telegram_proxy_type=proxy_type.strip().lower() if proxy_type else None,
        telegram_proxy_host=proxy_host.strip() if proxy_host else None,
        telegram_proxy_port=int(proxy_port_raw) if proxy_port_raw else None,
    )


def normalize_account_name(value: Any) -> str:
    name = str(value).strip()
    if not name:
        raise ValueError("account name must not be empty")
    if "/" in name:
        raise ValueError("account name must not contain '/'")
    return name


def _account_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = config.get("accounts")
    if accounts is None:
        raise ValueError("config accounts must be a list")
    if not isinstance(accounts, list):
        raise ValueError("config accounts must be a list")
    if not accounts:
        raise ValueError("config accounts must not be empty")
    for idx, account in enumerate(accounts, start=1):
        if not isinstance(account, dict):
            raise ValueError(f"accounts[{idx}] must be a mapping")
    return accounts


def _parse_defaults(config: dict[str, Any]) -> dict[str, Any]:
    default_delay = float(config.get("default_delay_seconds", 3))
    default_cron = str(config.get("default_cron") or DEFAULT_CRON).strip() or DEFAULT_CRON
    default_stagger = int(config.get("default_stagger_seconds", DEFAULT_STAGGER_SECONDS))
    default_stagger_mode = str(config.get("default_stagger_mode") or "stable").strip().lower()
    if default_stagger < 0:
        raise ValueError("default_stagger_seconds must be >= 0")
    if default_stagger_mode not in STAGGER_MODES:
        raise ValueError("default_stagger_mode must be stable, random, or off")
    return {
        "delay_seconds": default_delay,
        "cron": default_cron,
        "stagger_seconds": default_stagger,
        "stagger_mode": default_stagger_mode,
    }


def _parse_group_jobs(
    *,
    account_name: str,
    idx: int,
    item: Any,
    default_delay: float,
    default_cron: str,
    default_stagger: int,
    default_stagger_mode: str,
) -> list[JobConfig]:
    if not isinstance(item, dict):
        raise ValueError(f"{account_name}.groups[{idx}] must be a mapping")
    group_name = str(item.get("name") or f"group-{idx}")
    chat_value = item.get("chat_id", item.get("chat"))
    if chat_value is None:
        raise ValueError(f"{account_name}/{group_name}: missing chat_id")
    chat_id = normalize_chat_id(chat_value)
    job_prefix = f"{account_name}/{group_name}"

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
        account_name=account_name,
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


def env_int(name: str, *, required: bool = True) -> int | None:
    raw = os.getenv(name)
    if raw in (None, ""):
        if required:
            raise RuntimeError(f"missing required env: {name}")
        return None
    return int(raw)


def env_str(name: str, *, required: bool = True) -> str | None:
    raw = os.getenv(name)
    if raw in (None, ""):
        if required:
            raise RuntimeError(f"missing required env: {name}")
        return None
    return raw


def _account_env_name(item: dict[str, Any], field: str) -> str:
    explicit = item.get(f"{field}_env")
    if explicit:
        return str(explicit)
    prefix = item.get("env_prefix")
    if prefix:
        return f"{str(prefix).strip().upper()}_{field.upper()}"
    return f"{normalize_account_name(item.get('name')).upper()}_{field.upper()}"


def _account_str(item: dict[str, Any], field: str, *, require: bool) -> str | None:
    raw = item.get(field)
    if raw not in (None, ""):
        return str(raw)
    return env_str(_account_env_name(item, field), required=require)


def _account_int(item: dict[str, Any], field: str, *, require: bool) -> int | None:
    raw = item.get(field)
    if raw not in (None, ""):
        return int(raw)
    return env_int(_account_env_name(item, field), required=require)
