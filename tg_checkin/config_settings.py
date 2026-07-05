from __future__ import annotations

import os

from .models import AppSettings


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
