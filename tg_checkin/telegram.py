from __future__ import annotations

import base64
import re
import sqlite3
import struct
from typing import Optional

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession, StringSession
from telethon.tl.types import MessageEntityBotCommand

BOT_COMMAND_RE = re.compile(r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?")
LEGACY_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{80,}$")


def command_entities(message: str, enabled: bool) -> Optional[list[MessageEntityBotCommand]]:
    if not enabled:
        return None
    match = BOT_COMMAND_RE.match(message)
    if not match:
        return None
    return [MessageEntityBotCommand(offset=0, length=len(match.group(0)))]


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


def create_client(session_string: str, api_id: int, api_hash: str) -> TelegramClient:
    """Create a Telethon client from Telethon or Pyrogram-style strings."""
    session_string = session_string.strip()
    if session_string.startswith("1"):
        return TelegramClient(StringSession(session_string), api_id, api_hash)

    if LEGACY_SESSION_RE.fullmatch(session_string):
        raw = base64.urlsafe_b64decode(session_string + "=" * (-len(session_string) % 4))
        if len(raw) == struct.calcsize(">BI?256sQ?"):
            dc_id, embedded_api_id, test_mode, auth_key, _user_id, _is_bot = struct.unpack(">BI?256sQ?", raw)
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
