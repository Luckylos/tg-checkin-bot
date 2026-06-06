# tg-checkin-bot

Telegram 用户号定时签到 / 按钮式机器人流程自动化工具。

它适合两类任务：

- **普通签到**：每天向群组或 bot 发送一条消息，例如 `/checkin@HyVPS_Bot`、`签到`、`📅 每日签到`。
- **按钮式流程**：按菜单一步步触发 bot，例如 `/start → 积分商城 → 商品 → 确认兑换`。

项目使用 Telethon 用户号会话，不是 Bot API。请遵守 Telegram 和目标 bot/群组规则，避免高频发送、垃圾信息或绕过限制。

## 目录

- [核心概念](#核心概念)
- [快速部署](#快速部署)
- [生成 TG_SESSION_STRING](#生成-tg_session_string)
- [配置示例 1：普通单任务签到](#配置示例-1普通单任务签到)
- [配置示例 2：同一目标多个时间段](#配置示例-2同一目标多个时间段)
- [配置示例 3：按钮式 flow 状态机](#配置示例-3按钮式-flow-状态机)
- [配置字段说明](#配置字段说明)
- [控制命令](#控制命令)
- [验证、日志和排错](#验证日志和排错)
- [更新部署和回滚](#更新部署和回滚)
- [项目结构](#项目结构)
- [安全注意事项](#安全注意事项)

## 核心概念

### 1. 普通 message 任务

普通任务只发一条消息，适合签到：

```yaml
- name: 每日签到
  cron: ""
  message: "📅 每日签到"
```

`cron: ""` 表示使用全局默认时间，默认每天 `00:10:00`。

### 2. flow 状态机任务

flow 任务适合需要按菜单走上下文的 bot：

```yaml
- name: plus兑换
  cron: "0 0 0 * * *"
  flow:
    - send: "/start"
      expect: "积分商城"
    - send: "🛍️ 积分商城"
      expect: "Plus 成品号"
    - send: "💎 Plus 成品号(PP渠道) · 3积分"
      expect: "确认兑换"
    - send: "✅ 确认兑换"
      expect_any: ["兑换", "上限", "积分不足"]
```

运行器每一步都会：

1. 发送 `send` 文本；
2. 等待 bot 回复；
3. 在回复正文和按钮文本中查找 `expect` / `expect_any`；
4. 不符合预期就停止，避免上下文丢失后继续误发。

### 3. message 和 flow 可以共存

同一个 bot 可以同时配置每日签到和兑换 flow：

```yaml
tasks:
  - name: 签到
    cron: ""
    message: "📅 每日签到"
  - name: plus兑换
    cron: "0 0 0 * * *"
    flow:
      - send: "/start"
        expect: "积分商城"
```

普通签到不会因为新增 flow 丢失。

## 快速部署

以下以 Docker Compose 部署为例。

```bash
git clone https://github.com/Luckylos/tg-checkin-bot.git
cd tg-checkin-bot

cp .env.example .env
mkdir -p config
cp config.example.yml config/config.yml
chmod 600 .env config/config.yml
```

编辑 `.env`：

```env
TG_API_ID=123456
TG_API_HASH=你的_api_hash
TG_SESSION_STRING=你的_session_string
CONFIG_PATH=/config/config.yml
CONFIG_RELOAD_SECONDS=60
CONTROL_BOT_ENABLED=true
LOG_LEVEL=INFO
```

启动：

```bash
docker compose build
docker compose up -d
```

验证：

```bash
docker compose run --rm --no-deps tg-checkin python /app/app.py validate /config/config.yml
docker compose logs --tail=80 tg-checkin
```

## 生成 TG_SESSION_STRING

建议在你自己的本地电脑生成，不要在服务器或公开聊天中输入手机号、验证码、2FA 密码。

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

把输出填入 `.env` 的 `TG_SESSION_STRING`。

> `TG_SESSION_STRING` 等同登录态。不要提交到 Git，不要发到群里；泄露后应在 Telegram 里注销该会话并重新生成。

## 配置示例 1：普通单任务签到

适合每天给群组或 bot 发一条签到消息。

```yaml
timezone: Asia/Shanghai
default_delay_seconds: 3
default_cron: "0 10 0 * * *"
default_stagger_seconds: 1800
default_stagger_mode: stable

groups:
  - name: HyVPS
    enabled: true
    chat_id: -1003849837200
    message: /checkin@HyVPS_Bot
    parse_bot_command: true
    cron: ""
    run_on_start: false
```

说明：

- `chat_id`：群组推荐使用 `-100...` 数字 ID。
- `parse_bot_command: true`：让 `/checkin@HyVPS_Bot` 作为 Telegram bot command entity 发送，而不是普通文本。
- `cron: ""`：使用 `default_cron`，即每天 `00:10:00`。
- `default_stagger_seconds: 1800`：默认任务会稳定错峰 0～1800 秒，避免多个群同秒发送。

## 配置示例 2：同一目标多个时间段

适合同一个群或 bot 在不同时间发送不同内容。

```yaml
groups:
  - name: HyVPS 多时间段
    enabled: true
    chat_id: -1003849837200
    parse_bot_command: true
    tasks:
      - name: morning
        cron: "0 10 9 * * *"
        message: /checkin@HyVPS_Bot
      - name: night
        cron: "0 10 21 * * *"
        message: /sign@OtherBot
      - name: text-message
        cron: "0 0 23 * * *"
        message: "签到"
        parse_bot_command: false
```

任务会展开为：

- `HyVPS 多时间段/morning`
- `HyVPS 多时间段/night`
- `HyVPS 多时间段/text-message`

控制命令里如果要指定子任务，可使用完整名或当前群内的短任务名。

## 配置示例 3：按钮式 flow 状态机

适合 bot 底部菜单 / ReplyKeyboard 场景。点击按钮本质上通常是发送完全匹配的按钮文本。

```yaml
groups:
  - name: 公益Plus/Team机器人
    enabled: true
    chat_id: freexzteam_bot
    parse_bot_command: true
    tasks:
      - name: 签到
        cron: ""
        message: "📅 每日签到"
        run_on_start: false

      - name: PP_PLUS_FLOW_1
        cron: "0 0 0 * * *"
        run_on_start: false
        flow:
          mode: auto
          repeat:
            count: 50
            interval_seconds: 3
            jitter_seconds: 1
            stop_on_success: true
            max_runtime_seconds: 300
          rules:
            abort_on_text:
              - "今日 Plus 成品号上限已满，明天再来"
              - "今日兑换上限已满"
              - "明天再来"
            success_on_text:
              - "兑换成功"
              - "领取成功"
              - "已为你生成"
            retry_on_text:
              - "库存不足"
              - "请稍后再试"
              - "当前繁忙"
              - "排队中"
            unknown_policy: retry
            max_unknown_replies: 3
          steps:
            - action: send
              text: "/start"
              expect_any:
                text:
                  - "积分商城"
                buttons:
                  - "积分商城"
              timeout_seconds: 20
            - action: click
              button: "🛍️ 积分商城"
              expect_any:
                text:
                  - "Plus 成品号"
                buttons:
                  - "Plus 成品号"
              timeout_seconds: 20
            - action: click
              button: "💎 Plus 成品号(PP渠道) · 3积分"
              expect_any:
                text:
                  - "确认兑换"
                  - "今日 Plus 成品号上限已满，明天再来"
                buttons:
                  - "确认兑换"
              timeout_seconds: 20
            - action: click
              button: "✅ 确认兑换"
              expect_any:
                text:
                  - "兑换成功"
                  - "今日 Plus 成品号上限已满，明天再来"
                  - "库存不足"
              timeout_seconds: 25
```

注意：

- `repeat.count` 是最大尝试轮数，不是必须跑满；成功、终止文本或 unknown 熔断都可能提前停止。
- `abort_on_text` 优先级最高；例如命中 `今日 Plus 成品号上限已满，明天再来` 后会立即停止整个 flow。
- `retry_on_text` 只结束当前轮，等待 `interval_seconds + jitter_seconds` 后进入下一轮。
- 按钮文本要完全一致，emoji 变体也可能影响识别，例如 `🛍` 和 `🛍️` 不是同一个字符串。
- `expect` / `expect_any` 可以匹配回复正文，也可以匹配 bot 返回的按钮文本。
- 如果只想先演练，不要配置最后的 `✅ 确认兑换` 步骤。

## 配置字段说明

### 顶层字段

- `timezone`：调度时区，默认建议 `Asia/Shanghai`。
- `default_delay_seconds`：任务完成后的延迟，默认 `3` 秒。
- `default_cron`：任务 `cron` 留空时使用，默认 `0 10 0 * * *`。
- `default_stagger_seconds`：默认错峰窗口，默认 `1800` 秒。
- `default_stagger_mode`：`stable` / `random` / `off`。

### group 字段

- `name`：任务组名称。日志和控制命令会使用它。
- `enabled`：是否启用。
- `chat_id`：目标群、频道、用户或 bot。
  - 群组推荐 `-100...` 数字 ID。
  - bot / 用户私聊可用 username，例如 `freexzteam_bot` 或 `@freexzteam_bot`。
- `message`：普通单消息任务内容。
- `flow`：按钮式状态机步骤。
- `tasks`：多个子任务列表。
- `parse_bot_command`：是否为 `/command` 自动构造 Telegram bot command entity。
- `cron`：调度时间。
- `run_on_start`：服务加载该任务后是否立即执行一次，测试时才建议开启。
- `stagger_seconds` / `stagger_mode`：覆盖默认错峰配置。

### cron 写法

支持 5 字段和 6 字段：

```text
# 5 字段：分 时 日 月 星期
10 0 * * *

# 6 字段：秒 分 时 日 月 星期
0 10 0 * * *
```

在 Telegram 控制命令里，因为空格会分隔参数，cron 用 `_` 代替空格：

```text
0_10_0_*_*_*
```

`-` 表示使用默认 cron。

### flow 配置字段

兼容两种写法：

- 旧 list 写法：`flow: [{send, expect, expect_any, timeout_seconds, delay_seconds}]`，默认只执行 1 轮。
- 新结构化写法：`flow: {mode, repeat, rules, steps}`，支持 `count` 轮次、成功/重试/终止规则。

结构化字段：

- `mode`：目前支持 `auto` / `manual`，当前运行器按 `auto` 执行。
- `repeat.count`：最大尝试轮数，必须大于 0；不是必须跑满。
- `repeat.interval_seconds`：每轮之间等待秒数。
- `repeat.jitter_seconds`：每轮额外随机等待秒数。
- `repeat.stop_on_success`：命中成功文本后是否停止。
- `repeat.max_runtime_seconds`：整个 flow 最大运行秒数。
- `rules.abort_on_text`：不可继续文本，优先级最高，命中后立即停止整个 flow。
- `rules.success_on_text`：成功文本。
- `rules.retry_on_text`：可重试文本，命中后结束当前轮并进入下一轮。
- `rules.unknown_policy`：`retry` / `abort`。
- `rules.max_unknown_replies`：允许 unknown 回复的最大次数。

### flow step 字段

旧 list 写法：

- `send`：本步骤发送的文本。必填。
- `expect`：期望回复中包含的单个文本。
- `expect_any`：期望回复中包含任一文本，字符串或列表均可。
- `timeout_seconds` / `timeout`：等待回复超时时间，默认 `20` 秒。
- `delay_seconds` / `delay`：本步骤通过后额外等待时间，默认 `0` 秒。

结构化 `steps` 写法：

- `action`：`send` / `click` / `wait`。
- `text`：`send` 步骤发送的文本。
- `button`：`click` 步骤点击/发送的按钮文本；ReplyKeyboard 场景下本质上是发送该按钮文本。
- `expect`：期望回复中包含的单个文本。
- `expect_any`：可为字符串、列表，或 `{text: [...], buttons: [...]}`。
- `timeout_seconds` / `timeout`：等待回复超时时间，默认 `20` 秒。
- `delay_seconds` / `delay`：本步骤通过后额外等待时间，默认 `0` 秒。

## 控制命令

控制命令只监听登录账号自己发出的消息。推荐在目标群里用该账号发送命令，程序会自动读取当前群名和 `chat_id`。

```text
/help
/id
/list
/add <message...>
/add <cron|-> <message...>
/add <task> <cron|-> <message...>
/add <name> <chat_id> <cron|-> <message...>
/del [name]
/enable [name]
/disable [name]
/set [name] cron <expr|->
/set [name] message <text>
/set [name] chat_id <id|username>
/test [name]
```

常用例子：

```text
# 当前群添加普通签到
/add /checkin@HyVPS_Bot

# 当前群添加指定时间签到
/add 0_10_9_*_*_* /checkin@HyVPS_Bot

# 当前群添加两个子任务
/add morning 0_10_9_*_*_* /checkin@HyVPS_Bot
/add night 0_10_21_*_*_* /sign@OtherBot

# 私聊 bot/用户目标，使用完整模式
/add 公益Plus freexzteam_bot - 📅 每日签到

# 修改和测试
/set cron 0_10_9_*_*_*
/set cron -
/test
/test morning
```

当前控制命令主要面向普通 `message` 任务；复杂 `flow` 建议直接编辑 `config/config.yml`。

## 验证、日志和排错

### 配置校验

```bash
docker compose run --rm --no-deps tg-checkin python /app/app.py validate /config/config.yml
```

看到类似输出表示配置能解析：

```text
OK: 4 jobs, timezone=Asia/Shanghai
```

### 运行测试

```bash
docker compose run --rm --no-deps tg-checkin pytest tests -q
```

### 查看服务状态

```bash
docker compose ps
docker compose logs --tail=100 tg-checkin
```

### 常见问题

- **bot 回复“没识别到你当前在走哪一步”**
  - 说明上下文丢失或按钮文本不完全匹配。
  - 用 flow 从 `/start` 或主菜单重新走完整路径。
  - 检查 emoji 是否完全一致，例如 `🛍` vs `🛍️`。

- **`Could not find the input entity`**
  - 正数用户 / bot ID 可能缺少 access_hash。
  - 私聊 bot/用户建议用 username，例如 `freexzteam_bot`。
  - 群组仍推荐 `-100...` 数字 ID。

- **`TG_SESSION_STRING is not authorized`**
  - session 无效或已被注销。
  - 重新生成 `TG_SESSION_STRING` 并更新 `.env`。

- **普通签到没有执行**
  - 确认 `enabled: true`。
  - 确认任务仍有 `message`，不要只留下 `flow`。
  - 运行 `validate` 查看 job 数量。
  - 查看日志中是否有 `scheduled job=.../签到`。

## 更新部署和回滚

### 更新代码

```bash
git pull --ff-only origin main
docker compose build tg-checkin
docker compose run --rm --no-deps tg-checkin pytest tests -q
docker compose run --rm --no-deps tg-checkin python /app/app.py validate /config/config.yml
docker compose up -d --no-build --force-recreate tg-checkin
```

### 保留本机 live 配置

生产环境通常不要覆盖：

- `.env`
- `config/config.yml`
- 实例特有的 `docker-compose.yml`，例如 `container_name`

如果多个实例共用同一源码，建议以 GitHub 仓库为 canonical 源码，只同步代码和文档，保留每个实例自己的 live 配置。

### 回滚

如果使用 Git 部署：

```bash
git fetch origin --tags
git checkout <旧tag或旧commit>
docker compose build tg-checkin
docker compose up -d --no-build --force-recreate tg-checkin
```

如果使用宿主机备份：

```bash
# 示例，按你的实际备份路径替换
cp -a backups/<timestamp>/tg_checkin ./
cp -a backups/<timestamp>/tests ./
docker compose build tg-checkin
docker compose up -d --no-build --force-recreate tg-checkin
```

## 项目结构

```text
app.py                    # 兼容入口，转发到 tg_checkin.cli
tg_checkin/cli.py         # CLI：run / validate / auth 禁用提示
tg_checkin/app.py         # 运行时编排：Telegram client、APScheduler、配置热加载
tg_checkin/config.py      # YAML 配置读写、环境变量和任务解析
tg_checkin/flow.py        # ReplyKeyboard/按钮式 bot 状态机执行器
tg_checkin/flow_config.py # flow 配置解析与校验
tg_checkin/control.py     # Telegram outgoing 控制命令解析与持久化修改
tg_checkin/scheduler.py   # cron 和错峰调度辅助
tg_checkin/telegram.py    # Telethon client/session、实体解析和 bot_command entity
tg_checkin/models.py      # 数据模型与基础校验
tests/                    # 行为回归测试
```

## 安全注意事项

- 不要提交 `.env`、`TG_API_HASH`、`TG_SESSION_STRING`。
- `TG_SESSION_STRING` 泄露后应立即在 Telegram 注销对应会话并重新生成。
- 不建议在服务器上交互输入手机号、验证码或 2FA 密码。
- 不要把所有任务设置成同一秒发送；保留错峰或分散 cron。
- 对会产生消耗/兑换/购买的 flow，先移除最后确认步骤做 dry-run，确认路径正确后再启用。
