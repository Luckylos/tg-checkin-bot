import pytest
from telethon.tl.types import MessageEntityBotCommand

from tg_checkin.config import load_config, parse_jobs, save_config
from tg_checkin.control import parse_control_command
from tg_checkin.models import DEFAULT_CRON, normalize_chat_id
from tg_checkin.scheduler import cron_trigger, stable_stagger_offset
from tg_checkin.telegram import command_entities


def test_parse_jobs_defaults_and_stagger_behavior():
    cfg = {
        "default_cron": DEFAULT_CRON,
        "default_delay_seconds": 7,
        "default_stagger_seconds": 1800,
        "default_stagger_mode": "stable",
        "groups": [
            {"name": "default-empty", "chat_id": "-1001", "message": "/checkin@Bot", "cron": ""},
            {"name": "explicit-default", "chat_id": -1002, "message": "签到", "cron": DEFAULT_CRON},
            {"name": "custom", "chat_id": -1003, "message": "hello", "cron": "0 5 9 * * *"},
            {"name": "off", "chat_id": -1004, "message": "hello", "cron": "", "stagger_mode": "off"},
        ],
    }

    jobs = parse_jobs(cfg)

    assert jobs[0].cron == DEFAULT_CRON
    assert jobs[0].delay_seconds == 7
    assert jobs[0].stagger_seconds == 1800
    assert jobs[1].stagger_seconds == 1800
    assert jobs[2].stagger_seconds == 0
    assert jobs[3].stagger_seconds == 0


def test_parse_jobs_supports_group_tasks_for_different_messages_at_different_times():
    jobs = parse_jobs(
        {
            "default_delay_seconds": 5,
            "groups": [
                {
                    "name": "HyVPS",
                    "enabled": True,
                    "chat_id": "-1003849837200",
                    "parse_bot_command": True,
                    "tasks": [
                        {"name": "morning", "cron": "0 10 9 * * *", "message": "/checkin@HyVPS_Bot"},
                        {"name": "night", "cron": "0 10 21 * * *", "message": "/sign@OtherBot", "enabled": False},
                    ],
                }
            ],
        }
    )

    assert [job.name for job in jobs] == ["HyVPS/morning", "HyVPS/night"]
    assert {job.chat_id for job in jobs} == {-1003849837200}
    assert [job.cron for job in jobs] == ["0 10 9 * * *", "0 10 21 * * *"]
    assert [job.message for job in jobs] == ["/checkin@HyVPS_Bot", "/sign@OtherBot"]
    assert jobs[0].enabled is True
    assert jobs[1].enabled is False
    assert jobs[0].delay_seconds == 5


def test_parse_jobs_allows_group_message_as_task_default():
    jobs = parse_jobs(
        {
            "groups": [
                {
                    "name": "HyVPS",
                    "chat_id": -1001,
                    "message": "/default@Bot",
                    "tasks": [
                        {"name": "default", "cron": "0 10 9 * * *"},
                        {"name": "override", "cron": "0 10 21 * * *", "message": "签到"},
                    ],
                }
            ]
        }
    )

    assert [job.message for job in jobs] == ["/default@Bot", "签到"]


def test_parse_jobs_rejects_duplicate_job_names():
    with pytest.raises(ValueError, match="duplicate job name"):
        parse_jobs(
            {
                "groups": [
                    {"name": "HyVPS", "chat_id": -1001, "message": "签到", "tasks": [{"name": "morning"}, {"name": "morning"}]}
                ]
            }
        )


def test_stable_stagger_offset_is_deterministic_and_bounded():
    job = parse_jobs(
        {
            "default_stagger_seconds": 1800,
            "groups": [{"name": "HyVPS", "chat_id": -1001, "message": "/checkin@Bot", "cron": ""}],
        }
    )[0]

    first = stable_stagger_offset(job)
    second = stable_stagger_offset(job)

    assert first == second
    assert 0 <= first <= 1800


def test_normalize_chat_id_rejects_bool_and_text():
    assert normalize_chat_id("-1003849837200") == -1003849837200
    assert normalize_chat_id(-1003849837200) == -1003849837200
    with pytest.raises(ValueError):
        normalize_chat_id(True)
    with pytest.raises(ValueError):
        normalize_chat_id("@group")


def test_cron_trigger_accepts_five_and_six_fields():
    assert cron_trigger("10 0 * * *", "Asia/Shanghai")
    assert cron_trigger("0 10 0 * * *", "Asia/Shanghai")
    with pytest.raises(ValueError):
        cron_trigger("0 10 0", "Asia/Shanghai")


def test_command_entities_only_for_bot_commands():
    entities = command_entities("/checkin@HyVPS_Bot", True)
    assert entities
    assert isinstance(entities[0], MessageEntityBotCommand)
    assert entities[0].offset == 0
    assert entities[0].length == len("/checkin@HyVPS_Bot")
    assert command_entities("签到", True) is None
    assert command_entities("/checkin", False) is None


def test_parse_control_command_strips_bot_suffix():
    assert parse_control_command("/add@SomeBot - /checkin@HyVPS_Bot") == (
        "/add",
        ["-", "/checkin@HyVPS_Bot"],
    )
    assert parse_control_command("   ") == ("", [])


def test_load_and_save_config_atomic(tmp_path):
    path = tmp_path / "config.yml"
    save_config(str(path), {"timezone": "Asia/Shanghai", "groups": []})

    assert load_config(str(path)) == {"timezone": "Asia/Shanghai", "groups": []}
    assert not (tmp_path / ".config.yml.tmp").exists()
