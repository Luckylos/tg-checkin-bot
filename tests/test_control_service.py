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


async def test_full_mode_add_allows_duplicate_chat_for_multiple_time_slots():
    h = Harness()
    service = h.service()
    ctx = ControlContext(chat_id=1, sender_id=123, chat_name="private")

    await service.run("/add", ["HyVPS-morning", "-1003849837200", "0_10_9_*_*_*", "/checkin@HyVPS_Bot"], ctx)
    await service.run("/add", ["HyVPS-night", "-1003849837200", "0_10_21_*_*_*", "/checkin@HyVPS_Bot"], ctx)

    assert [item["name"] for item in h.config["groups"]] == ["HyVPS-morning", "HyVPS-night"]
    assert [item["cron"] for item in h.config["groups"]] == ["0 10 9 * * *", "0 10 21 * * *"]


async def test_set_cron_accepts_underscore_and_default_alias():
    h = Harness()
    h.config["groups"] = [{"name": "A", "chat_id": -1001, "message": "签到", "cron": "0 5 9 * * *"}]
    service = h.service()
    ctx = ControlContext(chat_id=-1001, sender_id=123, chat_name="A")

    await service.run("/set", ["cron", "0_10_9_*_*_*"], ctx)
    assert h.config["groups"][0]["cron"] == "0 10 9 * * *"

    await service.run("/set", ["cron", "-"], ctx)
    assert h.config["groups"][0]["cron"] == ""
