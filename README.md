# tg-checkin-bot

Docker 环境下基于 `TG_API_ID` / `TG_API_HASH` / `TG_SESSION_STRING` 的 Telegram 用户号定时签到工具。

> 适用场景：需要模拟真实用户向群组/机器人发送签到命令，例如 `/checkin@HyVPS_Bot`。这类 BotFather 命令不能简单当作普通文本复制粘贴，本项目会为命令前缀附加 Telegram `bot_command` entity。

## 功能

- Docker Compose 部署。
- 使用用户本地生成的 `TG_SESSION_STRING`，服务器不交互输入手机号/验证码，降低敏感信息泄露风险。
- 推荐使用 `-100...` 数字 `chat_id` 作为群组唯一标识，避免群组 username 改名导致失效。
- `config/config.yml` 自由添加/删除群组、消息内容和 cron 时间。
- `cron` 支持留空；留空默认每天 `00:10`。
- 默认 cron 任务支持自动错峰发送，避免大量群组在同一秒集中签到。
- 支持通过 Telegram 消息控制添加/删除/启用/禁用/修改/测试任务。
- 自动重载配置文件。
- 支持 5 字段或 6 字段 cron。
- 对 `/command`、`/command@BotName` 自动构造 `MessageEntityBotCommand`。

## 快速开始

```bash
cd /root/tg-checkin-bot
cp .env.example .env
mkdir -p config
cp config.example.yml config/config.yml
chmod 600 .env
```

编辑 `.env`：

```env
TG_API_ID=你的_api_id
TG_API_HASH=你的_api_hash
TG_SESSION_STRING=你本地生成的_session_string
CONTROL_BOT_ENABLED=true
```

## 生成 TG_SESSION_STRING

请在你自己的本地电脑生成，不建议在服务器上输入手机号、验证码或 2FA 密码：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install telethon
python3 - <<'PY'
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input('TG_API_ID: ').strip())
api_hash = input('TG_API_HASH: ').strip()
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print('\nTG_SESSION_STRING=')
    print(client.session.save())
PY
```

把输出的 `TG_SESSION_STRING` 填入服务器 `.env`。不要把它发到聊天里，也不要提交到 Git。

## 配置任务

编辑 `config/config.yml`，添加需要签到的群组：

```yaml
timezone: Asia/Shanghai
default_delay_seconds: 3
# 可选：单个任务 cron 留空时使用该默认值；不配置则内置为每天 00:10:00。
default_cron: "0 10 0 * * *"
# cron 留空/默认的任务会在触发后按任务稳定错峰 0~1800 秒发送。
default_stagger_seconds: 1800
default_stagger_mode: stable

groups:
  - name: HyVPS
    enabled: true
    chat_id: -1003849837200
    message: /checkin@HyVPS_Bot
    parse_bot_command: true
    cron: ""   # 留空默认每天 00:10
    run_on_start: false
```

启动：

```bash
docker-compose build
docker-compose up -d
```

查看日志：

```bash
docker-compose logs -f tg-checkin
```

验证配置：

```bash
docker-compose run --rm tg-checkin python /app/app.py validate
```

## 获取 chat_id

把登录的用户号拉进目标群，并用该账号在目标群中发送控制命令：

```text
/id
```

回复会包含当前 `chat_id`。多数情况下你不需要手写它，因为群内 `/add` 会自动读取当前群名和 `chat_id`。

## Telegram 控制命令

控制命令只监听登录账号自身发出的消息。推荐直接在目标群里用该账号发送命令，程序会自动读取当前群名和 `chat_id`。

```text
/help
/id
/list
/add <message...>
/add <cron|-> <message...>
/add <name> <chat_id> <cron|-> <message...>
/del [name]
/enable [name]
/disable [name]
/set [name] cron <expr|->
/set [name] message <text>
/set [name] chat_id <id>
/test [name]
```

由于 Telegram 消息用空格分隔，控制命令里的 cron 建议用下划线代替空格，程序会自动还原。`-` 表示默认每天 `00:10`：

```text
# 在目标群内自动添加当前群
/add /checkin@HyVPS_Bot
/add 0_10_9_*_*_* /checkin@HyVPS_Bot

# 在当前群内修改/测试
/set cron 0_10_9_*_*_*
/set cron -
/test

# 也保留完整模式
/add HyVPS -1003849837200 - /checkin@HyVPS_Bot
```

## 配置说明

- `chat_id`：推荐填写 `-100...` 数字群组 ID，作为稳定唯一标识。
- `message`：要发送的签到内容。
- `parse_bot_command`：为 `true` 时，如果消息以 `/xxx` 或 `/xxx@BotName` 开头，会发送 Telegram bot command entity，而不是纯文本。
- `cron`：
  - 留空：默认每天 `00:10`
  - 5 字段：`分 时 日 月 星期`
  - 6 字段：`秒 分 时 日 月 星期`
- `run_on_start`：首次加载该任务后立即发送一次，建议仅测试时打开。
- `default_stagger_seconds`：全局默认错峰窗口，默认 `1800` 秒。cron 留空/默认的任务会在触发后按任务分散到该窗口内发送。
- `default_stagger_mode`：`stable` 为稳定错峰，同一任务每天偏移基本固定；`random` 为每次随机；`off` 为关闭。
- `stagger_seconds` / `stagger_mode`：单任务覆盖全局错峰配置。显式写了非默认 cron 的任务默认不自动错峰，可按需为单任务设置。
- `enabled`：设为 `false` 即可临时禁用。

## 注意事项

- 这是用户号自动化，需遵守 Telegram 规则以及目标群组/机器人规则，避免高频发送、垃圾信息或绕过限制。
- 群组数量较多时不要让所有任务同秒发送；保留默认错峰，或为不同任务设置不同 cron。
- `TG_SESSION_STRING` 等同于登录凭据，请妥善保护。
- `.env` 中的 `TG_API_HASH`、`TG_SESSION_STRING` 都不要公开。
- 若目标群组要求先加入群，需先用该 Telegram 账号加入目标群组。
