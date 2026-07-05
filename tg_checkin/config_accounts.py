from __future__ import annotations

import os
from typing import Any

from .models import AccountSettings


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
