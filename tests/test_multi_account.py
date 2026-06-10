import pytest

from tg_checkin.app import CheckinApp
from tg_checkin.config import load_settings_from_env, parse_accounts, parse_jobs
from tg_checkin.models import AppSettings


def test_parse_jobs_supports_multiple_accounts_with_isolated_names():
    jobs = parse_jobs(
        {
            "accounts": [
                {
                    "name": "alice",
                    "groups": [
                        {
                            "name": "BotA",
                            "chat_id": "bot_a",
                            "tasks": [
                                {"name": "daily", "cron": "0 1 9 * * *", "message": "/checkin"},
                                {"name": "night", "cron": "0 1 21 * * *", "message": "签到"},
                            ],
                        }
                    ],
                },
                {"name": "bob", "groups": [{"name": "BotA", "chat_id": "bot_a", "cron": "0 2 9 * * *", "message": "/checkin"}]},
            ]
        }
    )

    assert [job.name for job in jobs] == ["alice/BotA/daily", "alice/BotA/night", "bob/BotA"]
    assert [job.account_name for job in jobs] == ["alice", "alice", "bob"]
    assert [job.chat_id for job in jobs] == ["bot_a", "bot_a", "bot_a"]


def test_parse_jobs_rejects_duplicate_account_names():
    config = {"accounts": [{"name": "same", "groups": []}, {"name": "same", "groups": []}]}
    with pytest.raises(ValueError, match="duplicate account name"):
        parse_jobs(config)
    with pytest.raises(ValueError, match="duplicate account name"):
        parse_accounts(config, require_secrets=False)


def test_parse_accounts_can_load_account_secrets_from_env(monkeypatch):
    monkeypatch.setenv("ALICE_API_ID", "12345")
    monkeypatch.setenv("ALICE_API_HASH", "hash")
    monkeypatch.setenv("ALICE_SESSION_STRING", "session")

    accounts = parse_accounts({"accounts": [{"name": "alice", "env_prefix": "ALICE", "groups": []}]})

    assert len(accounts) == 1
    assert accounts[0].name == "alice"
    assert accounts[0].api_id == 12345
    assert accounts[0].api_hash == "hash"
    assert accounts[0].session_string == "session"


def test_account_control_config_roundtrip_updates_only_that_account():
    app = CheckinApp(AppSettings(config_path="/unused"))
    full = {
        "timezone": "Asia/Shanghai",
        "accounts": [
            {"name": "alice", "groups": [{"name": "A", "chat_id": "bot_a", "message": "a"}]},
            {"name": "bob", "groups": [{"name": "B", "chat_id": "bot_b", "message": "b"}]},
        ],
    }

    sub = app.extract_account_config(full, "alice")
    assert sub["groups"][0]["name"] == "A"
    sub["groups"] = [{"name": "A2", "chat_id": "bot_a", "message": "changed"}]
    updated = app.replace_account_config(full, "alice", sub)

    assert updated["accounts"][0]["groups"] == [{"name": "A2", "chat_id": "bot_a", "message": "changed"}]
    assert updated["accounts"][1]["groups"] == [{"name": "B", "chat_id": "bot_b", "message": "b"}]
