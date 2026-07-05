from __future__ import annotations

import asyncio
from typing import Any, Protocol


class TelegramMessageLike(Protocol):
    id: int
    out: bool
    raw_text: str | None
    buttons: Any


class TelegramClientLike(Protocol):
    async def send_message(self, entity, message: str, *, formatting_entities=None) -> Any: ...
    async def get_messages(self, entity, *, limit: int) -> Any: ...
    def iter_messages(self, entity, *, limit: int) -> Any: ...


class FlowTransport:
    def __init__(self, client: TelegramClientLike) -> None:
        self.client = client

    async def send_message(self, entity, message: str, *, formatting_entities=None) -> Any:
        return await self.client.send_message(entity, message, formatting_entities=formatting_entities)

    async def latest_message_id(self, entity) -> int:
        messages = await self.client.get_messages(entity, limit=1)
        if not messages:
            return 0
        return int(messages[0].id)

    async def wait_for_reply(self, entity, *, after_id: int, timeout: float):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            async for message in self.client.iter_messages(entity, limit=10):
                if message.id > after_id and not message.out:
                    return message
            await asyncio.sleep(1)
        return None
