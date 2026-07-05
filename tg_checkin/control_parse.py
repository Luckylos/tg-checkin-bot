from __future__ import annotations

from typing import Any

from .models import DEFAULT_CRON, normalize_chat_id

CONTROL_COMMANDS = {"/help", "/id", "/list", "/add", "/del", "/enable", "/disable", "/set", "/test"}
DEFAULT_ALIASES = {"-", "default", "默认"}
CONFIG_FIELDS = {"cron", "message", "chat_id"}
TASK_FIELDS = {"cron", "message", "parse_bot_command", "delay_seconds", "run_on_start", "stagger_seconds", "stagger_mode"}
GROUP_TASK_FIELDS = {"message", "parse_bot_command", "delay_seconds", "run_on_start", "stagger_seconds", "stagger_mode"}


def parse_control_command(text: str) -> tuple[str, list[str]]:
    parts = text.strip().split()
    if not parts:
        return "", []
    cmd = parts[0].split("@", 1)[0].lower()
    return cmd, parts[1:]


def help_text() -> str:
    return (
        "命令：\n"
        "/id - 显示当前 chat_id 和 user_id\n"
        "/list - 列出任务；群内使用时优先显示本群任务\n"
        "/add <message...> - 在当前群添加单任务，默认每天 00:10，且自动错峰发送\n"
        "/add <cron|-> <message...> - 当前群添加单任务并指定 cron，cron 用 _ 代替空格\n"
        "/add <task> <cron|-> <message...> - 当前群添加子任务；用于不同时间发送不同内容\n"
        "/add <name> <chat_id> <cron|-> <message...> - 完整模式\n"
        "/del [name]，群内省略 name 时删除本群任务\n"
        "/enable [name] | /disable [name]\n"
        "/set [name] cron <expr|-> | message <text> | chat_id <id>\n"
        "/test [name] - 立即发送一次"
    )


def is_chat_id(value: Any) -> bool:
    try:
        normalize_chat_id(value)
    except ValueError:
        return False
    return True


def is_default_alias(value: str) -> bool:
    return value in DEFAULT_ALIASES


def looks_like_cron(value: str) -> bool:
    return len(value.replace("_", " ").split()) in {5, 6}


def display_cron(value: str) -> str:
    return value or DEFAULT_CRON
