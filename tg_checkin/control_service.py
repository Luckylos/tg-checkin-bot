from __future__ import annotations

from typing import Any, Awaitable, Callable

from .config import parse_jobs
from .control_parse import CONFIG_FIELDS, CONTROL_COMMANDS, help_text, is_chat_id, is_default_alias, looks_like_cron
from .control_targets import (
    ControlContext,
    ResolvedTarget,
    build_task,
    ensure_task_group,
    ensure_tasks_list,
    render_task_list,
    resolve_target,
    same_chat,
    set_enabled,
    set_field,
    target_job_name,
    task_group_for_validation,
    unique_name,
    unique_task_name,
)
from .models import DEFAULT_CRON, normalize_chat_id
from .scheduler import cron_trigger


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
            return render_task_list(groups, ctx.chat_id)
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
            return f"已添加：{job['name']}\nchat_id={chat_id}\ncron={parsed_job.cron}\nmessage={message}"

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
                f"chat_id={ctx.chat_id}\ncron={parsed_job.cron}\nmessage={message}"
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
        return f"已添加：{job['name']}\nchat_id={chat_id}\ncron={parsed_job.cron}\nmessage={message}"

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
