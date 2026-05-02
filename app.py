import asyncio
import logging
import os
import re
import base64
import hashlib
import signal
import sqlite3
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telethon import TelegramClient, events
from telethon.sessions import MemorySession, StringSession
from telethon.crypto import AuthKey
from telethon.tl.types import MessageEntityBotCommand

BOT_COMMAND_RE = re.compile(r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?")
DEFAULT_CRON = "0 10 0 * * *"  # daily 00:10:00
DEFAULT_STAGGER_SECONDS = 1800  # spread default-cron jobs across 30 minutes
LEGACY_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{80,}$")


@dataclass(frozen=True)
class JobConfig:
    name: str
    enabled: bool
    chat_id: int
    message: str
    parse_bot_command: bool
    cron: str
    delay_seconds: float
    run_on_start: bool
    stagger_seconds: int
    stagger_mode: str


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def env_int(name: str, required: bool = True, default: Optional[int] = None) -> Optional[int]:
    raw = os.getenv(name)
    if raw in (None, ""):
        if required:
            raise RuntimeError(f"missing required env: {name}")
        return default
    return int(raw)


def env_int_set(name: str) -> set[int]:
    raw = os.getenv(name, "")
    result: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            result.add(int(part))
    return result


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")
    data.setdefault("groups", [])
    return data


def save_config(path: str, config: Dict[str, Any]) -> None:
    target = Path(path)
    tmp = target.with_name(f".{target.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, target)


def normalize_chat_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("chat_id must be an integer")
    if isinstance(value, int):
        return value
    raw = str(value).strip()
    if not re.fullmatch(r"-?\d+", raw):
        raise ValueError(f"chat_id must be numeric, got: {value!r}")
    return int(raw)


def parse_jobs(config: Dict[str, Any]) -> List[JobConfig]:
    default_delay = float(config.get("default_delay_seconds", 3))
    default_cron = str(config.get("default_cron") or DEFAULT_CRON).strip() or DEFAULT_CRON
    default_stagger = int(config.get("default_stagger_seconds", DEFAULT_STAGGER_SECONDS))
    default_stagger_mode = str(config.get("default_stagger_mode") or "stable").strip().lower()
    if default_stagger < 0:
        raise ValueError("default_stagger_seconds must be >= 0")
    if default_stagger_mode not in {"stable", "random", "off"}:
        raise ValueError("default_stagger_mode must be stable, random, or off")
    jobs: List[JobConfig] = []
    for idx, item in enumerate(config.get("groups", []), start=1):
        if not isinstance(item, dict):
            raise ValueError(f"groups[{idx}] must be a mapping")
        name = str(item.get("name") or f"job-{idx}")
        chat_value = item.get("chat_id", item.get("chat"))
        if chat_value is None:
            raise ValueError(f"{name}: missing chat_id")
        if "message" not in item:
            raise ValueError(f"{name}: missing message")
        raw_cron = item.get("cron")
        cron = str(raw_cron or default_cron).strip()
        uses_default_cron = raw_cron in (None, "") or cron == default_cron
        stagger_seconds = int(item.get("stagger_seconds", default_stagger if uses_default_cron else 0))
        stagger_mode = str(item.get("stagger_mode") or default_stagger_mode).strip().lower()
        if stagger_seconds < 0:
            raise ValueError(f"{name}: stagger_seconds must be >= 0")
        if stagger_mode not in {"stable", "random", "off"}:
            raise ValueError(f"{name}: stagger_mode must be stable, random, or off")
        if stagger_mode == "off":
            stagger_seconds = 0
        jobs.append(
            JobConfig(
                name=name,
                enabled=bool(item.get("enabled", True)),
                chat_id=normalize_chat_id(chat_value),
                message=str(item["message"]),
                parse_bot_command=bool(item.get("parse_bot_command", True)),
                cron=cron,
                delay_seconds=float(item.get("delay_seconds", default_delay)),
                run_on_start=bool(item.get("run_on_start", False)),
                stagger_seconds=stagger_seconds,
                stagger_mode=stagger_mode,
            )
        )
    return jobs


def cron_trigger(expr: str, timezone: str) -> CronTrigger:
    fields = expr.split()
    if len(fields) == 5:
        minute, hour, day, month, day_of_week = fields
        second = "0"
    elif len(fields) == 6:
        second, minute, hour, day, month, day_of_week = fields
    else:
        raise ValueError(f"cron must have 5 or 6 fields, got: {expr}")
    return CronTrigger(
        second=second,
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=pytz.timezone(timezone),
    )


def command_entities(message: str, enabled: bool) -> Optional[List[MessageEntityBotCommand]]:
    if not enabled:
        return None
    match = BOT_COMMAND_RE.match(message)
    if not match:
        return None
    return [MessageEntityBotCommand(offset=0, length=len(match.group(0)))]


def stable_stagger_offset(job: JobConfig) -> int:
    if job.stagger_seconds <= 0:
        return 0
    if job.stagger_mode == "random":
        return int(time.time_ns() % (job.stagger_seconds + 1))
    seed = f"{job.name}:{job.chat_id}:{job.message}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") % (job.stagger_seconds + 1)


async def maybe_stagger_job(job: JobConfig) -> None:
    offset = stable_stagger_offset(job)
    if offset > 0:
        logging.getLogger("tg-checkin").info(
            "stagger job=%s chat_id=%s offset_seconds=%s mode=%s",
            job.name,
            job.chat_id,
            offset,
            job.stagger_mode,
        )
        await asyncio.sleep(offset)


def parse_control_command(text: str) -> Tuple[str, List[str]]:
    parts = text.strip().split()
    if not parts:
        return "", []
    cmd = parts[0].split("@", 1)[0].lower()
    return cmd, parts[1:]


def create_client(session_string: str, api_id: int, api_hash: str) -> TelegramClient:
    """Create a Telethon client from Telethon or Pyrogram-style strings."""
    session_string = session_string.strip()
    if session_string.startswith("1"):
        return TelegramClient(StringSession(session_string), api_id, api_hash)

    if LEGACY_SESSION_RE.fullmatch(session_string):
        raw = base64.urlsafe_b64decode(session_string + "=" * (-len(session_string) % 4))
        if len(raw) == struct.calcsize(">BI?256sQ?"):
            dc_id, embedded_api_id, test_mode, auth_key, user_id, is_bot = struct.unpack(">BI?256sQ?", raw)
            if embedded_api_id != api_id:
                raise ValueError("TG_SESSION_STRING api_id does not match TG_API_ID")
            session = MemorySession()
            session.set_dc(dc_id, dc_server_address(dc_id, test_mode), 443)
            session.auth_key = AuthKey(auth_key)
            return TelegramClient(session, api_id, api_hash)
        if raw.startswith(b"SQLite format 3\x00") and hasattr(sqlite3.Connection, "deserialize"):
            source = sqlite3.connect(":memory:")
            source.deserialize(raw)
            db_uri = "file:tgcheckin_session?mode=memory&cache=shared"
            shared = sqlite3.connect(db_uri, uri=True)
            source.backup(shared)
            shared.close()
            return TelegramClient(db_uri, api_id, api_hash)

    return TelegramClient(StringSession(session_string), api_id, api_hash)


def dc_server_address(dc_id: int, test_mode: bool = False) -> str:
    prod = {
        1: "149.154.175.53",
        2: "149.154.167.51",
        3: "149.154.175.100",
        4: "149.154.167.91",
        5: "91.108.56.130",
    }
    test = {
        1: "149.154.175.10",
        2: "149.154.167.40",
        3: "149.154.175.117",
    }
    table = test if test_mode else prod
    if dc_id not in table:
        raise ValueError(f"unsupported Telegram dc_id: {dc_id}")
    return table[dc_id]


class CheckinApp:
    def __init__(self) -> None:
        api_id = env_int("TG_API_ID")
        api_hash = os.getenv("TG_API_HASH")
        if not api_hash:
            raise RuntimeError("missing required env: TG_API_HASH")
        session_string = os.getenv("TG_SESSION_STRING")
        if not session_string:
            raise RuntimeError("missing required env: TG_SESSION_STRING")
        self.config_path = os.getenv("CONFIG_PATH", "/config/config.yml")
        self.reload_seconds = int(os.getenv("CONFIG_RELOAD_SECONDS", "60"))
        self.control_enabled = os.getenv("CONTROL_BOT_ENABLED", "true").lower() not in {"0", "false", "no"}
        self.client = create_client(session_string, api_id, api_hash)
        self.scheduler = AsyncIOScheduler()
        self.logger = logging.getLogger("tg-checkin")
        self._config_mtime: Optional[float] = None
        self._started_jobs: set[str] = set()
        self._stop_event = asyncio.Event()

    async def start_client(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram session string is not authorized. Set a valid TG_SESSION_STRING in .env")
        me = await self.client.get_me()
        self.logger.info("authorized as %s", getattr(me, "username", None) or getattr(me, "id", "unknown"))
        if self.control_enabled:
            # This is an automation userbot: only the logged-in account's own
            # outgoing commands are treated as configuration commands.
            self.client.add_event_handler(self.handle_control_message, events.NewMessage(outgoing=True))
            self.logger.info("control bot enabled for self outgoing commands")

    async def send_job(self, job: JobConfig, *, apply_stagger: bool = False) -> None:
        if apply_stagger:
            await maybe_stagger_job(job)
        entities = command_entities(job.message, job.parse_bot_command)
        self.logger.info("sending job=%s chat_id=%s message=%r bot_command_entity=%s", job.name, job.chat_id, job.message, bool(entities))
        await self.client.send_message(job.chat_id, job.message, formatting_entities=entities)
        if job.delay_seconds > 0:
            await asyncio.sleep(job.delay_seconds)

    async def reload_config(self) -> None:
        path = Path(self.config_path)
        stat = path.stat()
        if self._config_mtime == stat.st_mtime:
            return
        config = load_config(self.config_path)
        timezone = str(config.get("timezone") or "Asia/Shanghai")
        jobs = parse_jobs(config)
        self.scheduler.remove_all_jobs()
        self._config_mtime = stat.st_mtime
        enabled_count = 0
        for job in jobs:
            if not job.enabled:
                self.logger.info("skip disabled job=%s", job.name)
                continue
            trigger = cron_trigger(job.cron, timezone)
            self.scheduler.add_job(
                self.send_job,
                trigger=trigger,
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
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.reload_seconds)
            except asyncio.TimeoutError:
                pass

    async def handle_control_message(self, event: events.NewMessage.Event) -> None:
        text = event.raw_text or ""
        cmd, args = parse_control_command(text)
        if cmd not in {"/help", "/id", "/list", "/add", "/del", "/enable", "/disable", "/set", "/test"}:
            return
        self.logger.info("control command received cmd=%s sender_id=%s chat_id=%s outgoing=%s", cmd, event.sender_id, event.chat_id, event.out)
        try:
            reply = await self.run_control_command(cmd, args, event)
        except Exception as exc:
            self.logger.exception("control command failed: %s", text)
            reply = f"失败：{exc}"
        if reply:
            await self.reply_control(event, reply)

    async def reply_control(self, event: events.NewMessage.Event, text: str) -> None:
        if event.out:
            await self.client.send_message(event.chat_id, text, reply_to=event.id)
        else:
            await event.reply(text)

    async def run_control_command(self, cmd: str, args: List[str], event: events.NewMessage.Event) -> str:
        if cmd == "/help":
            return (
                "命令：\n"
                "/id - 显示当前 chat_id 和 user_id\n"
                "/list - 列出任务；群内使用时优先显示本群任务\n"
                "/add <message...> - 在当前群自动用群名和 chat_id 添加，默认每天 00:10，且自动错峰发送\n"
                "/add <cron|-> <message...> - 当前群指定 cron，cron 用 _ 代替空格；显式 cron 默认不自动错峰\n"
                "/add <name> <chat_id> <cron|-> <message...> - 完整模式\n"
                "/del [name]，群内省略 name 时删除本群任务\n"
                "/enable [name] | /disable [name]\n"
                "/set [name] cron <expr|-> | message <text> | chat_id <id>\n"
                "/test [name] - 立即发送一次"
            )
        if cmd == "/id":
            return f"chat_id={event.chat_id}\nuser_id={event.sender_id}"

        config = load_config(self.config_path)
        groups = config.setdefault("groups", [])
        if not isinstance(groups, list):
            raise ValueError("config groups must be a list")

        async def current_chat_name() -> str:
            entity = await event.get_chat()
            for attr in ("title", "first_name", "username"):
                value = getattr(entity, attr, None)
                if value:
                    return str(value)
            return f"chat_{event.chat_id}"

        def display_cron(value: str) -> str:
            return value or DEFAULT_CRON

        def find_index(name: str) -> int:
            for i, item in enumerate(groups):
                if str(item.get("name")) == name:
                    return i
            raise ValueError(f"任务不存在：{name}")

        def find_index_by_chat_id(chat_id: int) -> int:
            matches = []
            for i, item in enumerate(groups):
                chat_value = item.get("chat_id", item.get("chat"))
                if chat_value is not None and normalize_chat_id(chat_value) == chat_id:
                    matches.append(i)
            if not matches:
                raise ValueError(f"当前群未配置任务：chat_id={chat_id}")
            if len(matches) > 1:
                names = ", ".join(str(groups[i].get("name")) for i in matches)
                raise ValueError(f"当前群匹配到多个任务，请指定 name：{names}")
            return matches[0]

        def resolve_index(optional_name: Optional[str]) -> int:
            if optional_name:
                return find_index(optional_name)
            return find_index_by_chat_id(normalize_chat_id(event.chat_id))

        if cmd == "/list":
            if not groups:
                return "暂无任务。"
            current_id = normalize_chat_id(event.chat_id)
            scoped = []
            for item in groups:
                chat_value = item.get("chat_id", item.get("chat"))
                if chat_value is not None and normalize_chat_id(chat_value) == current_id:
                    scoped.append(item)
            show_groups = scoped or groups
            header = "本群任务：" if scoped else "任务列表："
            lines = []
            for item in show_groups:
                state = "启用" if item.get("enabled", True) else "禁用"
                lines.append(f"- {item.get('name')}: {state}, chat_id={item.get('chat_id', item.get('chat'))}, cron={display_cron(str(item.get('cron') or ''))}, msg={item.get('message')}")
            return header + "\n" + "\n".join(lines)

        if cmd == "/add":
            # Full mode: /add <name> <chat_id> <cron|-> <message...>
            if len(args) >= 4:
                try:
                    normalize_chat_id(args[1])
                    full_mode = True
                except ValueError:
                    full_mode = False
            else:
                full_mode = False

            if full_mode:
                name, chat_id_raw, cron_raw = args[0], args[1], args[2]
                message = " ".join(args[3:])
            else:
                if len(args) < 1:
                    return "用法：群内 /add <message...> 或 /add <cron|-> <message...>；完整模式 /add <name> <chat_id> <cron|-> <message...>"
                name = await current_chat_name()
                chat_id_raw = str(event.chat_id)
                if args[0] in {"-", "default", "默认"} or len(args[0].replace("_", " ").split()) in {5, 6}:
                    cron_raw = args[0]
                    message = " ".join(args[1:])
                    if not message:
                        return "用法：/add <cron|-> <message...>；message 不能为空"
                else:
                    cron_raw = "-"
                    message = " ".join(args)

            chat_id = normalize_chat_id(chat_id_raw)
            existing_names = {str(item.get("name")) for item in groups}
            base_name = name
            suffix = 2
            while name in existing_names:
                name = f"{base_name}-{suffix}"
                suffix += 1
            if any(normalize_chat_id(item.get("chat_id", item.get("chat"))) == chat_id for item in groups if item.get("chat_id", item.get("chat")) is not None):
                raise ValueError(f"当前 chat_id 已存在任务；如需修改请用 /set 或先 /del：{chat_id}")
            cron = DEFAULT_CRON if cron_raw in {"-", "default", "默认"} else cron_raw.replace("_", " ")
            job = {
                "name": name,
                "enabled": True,
                "chat_id": chat_id,
                "message": message,
                "parse_bot_command": True,
                "cron": "" if cron == DEFAULT_CRON and cron_raw in {"-", "default", "默认"} else cron,
                "run_on_start": False,
            }
            parsed_job = parse_jobs({"groups": [job]})[0]
            cron_trigger(parsed_job.cron, str(config.get("timezone") or "Asia/Shanghai"))
            groups.append(job)
            save_config(self.config_path, config)
            await self.force_reload_after_write()
            return f"已添加：{name}\nchat_id={chat_id}\ncron={display_cron(str(job.get('cron') or ''))}\nmessage={message}"

        if cmd == "/del":
            if len(args) > 1:
                return "用法：/del [name]"
            idx = resolve_index(args[0] if args else None)
            removed = groups.pop(idx)
            save_config(self.config_path, config)
            await self.force_reload_after_write()
            return f"已删除：{removed.get('name')}"

        if cmd in {"/enable", "/disable"}:
            if len(args) > 1:
                return f"用法：{cmd} [name]"
            idx = resolve_index(args[0] if args else None)
            groups[idx]["enabled"] = cmd == "/enable"
            save_config(self.config_path, config)
            await self.force_reload_after_write()
            return f"已{'启用' if cmd == '/enable' else '禁用'}：{groups[idx].get('name')}"

        if cmd == "/set":
            if len(args) < 2:
                return "用法：/set [name] cron <expr|-> | message <text> | chat_id <id>"
            if args[0] in {"cron", "message", "chat_id"}:
                idx = resolve_index(None)
                field = args[0]
                value = " ".join(args[1:])
            else:
                if len(args) < 3:
                    return "用法：/set <name> cron <expr|-> | message <text> | chat_id <id>"
                idx = resolve_index(args[0])
                field = args[1]
                value = " ".join(args[2:])
            if field == "cron":
                if value in {"-", "default", "默认", ""}:
                    value = ""
                    effective_cron = DEFAULT_CRON
                else:
                    value = value.replace("_", " ")
                    effective_cron = value
                cron_trigger(effective_cron, str(config.get("timezone") or "Asia/Shanghai"))
                groups[idx]["cron"] = value
            elif field == "message":
                groups[idx]["message"] = value
            elif field == "chat_id":
                groups[idx]["chat_id"] = normalize_chat_id(value)
                groups[idx].pop("chat", None)
            else:
                return "字段只支持：cron、message、chat_id"
            save_config(self.config_path, config)
            await self.force_reload_after_write()
            return f"已更新：{groups[idx].get('name')} {field}"

        if cmd == "/test":
            if len(args) > 1:
                return "用法：/test [name]"
            idx = resolve_index(args[0] if args else None)
            target_name = str(groups[idx].get("name"))
            jobs = parse_jobs(config)
            job = next((j for j in jobs if j.name == target_name), None)
            if not job:
                raise ValueError(f"任务不存在：{target_name}")
            await self.send_job(job)
            return f"已测试发送：{job.name}"

        return "未知命令。"

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


async def auth() -> None:
    raise SystemExit("auth command is disabled. Generate a Telethon StringSession locally and set TG_SESSION_STRING in .env")


async def validate() -> None:
    setup_logging()
    config_path = os.getenv("CONFIG_PATH", "/config/config.yml")
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
        asyncio.run(CheckinApp().run())
    elif cmd == "auth":
        asyncio.run(auth())
    elif cmd == "validate":
        asyncio.run(validate())
    else:
        raise SystemExit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
