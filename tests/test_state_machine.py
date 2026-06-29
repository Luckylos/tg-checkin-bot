import pytest

from tg_checkin.config import parse_jobs
from tg_checkin.flow import render_reply_text
from tg_checkin.flow_config import parse_flow


def cfg(groups):
    return {"accounts": [{"name": "main", "groups": groups}]}


def test_parse_jobs_supports_state_machine_flow_steps():
    jobs = parse_jobs(
        cfg(
            [
                {
                    "name": "公益Plus/Team机器人",
                    "chat_id": "freexzteam_bot",
                    "tasks": [
                        {
                            "name": "PP_PLUS_FLOW",
                            "cron": "0 0 0 * * *",
                            "flow": {
                                "steps": [
                                    {"action": "send", "text": "/start", "expect_any": ["积分商城"]},
                                    {"action": "click", "button": "积分商城", "expect_any": ["Plus 成品号"]},
                                    {"action": "click", "button": "成品号", "expect_any": ["确认兑换"]},
                                    {"action": "click", "button": "✅ 确认兑换", "expect_any": ["兑换", "上限", "积分不足"]},
                                ]
                            },
                        }
                    ],
                }
            ]
        )
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.name == "main/公益Plus/Team机器人/PP_PLUS_FLOW"
    assert job.message == ""
    assert [step.send for step in job.flow] == ["/start", "积分商城", "成品号", "✅ 确认兑换"]
    assert job.flow[0].expect_any == ("积分商城",)
    assert job.flow[-1].expect_any == ("兑换", "上限", "积分不足")


def test_parse_jobs_requires_message_or_flow():
    with pytest.raises(ValueError, match="missing message or flow"):
        parse_jobs(cfg([{"name": "bad", "chat_id": "freexzteam_bot", "tasks": [{"name": "x"}]}]))


def test_parse_flow_validates_required_fields_and_bounds():
    assert parse_flow({"steps": [{"action": "send", "text": "hello", "expect_any": "ok", "timeout": 1}]}).steps[0].expect_any == ("ok",)
    with pytest.raises(ValueError, match="send step requires text"):
        parse_flow({"steps": [{"action": "send", "expect_any": "no-send"}]})
    with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
        parse_flow({"steps": [{"action": "send", "text": "hello", "timeout_seconds": 0}]})
    with pytest.raises(ValueError, match="expect_any.bad must be string or list"):
        parse_flow({"steps": [{"action": "send", "text": "hello", "expect_any": {"bad": True}}]})


def test_render_reply_text_includes_button_labels():
    class Button:
        def __init__(self, text):
            self.text = text

    class Message:
        id = 1
        out = False
        raw_text = "welcome"
        buttons = [[Button("🛍️ 积分商城"), Button("📅 每日签到")]]

    rendered = render_reply_text(Message())
    assert "welcome" in rendered
    assert "积分商城" in rendered


def test_flow_task_does_not_remove_regular_signin_task():
    jobs = parse_jobs(
        cfg(
            [
                {
                    "name": "公益Plus/Team机器人",
                    "chat_id": "freexzteam_bot",
                    "tasks": [
                        {"name": "签到", "cron": "", "message": "📅 每日签到"},
                        {"name": "兑换", "cron": "0 0 0 * * *", "flow": {"steps": [{"action": "send", "text": "/start", "expect_any": ["积分商城"]}]}},
                    ],
                }
            ]
        )
    )

    assert [job.name for job in jobs] == ["main/公益Plus/Team机器人/签到", "main/公益Plus/Team机器人/兑换"]
    assert jobs[0].message == "📅 每日签到"
    assert jobs[0].flow == ()
    assert jobs[1].message == ""
    assert jobs[1].flow[0].send == "/start"


def test_parse_flow_accepts_count_zero_as_noop():
    """repeat.count=0 is valid and means "this flow should not execute"."""
    flow = parse_flow({"steps": [{"action": "send", "text": "/start", "expect_any": ["ok"]}], "repeat": {"count": 0}})
    assert flow.repeat.count == 0
    assert flow.is_noop is True
    assert flow  # has steps, truthy; is_noop controls execution


def test_parse_flow_rejects_count_negative():
    with pytest.raises(ValueError, match="repeat.count must be >= 0"):
        parse_flow({"steps": [{"action": "send", "text": "/start"}], "repeat": {"count": -1}})
