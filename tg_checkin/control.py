from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config import parse_jobs
from .models import DEFAULT_CRON, normalize_chat_id
from .scheduler import cron_trigger

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


class ControlService:
    def __init__(
        self,
        *,
        account_name: str,
        load_config: Callable[[], dict[str, Any]],
        save_config: Callable[[dict[str, Any]], None],
        reload_scheduler: Callable[[], Awaitable[None]],
        send_job: Callable[[Any], Awaitable[None]],
    ) -> None:
        self.account_name = account_name
        self._load_config = load_config
        self._save_config = save_config
        self._reload_scheduler = reload_scheduler
        self._send_job = send_job

    async def run(self, cmd: str, args: list[str], ctx: ControlContext) -> str:
        if cmd == "/help":
            return help_text()
        if cmd == "/id":
            return f"chat_id={ctx.chat_id}\nuser_id={ctx.sender_id}"

        config = self._load_config()
        groups = config.setdefault("groups", [])
        if not isinstance(groups, list):
            raise ValueError("config groups must be a list")

        if cmd == "/list":
            return self._list(groups, ctx.chat_id)
        if cmd == "/add":
            return await self._add(config, groups, args, ctx)
        if cmd == "/del":
            return await self._delete(config, groups, args, ctx)
        if cmd in {"/enable", "/disable"}:
            return await self._toggle(config, groups, args, ctx, enabled=(cmd == "/enable"))
        if cmd == "/set":
            return await self._set(config, groups, args, ctx)
        if cmd == "/test":
            return await self._test(config, groups, args, ctx)
        return "未知命令。"

    def _list(self, groups: list[dict[str, Any]], chat_id: int) -> str:
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

    async def _add(self, config: dict[str, Any], groups: list[dict[str, Any]], args: list[str], ctx: ControlContext) -> str:
        full_mode = len(args) >= 4 and is_chat_id(args[1])
        task_mode = (
            len(args) >= 3
            and not full_mode
            and not is_default_alias(args[0])
            and (looks_like_cron(args[1]) or is_default_alias(args[1]))
        )
        if full_mode:
            name, chat_id_raw, cron_raw = args[0], args[1], args[2]
            message = " ".join(args[3:])
            chat_id = normalize_chat_id(chat_id_raw)
            cron = DEFAULT_CRON if is_default_alias(cron_raw) else cron_raw.replace("_", " ")
            job = {
                "name": unique_name(name, groups),
                "enabled": True,
                "chat_id": chat_id,
                "message": message,
                "parse_bot_command": True,
                "cron": "" if cron == DEFAULT_CRON and is_default_alias(cron_raw) else cron,
                "run_on_start": False,
            }
            parsed_job = parse_jobs(self._jobs_config(config, [job]))[0]
            cron_trigger(parsed_job.cron, str(config.get("timezone") or "Asia/Shanghai"))
            groups.append(job)
            await self._persist(config)
            return f"已添加：{job['name']}\nchat_id={chat_id}\ncron={display_cron(str(job.get('cron') or ''))}\nmessage={message}"

        if not args:
            return "用法：群内 /add <message...> 或 /add <task> <cron|-> <message...>；完整模式 /add <name> <chat_id> <cron|-> <message...>"

        if task_mode:
            task_name = args[0]
            cron_raw = args[1]
            message = " ".join(args[2:])
            if not message:
                return "用法：/add <task> <cron|-> <message...>；message 不能为空"
            group = ensure_task_group(groups, ctx.chat_id, ctx.chat_name)
            tasks = ensure_tasks_list(group)
            task = build_task(unique_task_name(task_name, tasks), cron_raw, message)
            parsed_job = parse_jobs(self._jobs_config(config, [task_group_for_validation(group, task)]))[0]
            cron_trigger(parsed_job.cron, str(config.get("timezone") or "Asia/Shanghai"))
            tasks.append(task)
            await self._persist(config)
            return (
                f"已添加：{group.get('name')}/{task['name']}\n"
                f"chat_id={ctx.chat_id}\ncron={display_cron(str(task.get('cron') or ''))}\nmessage={message}"
            )

        name = ctx.chat_name
        chat_id = normalize_chat_id(str(ctx.chat_id))
        if is_default_alias(args[0]) or looks_like_cron(args[0]):
            cron_raw = args[0]
            message = " ".join(args[1:])
            if not message:
                return "用法：/add <cron|-> <message...>；message 不能为空"
        else:
            cron_raw = "-"
            message = " ".join(args)

        cron = DEFAULT_CRON if is_default_alias(cron_raw) else cron_raw.replace("_", " ")
        job = {
            "name": unique_name(name, groups),
            "enabled": True,
            "chat_id": chat_id,
            "message": message,
            "parse_bot_command": True,
            "cron": "" if cron == DEFAULT_CRON and is_default_alias(cron_raw) else cron,
            "run_on_start": False,
        }
        parsed_job = parse_jobs(self._jobs_config(config, [job]))[0]
        cron_trigger(parsed_job.cron, str(config.get("timezone") or "Asia/Shanghai"))
        groups.append(job)
        await self._persist(config)
        return f"已添加：{job['name']}\nchat_id={chat_id}\ncron={display_cron(str(job.get('cron') or ''))}\nmessage={message}"

    async def _delete(self, config: dict[str, Any], groups: list[dict[str, Any]], args: list[str], ctx: ControlContext) -> str:
        if len(args) > 1:
            return "用法：/del [name]"
        target = resolve_target(groups, ctx.chat_id, args[0] if args else None)
        if target.is_task:
            group = groups[target.group_index]
            tasks = ensure_tasks_list(group)
            removed = tasks.pop(target.task_index)  # type: ignore[arg-type]
            if not tasks:
                groups.pop(target.group_index)
            await self._persist(config)
            return f"已删除：{group.get('name')}/{removed.get('name')}"
        removed = groups.pop(target.group_index)
        await self._persist(config)
        return f"已删除：{removed.get('name')}"

    async def _toggle(self, config: dict[str, Any], groups: list[dict[str, Any]], args: list[str], ctx: ControlContext, *, enabled: bool) -> str:
        if len(args) > 1:
            return "用法：/enable [name]" if enabled else "用法：/disable [name]"
        target = resolve_target(groups, ctx.chat_id, args[0] if args else None)
        label = set_enabled(groups, target, enabled)
        await self._persist(config)
        return f"已{'启用' if enabled else '禁用'}：{label}"

    async def _set(self, config: dict[str, Any], groups: list[dict[str, Any]], args: list[str], ctx: ControlContext) -> str:
        if len(args) < 2:
            return "用法：/set [name] cron <expr|-> | message <text> | chat_id <id>"
        if args[0] in CONFIG_FIELDS:
            target = resolve_target(groups, ctx.chat_id, None)
            field = args[0]
            value = " ".join(args[1:])
        else:
            if len(args) < 3:
                return "用法：/set <name> cron <expr|-> | message <text> | chat_id <id>"
            target = resolve_target(groups, ctx.chat_id, args[0])
            field = args[1]
            value = " ".join(args[2:])

        label = set_field(groups, target, field, value, str(config.get("timezone") or "Asia/Shanghai"))
        await self._persist(config)
        return f"已更新：{label} {field}"

    async def _test(self, config: dict[str, Any], groups: list[dict[str, Any]], args: list[str], ctx: ControlContext) -> str:
        if len(args) > 1:
            return "用法：/test [name]"
        target = resolve_target(groups, ctx.chat_id, args[0] if args else None)
        target_name = target_job_name(groups, target)
        jobs = parse_jobs(self._jobs_config(config, groups))
        qualified_target_name = self._qualified_job_name(target_name)
        job = next((j for j in jobs if j.name == qualified_target_name), None)
        if not job:
            raise ValueError(f"任务不存在：{target_name}")
        await self._send_job(job)
        return f"已测试发送：{job.name}"

    async def _persist(self, config: dict[str, Any]) -> None:
        self._save_config(config)
        await self._reload_scheduler()

    def _jobs_config(self, config: dict[str, Any], groups: list[dict[str, Any]]) -> dict[str, Any]:
        wrapped = {
            key: value
            for key, value in config.items()
            if key
            in {
                "timezone",
                "default_delay_seconds",
                "default_cron",
                "default_stagger_seconds",
                "default_stagger_mode",
            }
        }
        wrapped["accounts"] = [{"name": self.account_name, "groups": groups}]
        return wrapped

    def _qualified_job_name(self, local_name: str) -> str:
        if local_name.startswith(f"{self.account_name}/"):
            return local_name
        return f"{self.account_name}/{local_name}"


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


def same_chat(item: dict[str, Any], chat_id: int) -> bool:
    chat_value = item.get("chat_id", item.get("chat"))
    return chat_value is not None and normalize_chat_id(chat_value) == chat_id


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
