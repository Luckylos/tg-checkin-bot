# tg-checkin-bot

Telegram 用户号定时签到 / bot 菜单流程自动化工具。

- 使用 **Telethon 用户号会话**，不是 Bot API。
- 一个容器可同时运行多个账号。
- 所有任务按 `account / group / task` 隔离命名。
- 同一账号同一 `chat_id` 自动串行，避免 ReplyKeyboard/菜单上下文串线。
- 不保留旧版顶层 `groups:` 配置；即使只有一个账号，也必须写在 `accounts:` 下。

## 适合场景

- 每天定时向群组或 bot 发送固定签到消息。
- 同一 bot/群在不同时间发送不同内容。
- 按 bot 回复菜单一步步发送按钮文本，例如 `/start → 积分商城 → 商品 → 确认兑换`。
- 对低频兑换/领取流程做有限重复：命中成功、上限、失败规则后提前停止。

不适合验证码、支付确认、人机验证、绕过风控、高频刷屏或需要真正 InlineKeyboard callback 的流程。当前 `click` 语义是：从上一条 bot 回复的按钮文本里按子串匹配，发送匹配到的完整文本。

## 快速部署

```bash
git clone https://github.com/Luckylos/tg-checkin-bot.git
cd tg-checkin-bot
cp .env.example .env
mkdir -p config
cp config.example.yml config/config.yml
chmod 600 .env config/config.yml
```

编辑 `.env`，至少填一个账号：

```env
MAIN_API_ID=123456
MAIN_API_HASH=你的_api_hash
MAIN_SESSION_STRING=你的_session_string
CONFIG_PATH=/config/config.yml
CONFIG_RELOAD_SECONDS=60
CONTROL_BOT_ENABLED=true
LOG_LEVEL=INFO
```

启动与验证：

```bash
docker compose build
docker compose run --rm --no-deps tg-checkin python /app/app.py validate /config/config.yml
docker compose up -d
docker compose logs --tail=80 tg-checkin
```

## 生成 SESSION_STRING

建议在本地可信机器生成，不要在服务器或公开聊天里输入手机号、验证码、2FA 密码。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install telethon
python3 - <<'PY'
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input('API_ID: ').strip())
api_hash = input('API_HASH: ').strip()
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print('\nSESSION_STRING=')
    print(client.session.save())
PY
```

把输出填入 `.env` 中对应账号的 `*_SESSION_STRING`。

## 配置结构

所有任务都在 `accounts:` 下：

```yaml
timezone: Asia/Shanghai
default_delay_seconds: 3
default_cron: "0 10 0 * * *"
default_stagger_seconds: 1800
default_stagger_mode: stable

accounts:
  - name: main
    enabled: true
    env_prefix: MAIN
    groups:
      - name: HyVPS
        enabled: true
        chat_id: -1003849837200
        message: /checkin@HyVPS_Bot
        parse_bot_command: true
        cron: ""
        run_on_start: false
```

字段要点：

- `accounts[].name`：稳定账号名，不允许包含 `/`。
- `accounts[].env_prefix`：凭据变量前缀，例如 `MAIN_API_ID`、`MAIN_API_HASH`、`MAIN_SESSION_STRING`。
- `groups[].chat_id`：群推荐 `-100...` 数字 ID；bot/用户私聊可用 username，如 `freexzteam_bot`。
- `cron`：支持 5 或 6 字段；空字符串表示使用 `default_cron`。
- `parse_bot_command`：为 `true` 时，`/checkin@Bot` 会作为 Telegram bot command entity 发送。
- 实际 job 名：
  - 单任务 group：`main/HyVPS`
  - group 内 task：`main/HyVPS/morning`

## 同一目标多个任务

```yaml
accounts:
  - name: main
    env_prefix: MAIN
    groups:
      - name: HyVPS
        chat_id: -1003849837200
        parse_bot_command: true
        tasks:
          - name: morning
            cron: "0 10 9 * * *"
            message: /checkin@HyVPS_Bot
          - name: night
            cron: "0 10 21 * * *"
            message: /sign@OtherBot
```

## 多账号

```env
MAIN_API_ID=123456
MAIN_API_HASH=...
MAIN_SESSION_STRING=...
SECOND_API_ID=123456
SECOND_API_HASH=...
SECOND_SESSION_STRING=...
```

```yaml
accounts:
  - name: main
    env_prefix: MAIN
    groups:
      - name: 公益Plus
        chat_id: freexzteam_bot
        tasks:
          - name: 签到
            cron: ""
            message: "📅 每日签到"

  - name: second
    env_prefix: SECOND
    groups:
      - name: HyVPS
        chat_id: -1003849837200
        message: /checkin@HyVPS_Bot
        cron: "0 10 9 * * *"
```

不同账号可并行；同一账号同一 `chat_id` 串行。

### 从多个容器整合到一个容器

推荐把多个同构签到实例合并成一个 Compose 服务，用多个 account 承载不同 Telegram 用户号：

```env
ACC1_API_ID=123456
ACC1_API_HASH=...
ACC1_SESSION_STRING=...
ACC2_API_ID=123456
ACC2_API_HASH=...
ACC2_SESSION_STRING=...
ACC3_API_ID=123456
ACC3_API_HASH=...
ACC3_SESSION_STRING=...
```

```yaml
accounts:
  - name: main
    env_prefix: ACC1
    groups: []
  - name: account2
    env_prefix: ACC2
    groups: []
  - name: account3
    env_prefix: ACC3
    groups: []
```

迁移原则：

- 先备份每个旧实例的 `.env`、`config/config.yml`、`docker-compose.yml`。
- 把旧实例的 `groups` 原样移动到对应 `accounts[].groups`。
- 只改 env 变量名前缀，不改 session 字符串内容。
- 合并后先运行 `python /app/app.py validate /config/config.yml`，确认 job 数量符合预期。
- 新单容器启动并确认 3 个账号都 `authorized account=...` 后，再停用旧实例。
- 停用旧实例建议先 `systemctl disable --now ...` 并保留目录，不要立即删除，便于回滚。

资源配置按账号数和任务量调整。示例：3 个低频账号可从 3 个 `0.50 CPU / 256m` 实例合并为 1 个 `1.00 CPU / 768m` 容器；实际以日志、内存和 FloodWait 情况为准。

## flow 菜单状态机

普通签到用 `message`；需要菜单上下文的流程用 `flow`，不要拆成多个独立定时消息。

```yaml
accounts:
  - name: main
    env_prefix: MAIN
    groups:
      - name: 公益Plus
        chat_id: freexzteam_bot
        tasks:
          - name: 每日签到
            cron: ""
            message: "📅 每日签到"

          - name: plus兑换
            cron: "0 0 0 * * *"
            flow:
              repeat:
                count: 50
                interval_seconds: 3
                jitter_seconds: 1
                stop_on_success: true
                max_runtime_seconds: 300
              rules:
                abort_on_text:
                  - "今日 Plus 成品号上限已满，明天再来"
                  - "明天再来"
                success_on_text:
                  - "兑换成功"
                  - "领取成功"
                retry_on_text:
                  - "库存不足"
                  - "请稍后再试"
                unknown_policy: retry
                max_unknown_replies: 3
              steps:
                - action: send
                  text: "/start"
                  expect_any:
                    text: ["积分商城"]
                    buttons: ["积分商城"]
                - action: click
                  button: "积分商城"
                  expect_any:
                    text: ["Plus 成品号"]
                    buttons: ["Plus 成品号"]
                - action: click
                  button: "成品号"
                  expect_any:
                    text: ["确认兑换", "今日 Plus 成品号上限已满，明天再来"]
                    buttons: ["确认兑换"]
                - action: click
                  button: "✅ 确认兑换"
                  expect_any:
                    text: ["兑换成功", "今日 Plus 成品号上限已满，明天再来", "库存不足"]
```

规则优先级：

1. `abort_on_text`
2. `success_on_text`
3. `retry_on_text`
4. unknown reply，根据 `unknown_policy` 和 `max_unknown_replies` 处理

`repeat.count` 是最大尝试轮数，不是必须成功次数。

## 控制命令

开启 `CONTROL_BOT_ENABLED=true` 后，登录账号自己发出的 outgoing 命令会被处理。多账号模式下，每个账号只修改自己的 `groups`。

- `/id`：显示当前 chat/user id
- `/list`：列出任务；群内优先显示本群任务
- `/add <message...>`：当前群添加单任务，默认每天 00:10 并错峰
- `/add <cron|-> <message...>`：当前群添加单任务并指定 cron，cron 用 `_` 代替空格
- `/add <task> <cron|-> <message...>`：当前群添加子任务
- `/add <name> <chat_id> <cron|-> <message...>`：完整模式
- `/del [name]`
- `/enable [name]` / `/disable [name]`
- `/set [name] cron <expr|-> | message <text> | chat_id <id>`
- `/test [name]`

注意：不要对含真实确认/消费步骤的 flow 直接 `/test`，除非你明确接受该副作用。

## 本地开发与验证

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m py_compile tg_checkin/*.py app.py
pytest tests -q
python app.py validate config.example.yml
docker compose config -q
```

## 运行日志

关键日志：

- `authorized account=<name> as <user>`
- `control bot enabled for account=<name>`
- `scheduled account=<name> job=<job>`
- `config loaded: N enabled jobs`
- `scheduler started accounts=<names>`

## 安全

- `*_SESSION_STRING` 等同登录态，不要提交、转发或写入公开文档。
- `.env` 和 `config/config.yml` 建议 `0600`。
- 对兑换/领取/消费类 flow，先用 `repeat.count: 1` 且停止在确认前一步验证路径，再加入最终确认步骤。
