import pytest

from tg_checkin.config import parse_jobs
from tg_checkin.flow import BotFlowRunner, classify_reply

ABORT_TEXT = "今日 Plus 成品号上限已满，明天再来"


def cfg(groups):
    return {"accounts": [{"name": "main", "groups": groups}]}


class FakeMessage:
    def __init__(self, message_id: int, text: str, *, out: bool = False, buttons=None):
        self.id = message_id
        self.raw_text = text
        self.out = out
        self.buttons = buttons


class FakeTelegramClient:
    def __init__(self, replies: list[str | FakeMessage]):
        self._next_id = 1
        self.replies = list(replies)
        self.sent: list[str] = []
        self.reply_index = 0

    async def send_message(self, entity, message: str, *, formatting_entities=None):
        self.sent.append(message)
        self._next_id += 1
        return FakeMessage(self._next_id, message, out=True)

    async def get_messages(self, entity, *, limit: int):
        return []

    def iter_messages(self, entity, *, limit: int):
        client = self

        class _Iter:
            async def __aiter__(self_inner):
                if client.reply_index < len(client.replies):
                    client._next_id += 1
                    reply = client.replies[client.reply_index]
                    client.reply_index += 1
                    if isinstance(reply, FakeMessage):
                        reply.id = client._next_id
                        yield reply
                    else:
                        yield FakeMessage(client._next_id, reply, out=False)

        return _Iter()


async def test_flow_count_stops_early_on_abort_text_and_does_not_run_next_round():
    job = parse_jobs(
        cfg(
            [
                {
                    "name": "PlusBot",
                    "chat_id": "freexzteam_bot",
                    "tasks": [
                        {
                            "name": "plus兑换",
                            "flow": {
                                "repeat": {"count": 50, "interval_seconds": 0},
                                "rules": {
                                    "abort_on_text": [ABORT_TEXT],
                                    "retry_on_text": ["库存不足", "请稍后再试"],
                                },
                                "steps": [{"action": "send", "text": "/start", "expect_any": ["库存不足", ABORT_TEXT]}],
                            },
                        }
                    ],
                }
            ]
        )
    )[0]
    client = FakeTelegramClient(["库存不足", "请稍后再试", ABORT_TEXT])

    result = await BotFlowRunner(client).run(job, object())

    assert result.status == "STOPPED_ABORT_TEXT"
    assert result.round == 3
    assert result.matched_text == ABORT_TEXT
    assert len(client.sent) == 3


async def test_click_step_resolves_button_by_substring_before_sending_full_label():
    class Button:
        def __init__(self, text: str):
            self.text = text

    job = parse_jobs(
        cfg(
            [
                {
                    "name": "PlusBot",
                    "chat_id": "freexzteam_bot",
                    "flow": {
                        "repeat": {"count": 1},
                        "steps": [
                            {"action": "send", "text": "🛍️ 积分商城", "expect_any": {"buttons": ["成品号"]}},
                            {"action": "click", "button": "成品号", "expect_any": "确认兑换"},
                        ],
                    },
                }
            ]
        )
    )[0]
    client = FakeTelegramClient(
        [
            FakeMessage(0, "商品列表", buttons=[[Button("💎 Plus 成品号(X渠道) · 5积分")]]),
            "请确认兑换",
        ]
    )

    result = await BotFlowRunner(client).run(job, object())

    assert result.status == "DONE_SUCCESS"
    assert client.sent == ["🛍️ 积分商城", "💎 Plus 成品号(X渠道) · 5积分"]


async def test_flow_count_reaches_limit_when_every_round_is_retry():
    job = parse_jobs(
        cfg(
            [
                {
                    "name": "PlusBot",
                    "chat_id": "freexzteam_bot",
                    "flow": {
                        "repeat": {"count": 3, "interval_seconds": 0},
                        "rules": {"retry_on_text": ["库存不足"]},
                        "steps": [{"action": "send", "text": "/start", "expect": "库存不足"}],
                    },
                }
            ]
        )
    )[0]
    client = FakeTelegramClient(["库存不足", "库存不足", "库存不足"])

    result = await BotFlowRunner(client).run(job, object())

    assert result.status == "DONE_COUNT_REACHED"
    assert result.round == 3
    assert len(client.sent) == 3


def test_reply_classifier_prioritizes_abort_over_success_and_retry():
    rules = parse_jobs(
        cfg(
            [
                {
                    "name": "PlusBot",
                    "chat_id": "freexzteam_bot",
                    "flow": {
                        "repeat": {"count": 1},
                        "rules": {
                            "abort_on_text": [ABORT_TEXT],
                            "success_on_text": ["兑换成功"],
                            "retry_on_text": ["库存不足"],
                        },
                        "steps": [{"action": "send", "text": "/start"}],
                    },
                }
            ]
        )
    )[0].flow.rules

    result = classify_reply(f"兑换成功，但{ABORT_TEXT}，库存不足", rules)

    assert result.status == "abort"
    assert result.matched_text == ABORT_TEXT


def test_structured_flow_config_validation_rejects_bad_repeat_and_step_shape():
    with pytest.raises(ValueError, match="repeat.count must be > 0"):
        parse_jobs(cfg([{"name": "bad", "chat_id": "freexzteam_bot", "flow": {"repeat": {"count": 0}, "steps": [{"action": "send", "text": "/start"}]}}]))

    with pytest.raises(ValueError, match="click step requires button"):
        parse_jobs(cfg([{"name": "bad", "chat_id": "freexzteam_bot", "flow": {"repeat": {"count": 1}, "steps": [{"action": "click"}]}}]))

    with pytest.raises(ValueError, match="unknown_policy must be retry or abort"):
        parse_jobs(
            cfg(
                [
                    {
                        "name": "bad",
                        "chat_id": "freexzteam_bot",
                        "flow": {
                            "repeat": {"count": 1},
                            "rules": {"unknown_policy": "ignore"},
                            "steps": [{"action": "send", "text": "/start"}],
                        },
                    }
                ]
            )
        )
