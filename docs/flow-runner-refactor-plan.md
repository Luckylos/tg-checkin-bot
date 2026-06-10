# Flow Runner Current Design

本文件记录当前重构后的实现约束；历史兼容方案已移除。

## 配置入口

- 根配置必须使用 `accounts:`。
- 即使只有一个 Telegram 用户号，也写成一个 account。
- 凭据通过 `env_prefix` 读取：`<PREFIX>_API_ID`、`<PREFIX>_API_HASH`、`<PREFIX>_SESSION_STRING`。
- 顶层 `groups:` 不再是有效配置。

## 任务模型

- `account`：一个 Telethon 用户号和它自己的任务集合。
- `group`：一个 Telegram 目标会话，字段为 `name`、`chat_id`、`tasks` 或单任务字段。
- `task`：普通 `message` 或 `flow`。
- 运行时 job 名固定展开为：
  - `account/group`
  - `account/group/task`

## 并发模型

- 每个 account 启动一个 Telethon client。
- 不同 account 可并发。
- 同一 account + 同一 `chat_id` 使用 `asyncio.Lock` 串行执行，避免菜单上下文串线。

## flow 模型

- `flow` 必须是结构化 mapping，包含非空 `steps`。
- `steps[].action` 支持：`send`、`click`、`wait`。
- `click` 不是真正 callback click，而是：
  1. 读取上一步 bot 回复的按钮文本；
  2. 用配置的 `button` 做子串匹配；
  3. 匹配成功则发送完整按钮文本，否则发送配置值。
- `repeat.count` 是最大尝试次数，不是必须完成次数。
- 回复分类优先级：`abort_on_text > success_on_text > retry_on_text > unknown`。

## 验证门禁

```bash
python -m py_compile tg_checkin/*.py app.py
pytest tests -q
python app.py validate config.example.yml
docker compose config -q
```

## 安全边界

- 不在 README、示例配置、日志摘要、提交记录或最终回复中输出 session string/API hash。
- `/test` 可触发真实发送；含兑换确认步骤的 flow 不应在未明确接受副作用前测试。
