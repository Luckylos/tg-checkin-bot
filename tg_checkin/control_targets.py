from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .control_parse import CONFIG_FIELDS, DEFAULT_ALIASES, GROUP_TASK_FIELDS, TASK_FIELDS, display_cron, is_default_alias
from .models import DEFAULT_CRON, normalize_chat_id
from .scheduler import cron_trigger


@dataclass(frozen=True)
class ControlContext:
    chat_id: int
    sender_id: int | None
    chat_name: str


@dataclass(frozen=True)
class ResolvedTarget:
    group_index: int
    task_index: int | None = None

    @property
    def is_task(self) -> bool:
        return self.task_index is not None


def same_chat(item: dict[str, Any], chat_id: int) -> bool:
    chat_value = item.get("chat_id", item.get("chat"))
    return chat_value is not None and normalize_chat_id(chat_value) == chat_id


def unique_name(name: str, groups: list[dict[str, Any]]) -> str:
    existing_names = {str(item.get("name")) for item in groups}
    base_name = name
    suffix = 2
    while name in existing_names:
        name = f"{base_name}-{suffix}"
        suffix += 1
    return name


def unique_task_name(name: str, tasks: list[dict[str, Any]]) -> str:
    existing_names = {str(item.get("name")) for item in tasks}
    base_name = name
    suffix = 2
    while name in existing_names:
        name = f"{base_name}-{suffix}"
        suffix += 1
    return name


def build_task(name: str, cron_raw: str, message: str) -> dict[str, Any]:
    cron = DEFAULT_CRON if is_default_alias(cron_raw) else cron_raw.replace("_", " ")
    return {
        "name": name,
        "cron": "" if cron == DEFAULT_CRON and is_default_alias(cron_raw) else cron,
        "message": message,
        "run_on_start": False,
    }


def ensure_tasks_list(group: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = group.setdefault("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError(f"{group.get('name')}: tasks must be a list")
    return tasks


def ensure_task_group(groups: list[dict[str, Any]], chat_id: int, chat_name: str) -> dict[str, Any]:
    matches = [item for item in groups if same_chat(item, chat_id)]
    if matches:
        group = matches[0]
        if "tasks" not in group:
            task = build_task("default", str(group.pop("cron", "")), str(group.pop("message")))
            task["run_on_start"] = bool(group.pop("run_on_start", False))
            group["tasks"] = [task]
        ensure_tasks_list(group)
        return group
    group = {
        "name": unique_name(chat_name, groups),
        "enabled": True,
        "chat_id": normalize_chat_id(chat_id),
        "parse_bot_command": True,
        "tasks": [],
    }
    groups.append(group)
    return group


def task_group_for_validation(group: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    ensure_tasks_list(group)
    task = dict(task)
    task["parse_bot_command"] = bool(task.get("parse_bot_command", group.get("parse_bot_command", True)))
    validation_group = {
        key: value for key, value in group.items() if key in {"name", "enabled", "chat_id", "chat", *GROUP_TASK_FIELDS}
    }
    validation_group["tasks"] = [task]
    return validation_group


def resolve_target(groups: list[dict[str, Any]], chat_id: int, optional_name: str | None) -> ResolvedTarget:
    if optional_name:
        name = str(optional_name)
        scoped_matches: list[ResolvedTarget] = []
        global_matches: list[ResolvedTarget] = []
        for group_index, group in enumerate(groups):
            group_name = str(group.get("name"))
            if group_name == name:
                target = ResolvedTarget(group_index)
                (scoped_matches if same_chat(group, chat_id) else global_matches).append(target)
            tasks = group.get("tasks")
            if isinstance(tasks, list):
                for task_index, task in enumerate(tasks):
                    if not isinstance(task, dict):
                        continue
                    task_name = str(task.get("name"))
                    if name in {task_name, f"{group_name}/{task_name}"}:
                        target = ResolvedTarget(group_index, task_index)
                        (scoped_matches if same_chat(group, chat_id) else global_matches).append(target)
        matches = scoped_matches or global_matches
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(target_job_name(groups, target) for target in matches)
            raise ValueError(f"匹配到多个任务，请指定完整 name：{names}")
        raise ValueError(f"任务不存在：{optional_name}")

    matches: list[ResolvedTarget] = []
    for group_index, group in enumerate(groups):
        if not same_chat(group, chat_id):
            continue
        tasks = group.get("tasks")
        if isinstance(tasks, list):
            for task_index, task in enumerate(tasks):
                if isinstance(task, dict):
                    matches.append(ResolvedTarget(group_index, task_index))
        else:
            matches.append(ResolvedTarget(group_index))
    if not matches:
        raise ValueError(f"当前群未配置任务：chat_id={chat_id}")
    if len(matches) > 1:
        names = ", ".join(target_job_name(groups, target) for target in matches)
        raise ValueError(f"当前群匹配到多个任务，请指定 name：{names}")
    return matches[0]


def target_job_name(groups: list[dict[str, Any]], target: ResolvedTarget) -> str:
    group = groups[target.group_index]
    group_name = str(group.get("name"))
    if target.task_index is None:
        return group_name
    tasks = ensure_tasks_list(group)
    task = tasks[target.task_index]
    return f"{group_name}/{task.get('name')}"


def set_enabled(groups: list[dict[str, Any]], target: ResolvedTarget, enabled: bool) -> str:
    if target.task_index is None:
        groups[target.group_index]["enabled"] = enabled
    else:
        ensure_tasks_list(groups[target.group_index])[target.task_index]["enabled"] = enabled
    return target_job_name(groups, target)


def set_field(groups: list[dict[str, Any]], target: ResolvedTarget, field: str, value: str, timezone: str) -> str:
    group = groups[target.group_index]
    if target.task_index is None:
        if field not in CONFIG_FIELDS:
            raise ValueError("字段只支持：cron、message、chat_id")
        holder = group
    else:
        if field == "chat_id":
            holder = group
        elif field in TASK_FIELDS:
            holder = ensure_tasks_list(group)[target.task_index]
        else:
            raise ValueError("字段只支持：cron、message、chat_id")

    if field == "cron":
        if is_default_alias(value) or value == "":
            value = ""
            effective_cron = DEFAULT_CRON
        else:
            value = value.replace("_", " ")
            effective_cron = value
        cron_trigger(effective_cron, timezone)
        holder["cron"] = value
    elif field == "message":
        holder["message"] = value
    elif field == "chat_id":
        group["chat_id"] = normalize_chat_id(value)
        group.pop("chat", None)
    else:
        raise ValueError("字段只支持：cron、message、chat_id")
    return target_job_name(groups, target)


def render_task_list(groups: list[dict[str, Any]], chat_id: int) -> str:
    if not groups:
        return "暂无任务。"
    scoped = [item for item in groups if same_chat(item, chat_id)]
    show_groups = scoped or groups
    header = "本群任务：" if scoped else "任务列表："
    lines = []
    for item in show_groups:
        state = "启用" if item.get("enabled", True) else "禁用"
        tasks = item.get("tasks")
        if isinstance(tasks, list):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                task_state = "启用" if item.get("enabled", True) and task.get("enabled", True) else "禁用"
                lines.append(
                    f"- {item.get('name')}/{task.get('name')}: {task_state}, "
                    f"chat_id={item.get('chat_id', item.get('chat'))}, "
                    f"cron={display_cron(str(task.get('cron') or item.get('cron') or ''))}, "
                    f"msg={task.get('message', item.get('message'))}"
                )
        else:
            lines.append(
                f"- {item.get('name')}: {state}, "
                f"chat_id={item.get('chat_id', item.get('chat'))}, "
                f"cron={display_cron(str(item.get('cron') or ''))}, "
                f"msg={item.get('message')}"
            )
    return header + "\n" + "\n".join(lines)
