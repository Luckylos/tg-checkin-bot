from tg_checkin.control import ControlContext, ControlService


class Harness:
    def __init__(self):
        self.config = {
            "timezone": "Asia/Shanghai",
            "groups": [],
        }
        self.reloads = 0
        self.sent = []

    def service(self):
        return ControlService(
            load_config=lambda: self.config,
            save_config=lambda config: setattr(self, "config", config),
            reload_scheduler=self.reload,
            send_job=self.send,
        )

    async def reload(self):
        self.reloads += 1

    async def send(self, job):
        self.sent.append(job)


async def test_group_context_add_uses_chat_name_and_id():
    h = Harness()
    service = h.service()

    reply = await service.run(
        "/add",
        ["/checkin@HyVPS_Bot"],
        ControlContext(chat_id=-1003849837200, sender_id=123, chat_name="HyVPS"),
    )

    assert "已添加：HyVPS" in reply
    assert h.config["groups"] == [
        {
            "name": "HyVPS",
            "enabled": True,
            "chat_id": -1003849837200,
            "message": "/checkin@HyVPS_Bot",
            "parse_bot_command": True,
            "cron": "",
            "run_on_start": False,
        }
    ]
    assert h.reloads == 1


async def test_group_context_set_enable_disable_delete_and_test():
    h = Harness()
    h.config["groups"] = [
        {
            "name": "HyVPS",
            "enabled": True,
            "chat_id": -1003849837200,
            "message": "/old@Bot",
            "parse_bot_command": True,
            "cron": "",
            "run_on_start": False,
        }
    ]
    service = h.service()
    ctx = ControlContext(chat_id=-1003849837200, sender_id=123, chat_name="HyVPS")

    assert await service.run("/set", ["message", "/checkin@HyVPS_Bot"], ctx) == "已更新：HyVPS message"
    assert h.config["groups"][0]["message"] == "/checkin@HyVPS_Bot"

    assert await service.run("/disable", [], ctx) == "已禁用：HyVPS"
    assert h.config["groups"][0]["enabled"] is False

    assert await service.run("/enable", [], ctx) == "已启用：HyVPS"
    assert h.config["groups"][0]["enabled"] is True

    assert await service.run("/test", [], ctx) == "已测试发送：HyVPS"
    assert h.sent and h.sent[0].name == "HyVPS"

    assert await service.run("/del", [], ctx) == "已删除：HyVPS"
    assert h.config["groups"] == []


async def test_full_mode_add_allows_duplicate_chat_for_different_messages_at_different_times():
    h = Harness()
    service = h.service()
    ctx = ControlContext(chat_id=1, sender_id=123, chat_name="private")

    await service.run("/add", ["HyVPS-morning", "-1003849837200", "0_10_9_*_*_*", "/checkin@HyVPS_Bot"], ctx)
    await service.run("/add", ["HyVPS-night", "-1003849837200", "0_10_21_*_*_*", "/sign@OtherBot"], ctx)

    assert [item["name"] for item in h.config["groups"]] == ["HyVPS-morning", "HyVPS-night"]
    assert [item["cron"] for item in h.config["groups"]] == ["0 10 9 * * *", "0 10 21 * * *"]
    assert [item["message"] for item in h.config["groups"]] == ["/checkin@HyVPS_Bot", "/sign@OtherBot"]


async def test_group_context_add_appends_tasks_with_different_messages_to_same_chat_group():
    h = Harness()
    service = h.service()
    ctx = ControlContext(chat_id=-1003849837200, sender_id=123, chat_name="HyVPS")

    await service.run("/add", ["morning", "0_10_9_*_*_*", "/checkin@HyVPS_Bot"], ctx)
    await service.run("/add", ["night", "0_10_21_*_*_*", "/sign@OtherBot"], ctx)

    assert h.config["groups"] == [
        {
            "name": "HyVPS",
            "enabled": True,
            "chat_id": -1003849837200,
            "parse_bot_command": True,
            "tasks": [
                {"name": "morning", "cron": "0 10 9 * * *", "message": "/checkin@HyVPS_Bot", "run_on_start": False},
                {"name": "night", "cron": "0 10 21 * * *", "message": "/sign@OtherBot", "run_on_start": False},
            ],
        }
    ]

    jobs = [job for job in h.sent]
    assert jobs == []


async def test_task_group_commands_resolve_child_task_names():
    h = Harness()
    h.config["groups"] = [
        {
            "name": "公益Plus/Team机器人",
            "enabled": True,
            "chat_id": 8604751086,
            "parse_bot_command": True,
            "tasks": [
                {"name": "签到", "cron": "", "message": "📅 每日签到", "run_on_start": False},
                {"name": "PP_PLUS1", "cron": "5 0 0 * * *", "message": "💎 Plus 成品号(PP渠道) · 3积分", "run_on_start": False},
            ],
        }
    ]
    service = h.service()
    ctx = ControlContext(chat_id=8604751086, sender_id=123, chat_name="公益Plus/Team机器人")

    assert await service.run("/test", ["PP_PLUS1"], ctx) == "已测试发送：公益Plus/Team机器人/PP_PLUS1"
    assert h.sent[-1].name == "公益Plus/Team机器人/PP_PLUS1"
    assert h.sent[-1].message == "💎 Plus 成品号(PP渠道) · 3积分"

    assert await service.run("/test", ["公益Plus/Team机器人/签到"], ctx) == "已测试发送：公益Plus/Team机器人/签到"
    assert h.sent[-1].name == "公益Plus/Team机器人/签到"

    assert await service.run("/disable", ["PP_PLUS1"], ctx) == "已禁用：公益Plus/Team机器人/PP_PLUS1"
    assert h.config["groups"][0]["tasks"][1]["enabled"] is False

    assert await service.run("/set", ["PP_PLUS1", "message", "新内容"], ctx) == "已更新：公益Plus/Team机器人/PP_PLUS1 message"
    assert h.config["groups"][0]["tasks"][1]["message"] == "新内容"


async def test_set_cron_accepts_underscore_and_default_alias():
    h = Harness()
    h.config["groups"] = [{"name": "A", "chat_id": -1001, "message": "签到", "cron": "0 5 9 * * *"}]
    service = h.service()
    ctx = ControlContext(chat_id=-1001, sender_id=123, chat_name="A")

    await service.run("/set", ["cron", "0_10_9_*_*_*"], ctx)
    assert h.config["groups"][0]["cron"] == "0 10 9 * * *"

    await service.run("/set", ["cron", "-"], ctx)
    assert h.config["groups"][0]["cron"] == ""
