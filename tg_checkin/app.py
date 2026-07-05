from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import load_config, parse_accounts, parse_jobs
from .models import AppSettings, JobConfig
from .runtime import AccountRuntime
from .scheduler import cron_trigger


class CheckinApp:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.scheduler = AsyncIOScheduler()
        self.logger = logging.getLogger("tg-checkin")
        self._config_mtime: float | None = None
        self._started_jobs: set[str] = set()
        self._stop_event = asyncio.Event()
        self.runtimes: dict[str, AccountRuntime] = {}

    async def start_clients(self) -> None:
        config = load_config(self.settings.config_path)
        accounts = parse_accounts(config)
        self.runtimes = {account.name: AccountRuntime(self, account) for account in accounts}
        for runtime in self.runtimes.values():
            await runtime.start_client()

    async def send_job(self, job: JobConfig, *, apply_stagger: bool = False) -> None:
        runtime = self.runtimes.get(job.account_name)
        if runtime is None:
            raise RuntimeError(f"job {job.name}: account runtime is not available: {job.account_name}")
        await runtime.send_job(job, apply_stagger=apply_stagger)

    async def reload_config(self) -> None:
        path = Path(self.settings.config_path)
        stat = path.stat()
        if self._config_mtime == stat.st_mtime:
            return
        config = load_config(self.settings.config_path)
        timezone = str(config.get("timezone") or "Asia/Shanghai")
        jobs = parse_jobs(config)
        missing_accounts = sorted({job.account_name for job in jobs if job.enabled and job.account_name not in self.runtimes})
        if missing_accounts:
            raise RuntimeError(f"enabled jobs reference accounts that are not connected: {', '.join(missing_accounts)}")
        self.scheduler.remove_all_jobs()
        self._config_mtime = stat.st_mtime
        enabled_count = 0
        for job in jobs:
            if not job.enabled:
                self.logger.info("skip disabled account=%s job=%s", job.account_name, job.name)
                continue
            if job.flow and job.flow.is_noop:
                self.logger.info("skip noop flow account=%s job=%s (repeat.count=0)", job.account_name, job.name)
                continue
            self.scheduler.add_job(
                self.send_job,
                trigger=cron_trigger(job.cron, timezone),
                args=[job],
                kwargs={"apply_stagger": True},
                id=job.name,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=max(60, job.stagger_seconds + 300),
            )
            enabled_count += 1
            self.logger.info(
                "scheduled account=%s job=%s cron=%s timezone=%s stagger_seconds=%s stagger_mode=%s",
                job.account_name,
                job.name,
                job.cron,
                timezone,
                job.stagger_seconds,
                job.stagger_mode,
            )
            start_key = f"{job.account_name}:{job.name}:{job.cron}:{job.chat_id}:{job.message}:{len(job.flow)}"
            if job.run_on_start and start_key not in self._started_jobs:
                self._started_jobs.add(start_key)
                asyncio.create_task(self.send_job(job))
        self.logger.info("config loaded: %s enabled jobs", enabled_count)

    async def force_reload_after_write(self) -> None:
        self._config_mtime = None
        await self.reload_config()

    async def reload_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.reload_config()
            except Exception:
                self.logger.exception("failed to reload config")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.settings.reload_seconds)
            except asyncio.TimeoutError:
                pass

    def extract_account_config(self, full: dict[str, Any], account_name: str) -> dict[str, Any]:
        accounts = full.get("accounts")
        if not isinstance(accounts, list):
            raise ValueError("config accounts must be a list")
        for account in accounts:
            if isinstance(account, dict) and str(account.get("name")) == account_name:
                sub = self._account_subconfig(full, account)
                sub["groups"] = account.get("groups", [])
                return sub
        raise ValueError(f"account not found: {account_name}")

    def replace_account_config(self, full: dict[str, Any], account_name: str, account_config: dict[str, Any]) -> dict[str, Any]:
        accounts = full.get("accounts")
        if not isinstance(accounts, list):
            raise ValueError("config accounts must be a list")
        for account in accounts:
            if isinstance(account, dict) and str(account.get("name")) == account_name:
                groups = account_config.get("groups", [])
                if not isinstance(groups, list):
                    raise ValueError("groups must be a list")
                account["groups"] = groups
                return full
        raise ValueError(f"account not found: {account_name}")

    def _account_subconfig(self, full: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
        sub = {
            key: value
            for key, value in full.items()
            if key
            in {
                "timezone",
                "default_delay_seconds",
                "default_cron",
                "default_stagger_seconds",
                "default_stagger_mode",
            }
        }
        sub["groups"] = account.get("groups", [])
        return sub

    async def run(self) -> None:
        await self.start_clients()
        await self.reload_config()
        self.scheduler.start()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop_event.set)
        self.logger.info("scheduler started accounts=%s", ",".join(sorted(self.runtimes)))
        await self.reload_loop()
        self.scheduler.shutdown(wait=False)
        for runtime in self.runtimes.values():
            await runtime.disconnect()
