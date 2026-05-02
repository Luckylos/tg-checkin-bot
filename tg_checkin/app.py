from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import events

from .config import load_config, parse_jobs, save_config
from .control import CONTROL_COMMANDS, ControlContext, ControlService, parse_control_command
from .models import AppSettings, JobConfig
from .scheduler import cron_trigger, maybe_stagger_job
from .telegram import command_entities, create_client


class CheckinApp:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.client = create_client(settings.session_string, settings.api_id, settings.api_hash)
        self.scheduler = AsyncIOScheduler()
        self.logger = logging.getLogger("tg-checkin")
        self._config_mtime: float | None = None
        self._started_jobs: set[str] = set()
        self._stop_event = asyncio.Event()
        self.control = ControlService(
            load_config=lambda: load_config(self.settings.config_path),
            save_config=lambda config: save_config(self.settings.config_path, config),
            reload_scheduler=self.force_reload_after_write,
            send_job=self.send_job,
        )

    async def start_client(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram session string is not authorized. Set a valid TG_SESSION_STRING in .env")
        me = await self.client.get_me()
        self.logger.info("authorized as %s", getattr(me, "username", None) or getattr(me, "id", "unknown"))
        if self.settings.control_enabled:
            self.client.add_event_handler(self.handle_control_message, events.NewMessage(outgoing=True))
            self.logger.info("control bot enabled for self outgoing commands")

    async def send_job(self, job: JobConfig, *, apply_stagger: bool = False) -> None:
        if apply_stagger:
            await maybe_stagger_job(job)
        entities = command_entities(job.message, job.parse_bot_command)
        self.logger.info(
            "sending job=%s chat_id=%s message=%r bot_command_entity=%s",
            job.name,
            job.chat_id,
            job.message,
            bool(entities),
        )
        await self.client.send_message(job.chat_id, job.message, formatting_entities=entities)
        if job.delay_seconds > 0:
            await asyncio.sleep(job.delay_seconds)

    async def reload_config(self) -> None:
        path = Path(self.settings.config_path)
        stat = path.stat()
        if self._config_mtime == stat.st_mtime:
            return
        config = load_config(self.settings.config_path)
        timezone = str(config.get("timezone") or "Asia/Shanghai")
        jobs = parse_jobs(config)
        self.scheduler.remove_all_jobs()
        self._config_mtime = stat.st_mtime
        enabled_count = 0
        for job in jobs:
            if not job.enabled:
                self.logger.info("skip disabled job=%s", job.name)
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
                "scheduled job=%s cron=%s timezone=%s stagger_seconds=%s stagger_mode=%s",
                job.name,
                job.cron,
                timezone,
                job.stagger_seconds,
                job.stagger_mode,
            )
            start_key = f"{job.name}:{job.cron}:{job.chat_id}:{job.message}"
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

    async def handle_control_message(self, event: events.NewMessage.Event) -> None:
        text = event.raw_text or ""
        cmd, args = parse_control_command(text)
        if cmd not in CONTROL_COMMANDS:
            return
        self.logger.info(
            "control command received cmd=%s sender_id=%s chat_id=%s outgoing=%s",
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
            self.logger.exception("control command failed: %s", text)
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

    async def run(self) -> None:
        await self.start_client()
        await self.reload_config()
        self.scheduler.start()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop_event.set)
        self.logger.info("scheduler started")
        await self.reload_loop()
        self.scheduler.shutdown(wait=False)
        await self.client.disconnect()
