from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from telethon import events

from .config import load_config, save_config
from .control import CONTROL_COMMANDS, ControlContext, ControlService, parse_control_command
from .flow import BotFlowRunner
from .models import AccountSettings, JobConfig
from .scheduler import maybe_stagger_job
from .telegram import command_entities, create_client, resolve_send_entity

if TYPE_CHECKING:
    from .app import CheckinApp


class AccountRuntime:
    def __init__(self, app: CheckinApp, account: AccountSettings) -> None:
        self.app = app
        self.account = account
        self.logger = logging.getLogger("tg-checkin")
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._bind_client(self._create_client())
        self.control = ControlService(
            account_name=account.name,
            load_config=self.load_account_config,
            save_config=self.save_account_config,
            reload_scheduler=self.app.force_reload_after_write,
            send_job=self.send_control_job,
        )

    def _create_client(self):
        return create_client(
            self.account.session_string,
            self.account.api_id,
            self.account.api_hash,
            proxy_type=self.app.settings.telegram_proxy_type,
            proxy_host=self.app.settings.telegram_proxy_host,
            proxy_port=self.app.settings.telegram_proxy_port,
        )

    def _bind_client(self, client) -> None:
        self.client = client
        self.flow_runner = BotFlowRunner(self.client, logger=self.logger)

    def _client_connected(self) -> bool:
        is_connected = getattr(self.client, "is_connected", None)
        if callable(is_connected):
            return bool(is_connected())
        return False

    async def _authorize_bound_client(self) -> None:
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

    async def start_client(self) -> None:
        await self._authorize_bound_client()

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def ensure_client_ready(self, *, reason: str) -> None:
        if self._client_connected() and await self.client.is_user_authorized():
            return
        await self.rebuild_client(reason=reason)

    async def rebuild_client(self, *, reason: str) -> None:
        self.logger.warning("rebuilding client account=%s reason=%s", self.account.name, reason)
        try:
            await self.client.disconnect()
        except Exception:
            self.logger.exception("client disconnect during rebuild failed account=%s", self.account.name)
        self._bind_client(self._create_client())
        await self._authorize_bound_client()

    async def _dispatch_job_once(self, job: JobConfig) -> None:
        entity = await resolve_send_entity(self.client, job.chat_id)
        if job.flow:
            await self.flow_runner.run(job, entity)
        else:
            await self.send_single_message_job(job, entity)

    async def send_job(self, job: JobConfig, *, apply_stagger: bool = False) -> None:
        if apply_stagger:
            await maybe_stagger_job(job)
        lock = self._chat_locks.setdefault(str(job.chat_id), asyncio.Lock())
        async with lock:
            await self.ensure_client_ready(reason=f"preflight:{job.name}")
            try:
                await self._dispatch_job_once(job)
            except ConnectionError as exc:
                if job.flow:
                    self.logger.warning(
                        "flow connection lost account=%s job=%s; skip auto-retry to avoid duplicate interaction: %s",
                        self.account.name,
                        job.name,
                        exc,
                    )
                    raise
                self.logger.warning(
                    "connection lost account=%s job=%s; rebuilding client and retrying once: %s",
                    self.account.name,
                    job.name,
                    exc,
                )
                await self.rebuild_client(reason=f"retry:{job.name}:{type(exc).__name__}")
                await self._dispatch_job_once(job)
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
