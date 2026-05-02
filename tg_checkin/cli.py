from __future__ import annotations

import asyncio
import logging
import sys

from .app import CheckinApp
from .config import load_config, load_settings_from_env, parse_jobs
from .scheduler import cron_trigger
from .telegram import command_entities


def setup_logging() -> None:
    level = __import__("os").getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def auth() -> None:
    raise SystemExit("auth command is disabled. Generate a Telethon StringSession locally and set TG_SESSION_STRING in .env")


async def validate() -> None:
    setup_logging()
    config_path = __import__("os").getenv("CONFIG_PATH", "/config/config.yml")
    if len(sys.argv) >= 3:
        config_path = sys.argv[2]
    config = load_config(config_path)
    timezone = str(config.get("timezone") or "Asia/Shanghai")
    jobs = parse_jobs(config)
    for job in jobs:
        cron_trigger(job.cron, timezone)
        command_entities(job.message, job.parse_bot_command)
    print(f"OK: {len(jobs)} jobs, timezone={timezone}")


def main() -> None:
    setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        asyncio.run(CheckinApp(load_settings_from_env()).run())
    elif cmd == "auth":
        asyncio.run(auth())
    elif cmd == "validate":
        asyncio.run(validate())
    else:
        raise SystemExit(f"unknown command: {cmd}")
