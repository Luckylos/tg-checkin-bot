import pytest

from tg_checkin.app import CheckinApp
from tg_checkin.config import load_settings_from_env, parse_accounts, parse_jobs
from tg_checkin.models import AccountSettings, AppSettings, FlowSpec, FlowStep, JobConfig


class DummyClient:
    def __init__(self, *, authorized: bool = True, fail_send: bool = False):
        self.authorized = authorized
        self.fail_send = fail_send
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.send_calls = 0
        self.handlers = []

    async def connect(self):
        self.connected = True
        self.connect_calls += 1

    async def disconnect(self):
        self.connected = False
        self.disconnect_calls += 1

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return type("Me", (), {"username": "tester", "id": 42})()

    def add_event_handler(self, handler, event):
        self.handlers.append((handler, event))

    async def send_message(self, entity, message, *, formatting_entities=None):
        self.send_calls += 1
        if self.fail_send:
            raise ConnectionError("disconnected")
        return object()


class FailingFlowRunner:
    def __init__(self):
        self.calls = 0

    async def run(self, job, entity):
        self.calls += 1
        raise ConnectionError("flow disconnected")


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


async def test_account_runtime_rebuilds_client_and_retries_single_message_on_connection_error(monkeypatch):
    first = DummyClient(fail_send=True)
    second = DummyClient()
    created = [first, second]

    async def fake_resolve_send_entity(client, chat_id):
        return "entity"

    monkeypatch.setattr("tg_checkin.runtime.create_client", lambda *args, **kwargs: created.pop(0))
    monkeypatch.setattr("tg_checkin.runtime.resolve_send_entity", fake_resolve_send_entity)

    app = CheckinApp(AppSettings(control_enabled=False))
    runtime = __import__("tg_checkin.app", fromlist=["AccountRuntime"]).AccountRuntime(
        app,
        AccountSettings(name="main", api_id=1, api_hash="hash", session_string="session"),
    )
    await runtime.start_client()

    job = JobConfig(
        name="main/Test",
        account_name="main",
        enabled=True,
        chat_id="bot_a",
        task_type="message",
        message="签到",
        parse_bot_command=False,
        cron="",
        delay_seconds=0,
        run_on_start=False,
        stagger_seconds=0,
        stagger_mode="off",
    )

    await runtime.send_job(job)

    assert first.send_calls == 1
    assert first.disconnect_calls >= 1
    assert second.connect_calls >= 1
    assert second.send_calls == 1


async def test_account_runtime_does_not_blind_retry_flow_on_connection_error(monkeypatch):
    client = DummyClient()
    monkeypatch.setattr("tg_checkin.runtime.create_client", lambda *args, **kwargs: client)

    async def fake_resolve_send_entity(current_client, chat_id):
        return "entity"

    monkeypatch.setattr("tg_checkin.runtime.resolve_send_entity", fake_resolve_send_entity)

    app = CheckinApp(AppSettings(control_enabled=False))
    runtime = __import__("tg_checkin.app", fromlist=["AccountRuntime"]).AccountRuntime(
        app,
        AccountSettings(name="main", api_id=1, api_hash="hash", session_string="session"),
    )
    await runtime.start_client()
    runtime.flow_runner = FailingFlowRunner()

    job = JobConfig(
        name="main/Flow",
        account_name="main",
        enabled=True,
        chat_id="bot_a",
        task_type="flow",
        message="",
        parse_bot_command=False,
        cron="",
        delay_seconds=0,
        run_on_start=False,
        stagger_seconds=0,
        stagger_mode="off",
        flow=FlowSpec(steps=(FlowStep(text="/start"),)),
    )

    with pytest.raises(ConnectionError, match="flow disconnected"):
        await runtime.send_job(job)

    assert runtime.flow_runner.calls == 1
    assert client.disconnect_calls == 0


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
