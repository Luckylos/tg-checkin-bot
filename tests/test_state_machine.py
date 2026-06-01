import pytest

from tg_checkin.config import parse_jobs
from tg_checkin.flow import render_reply_text
from tg_checkin.flow_config import parse_flow


def test_parse_jobs_supports_state_machine_flow_steps():
    jobs = parse_jobs(
        {
            "groups": [
                {
                    "name": "公益Plus/Team机器人",
                    "chat_id": "freexzteam_bot",
                    "tasks": [
                        {
                            "name": "PP_PLUS_FLOW",
                            "cron": "0 0 0 * * *",
                            "flow": [
                                {"send": "/start", "expect": "积分商城"},
                                {"send": "🛍️ 积分商城", "expect": "Plus 成品号"},
                                {"send": "💎 Plus 成品号(PP渠道) · 3积分", "expect": "确认兑换"},
                                {"send": "✅ 确认兑换", "expect_any": ["兑换", "上限", "积分不足"]},
                            ],
                        }
                    ],
                }
            ]
        }
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.message == ""
    assert [step.send for step in job.flow] == [
        "/start",
        "🛍️ 积分商城",
        "💎 Plus 成品号(PP渠道) · 3积分",
        "✅ 确认兑换",
    ]
    assert job.flow[0].expect_any == ("积分商城",)
    assert job.flow[-1].expect_any == ("兑换", "上限", "积分不足")


def test_parse_jobs_requires_message_or_flow():
    with pytest.raises(ValueError, match="missing message or flow"):
        parse_jobs({"groups": [{"name": "bad", "chat_id": "freexzteam_bot", "tasks": [{"name": "x"}]}]})


def test_parse_flow_validates_required_fields_and_bounds():
    assert parse_flow([{"send": "hello", "expect_any": "ok", "timeout": 1}])[0].expect_any == ("ok",)
    with pytest.raises(ValueError, match="missing send"):
        parse_flow([{"expect": "no-send"}])
    with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
        parse_flow([{"send": "hello", "timeout_seconds": 0}])
    with pytest.raises(ValueError, match="expect_any must be string or list"):
        parse_flow([{"send": "hello", "expect_any": {"bad": True}}])


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
        {
            "groups": [
                {
                    "name": "公益Plus/Team机器人",
                    "chat_id": "freexzteam_bot",
                    "tasks": [
                        {"name": "签到", "cron": "", "message": "📅 每日签到"},
                        {"name": "兑换", "cron": "0 0 0 * * *", "flow": [{"send": "/start", "expect": "积分商城"}]},
                    ],
                }
            ]
        }
    )

    assert [job.name for job in jobs] == ["公益Plus/Team机器人/签到", "公益Plus/Team机器人/兑换"]
    assert jobs[0].message == "📅 每日签到"
    assert jobs[0].flow == ()
    assert jobs[1].message == ""
    assert jobs[1].flow[0].send == "/start"
