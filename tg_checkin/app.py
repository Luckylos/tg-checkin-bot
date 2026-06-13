from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import events

from .config import load_config, parse_accounts, parse_jobs, save_config
from .control import CONTROL_COMMANDS, ControlContext, ControlService, parse_control_command
from .flow import BotFlowRunner
from .models import AccountSettings, AppSettings, JobConfig
from .scheduler import cron_trigger, maybe_stagger_job
from .telegram import command_entities, create_client, resolve_send_entity


class AccountRuntime:
    def __init__(self, app: CheckinApp, account: AccountSettings) -> None:
        self.app = app
        self.account = account
        self.client = create_client(
            account.session_string,
            account.api_id,
            account.api_hash,
            proxy_type=app.settings.telegram_proxy_type,
            proxy_host=app.settings.telegram_proxy_host,
            proxy_port=app.settings.telegram_proxy_port,
        )
        self.logger = logging.getLogger("tg-checkin")
        self.flow_runner = BotFlowRunner(self.client, logger=self.logger)
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self.control = ControlService(
            account_name=account.name,
            load_config=self.load_account_config,
            save_config=self.save_account_config,
            reload_scheduler=self.app.force_reload_after_write,
            send_job=self.send_control_job,
        )

    async def start_client(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError(f"{self.account.name}: Telegram session string is not authorized")
        me = await self.client.get_me()
        self.logger.info(
            "authorized account=%s as %s",
            self.account.name,
            getattr(me, "username", None) or getattr(me, "id", "unknown"),
        )
        if self.app.settings.control_enabled:
            self.client.add_event_handler(self.handle_control_message, events.NewMessage(outgoing=True))
            self.logger.info("control bot enabled for account=%s self outgoing commands", self.account.name)

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def send_job(self, job: JobConfig, *, apply_stagger: bool = False) -> None:
        if apply_stagger:
            await maybe_stagger_job(job)
        lock = self._chat_locks.setdefault(str(job.chat_id), asyncio.Lock())
        async with lock:
            entity = await resolve_send_entity(self.client, job.chat_id)
            if job.flow:
                await self.flow_runner.run(job, entity)
            else:
                await self.send_single_message_job(job, entity)
            if job.delay_seconds > 0:
                await asyncio.sleep(job.delay_seconds)

    async def send_control_job(self, job: JobConfig) -> None:
        await self.send_job(job)

    async def send_single_message_job(self, job: JobConfig, entity: Any) -> None:
        entities = command_entities(job.message, job.parse_bot_command)
        self.logger.info(
            "sending account=%s job=%s chat_id=%s message=%r bot_command_entity=%s",
            self.account.name,
            job.name,
            job.chat_id,
            job.message,
            bool(entities),
        )
        await self.client.send_message(entity, job.message, formatting_entities=entities)

    async def handle_control_message(self, event: events.NewMessage.Event) -> None:
        text = event.raw_text or ""
        cmd, args = parse_control_command(text)
        if cmd not in CONTROL_COMMANDS:
            return
        self.logger.info(
            "control command received account=%s cmd=%s sender_id=%s chat_id=%s outgoing=%s",
            self.account.name,
            cmd,
            event.sender_id,
            event.chat_id,
            event.out,
        )
        try:
            chat_name = await self.current_chat_name(event)
            reply = await self.control.run(
                cmd,
                args,
                ControlContext(chat_id=event.chat_id, sender_id=event.sender_id, chat_name=chat_name),
            )
        except Exception as exc:
            self.logger.exception("control command failed account=%s text=%s", self.account.name, text)
            reply = f"失败：{exc}"
        if reply:
            await self.reply_control(event, reply)

    async def current_chat_name(self, event: events.NewMessage.Event) -> str:
        entity = await event.get_chat()
        for attr in ("title", "first_name", "username"):
            value = getattr(entity, attr, None)
            if value:
                return str(value)
        return f"chat_{event.chat_id}"

    async def reply_control(self, event: events.NewMessage.Event, text: str) -> None:
        await self.client.send_message(event.chat_id, text, reply_to=event.id)

    def load_account_config(self) -> dict[str, Any]:
        full = load_config(self.app.settings.config_path)
        return self.app.extract_account_config(full, self.account.name)

    def save_account_config(self, account_config: dict[str, Any]) -> None:
        full = load_config(self.app.settings.config_path)
        updated = self.app.replace_account_config(full, self.account.name, account_config)
        save_config(self.app.settings.config_path, updated)


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
