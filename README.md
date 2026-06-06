# tg-checkin-bot

Telegram 用户号定时签到 / 按钮式 bot 流程自动化工具。

它不是 Bot API 程序，而是使用 **Telethon 用户号会话**模拟你自己的 Telegram 账号发消息。因此它适合：

- **普通签到**：每天向群组或 bot 发送一条消息，例如 `/checkin@HyVPS_Bot`、`签到`、`📅 每日签到`。
- **菜单流程**：按 bot 菜单一步步发送按钮文本，例如 `/start → 积分商城 → 商品 → 确认兑换`。
- **有限重复流程**：对同一个 flow 最多重复 N 轮，命中成功/终止/异常规则后提前停止，适合手动式抢购/兑换场景的低频自动化辅助。

请遵守 Telegram 和目标 bot/群组规则。不要用它做垃圾消息、高频刷屏、绕过风控或违反服务条款的操作。

## 目录

- [它能做什么](#它能做什么)
- [核心概念](#核心概念)
- [快速部署](#快速部署)
- [生成 TG_SESSION_STRING](#生成-tg_session_string)
- [配置从哪里开始写](#配置从哪里开始写)
- [示例 1：普通每日签到](#示例-1普通每日签到)
- [示例 2：同一目标多个时间段](#示例-2同一目标多个时间段)
- [示例 3：按钮式 flow，一次走完整菜单](#示例-3按钮式-flow一次走完整菜单)
- [示例 4：重复兑换/抢购 flow](#示例-4重复兑换抢购-flow)
- [flow 是怎么判断继续、成功、重试、终止的](#flow-是怎么判断继续成功重试终止的)
- [配置字段速查](#配置字段速查)
- [控制命令](#控制命令)
- [验证、日志和排错](#验证日志和排错)
- [更新部署和回滚](#更新部署和回滚)
- [项目结构](#项目结构)
- [安全注意事项](#安全注意事项)

## 它能做什么

### 适合

- 每天定时发送固定签到命令。
- 一个群或 bot 配置多个定时任务。
- 给 Telegram bot 私聊发送菜单文本。
- 读取 bot 回复正文和按钮文本，确认当前步骤是否走对。
- 对一个 flow 设置最大尝试轮数，例如最多重复 50 次。
- 命中“今日上限已满”“积分不足”等文本时立即停止。

### 不适合

- 验证码、支付确认、人机验证、安全检测等需要人工判断的流程。
- 需要真正点击 **InlineKeyboard callback** 的复杂 bot。目前 `click` 对 ReplyKeyboard 类菜单等价为“发送按钮文本”。
- 高频抢购、秒级并发、绕过限制。当前设计是单账号、低频、可控、可停止。
- 同一 chat/bot 多个 flow 并发执行。当前没有 chat-level lock，建议不要给同一 bot 配置同时运行的多个 flow。

## 核心概念

### 1. group 是目标会话

一个 `group` 表示一个 Telegram 目标：群、频道、用户或 bot。

```yaml
groups:
  - name: HyVPS
    enabled: true
    chat_id: -1003849837200
    message: /checkin@HyVPS_Bot
```

`chat_id` 可以是：

- 群组 ID：推荐 `-100...` 数字 ID。
- bot / 用户 username：例如 `freexzteam_bot` 或 `@freexzteam_bot`。

### 2. message 任务只发一条消息

适合普通签到：

```yaml
- name: 每日签到
  cron: ""
  message: "📅 每日签到"
```

`cron: ""` 表示使用全局 `default_cron`。

### 3. flow 任务按步骤走菜单

适合 bot 菜单流程：

```yaml
- name: plus兑换
  cron: "0 0 0 * * *"
  flow:
    steps:
      - action: send
        text: "/start"
        expect_any: ["积分商城"]
      - action: click
        button: "🛍️ 积分商城"
        expect_any: ["Plus 成品号"]
```

每一步会做四件事：

1. 发送 `text` 或 `button` 对应文本。
2. 等待 bot 新回复。
3. 把回复正文和按钮文本合并后匹配 `expect` / `expect_any`。
4. 如果回复不符合预期，立即停止，避免上下文错了还继续误发。

### 4. repeat.count 是最大尝试轮数

`repeat.count` 不是“必须执行多少次”，而是“最多允许尝试多少轮”。

例如：

```yaml
repeat:
  count: 50
  interval_seconds: 3
  jitter_seconds: 1
  stop_on_success: true
```

含义：

- 最多尝试 50 轮。
- 每轮之间等待约 3～4 秒。
- 一旦命中成功文本，就提前停止。
- 一旦命中终止文本，也提前停止。

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

验证配置和日志：

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

## 配置从哪里开始写

最小配置结构如下：

```yaml
timezone: Asia/Shanghai

default_delay_seconds: 3
default_cron: "0 10 0 * * *"
default_stagger_seconds: 1800
default_stagger_mode: stable

groups:
  - name: 示例
    enabled: true
    chat_id: example_bot
    message: "签到"
    cron: ""
```

写配置时按这个顺序最不容易出错：

1. 先只配置一个普通 `message`，确认账号能发消息。
2. 再把目标 bot 的菜单路径手动走一遍，复制每个按钮的完整文本。
3. 写一个 `repeat.count: 1` 的 flow，只走到确认前一步。
4. 验证路径正确后，再加入最后确认步骤。
5. 最后才把 `repeat.count` 调大，并设置 `abort_on_text` / `success_on_text` / `retry_on_text`。

## 示例 1：普通每日签到

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

## 示例 2：同一目标多个时间段

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

## 示例 3：按钮式 flow，一次走完整菜单

适合只需要执行一轮的 bot 菜单流程。

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

      - name: plus兑换单轮
        cron: "0 0 0 * * *"
        run_on_start: false
        flow:
          mode: auto
          repeat:
            count: 1
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
              button: "成品号"
              expect_any:
                text:
                  - "确认兑换"
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

说明：

- `action: send`：发送普通文本，例如 `/start`。
- `action: click`：发送按钮文本，适合 ReplyKeyboard / 菜单按钮类 bot；`button` 可以写完整按钮名，也可以只写稳定子串，运行时会从上一条 bot 回复的按钮中自动补全完整文本。
- `expect_any.text`：匹配 bot 回复正文。
- `expect_any.buttons`：匹配 bot 回复里出现的按钮文本。
- 当前底层匹配会把正文和按钮文本合并后做包含匹配，因此按钮和正文都能作为判断依据。

按钮子串自动补全示例：

```yaml
- action: click
  button: "成品号"
  expect_any:
    text: ["确认兑换", "积分不足"]
    buttons: ["确认兑换"]
```

如果 bot 当前真实按钮从 `💎 Plus 成品号(PP渠道) · 3积分` 变成 `💎 Plus 成品号(X渠道) · 5积分`，运行器会在上一条 bot 回复的按钮列表中按 `成品号` 子串匹配，并发送完整实时按钮文本。这样渠道名、积分数、emoji 前缀变化时不用频繁改配置。

## 示例 4：重复兑换/抢购 flow

这个示例演示“最多重复 50 轮，成功/上限/异常时提前停止”。

```yaml
groups:
  - name: 公益Plus/Team机器人
    enabled: true
    chat_id: freexzteam_bot
    parse_bot_command: true
    tasks:
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
              - "积分不足"
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
                text: ["积分商城"]
                buttons: ["积分商城"]
              timeout_seconds: 20

            - action: click
              button: "🛍️ 积分商城"
              expect_any:
                text: ["Plus 成品号"]
                buttons: ["Plus 成品号"]
              timeout_seconds: 20

            - action: click
              button: "成品号"
              expect_any:
                text:
                  - "确认兑换"
                  - "今日 Plus 成品号上限已满，明天再来"
                  - "积分不足"
                buttons: ["确认兑换"]
              timeout_seconds: 20

            - action: click
              button: "✅ 确认兑换"
              expect_any:
                text:
                  - "兑换成功"
                  - "今日 Plus 成品号上限已满，明天再来"
                  - "库存不足"
                  - "请稍后再试"
              timeout_seconds: 25
```

这段配置的执行语义：

- 每一轮都从 `/start` 开始重新走完整路径，减少上下文错乱。
- 任意步骤的回复命中 `abort_on_text`，立即停止整个 flow。
- 任意步骤的回复命中 `success_on_text`，因为 `stop_on_success: true`，立即停止整个 flow。
- 任意步骤的回复命中 `retry_on_text`，当前轮结束，等待后进入下一轮。
- 回复不在规则里，但也没有违反当前 step 的 `expect_any` 时，视为 unknown。
- `unknown_policy: retry` 且 `max_unknown_replies: 3` 表示 unknown 最多重试 3 次，超过后停止。
- `max_runtime_seconds: 300` 是总运行时兜底，避免意外跑太久。

抢购/兑换类任务建议：

1. 第一次只保留到“确认兑换”前一步，不要放最后的 `✅ 确认兑换`。
2. `repeat.count` 先设为 `1`。
3. 手动观察日志，确认每一步匹配到的文本都正确。
4. 再加入最后确认步骤。
5. 最后才把 `repeat.count` 调大，例如 `20` 或 `50`。

## flow 是怎么判断继续、成功、重试、终止的

### 每一步的判断顺序

bot 回复后，运行器会先把以下内容合并成待匹配文本：

- 回复正文 `raw_text`
- 回复里的按钮文本 `buttons`

然后按固定顺序判断：

1. `rules.abort_on_text`
2. `rules.success_on_text`
3. `rules.retry_on_text`
4. 当前 step 的 `expect` / `expect_any`
5. unknown 策略

因此：

- `abort_on_text` 优先级最高，适合放“今日上限已满”“积分不足”“明天再来”等不可继续条件。
- `success_on_text` 只应放最终成功文本，不要放太宽泛的词，否则可能中途提前停止。
- `retry_on_text` 适合放“库存不足”“繁忙”“排队中”等可再试条件。
- `expect_any` 是当前步骤的安全护栏，确保菜单走到了预期页面。

### 旧 flow 写法仍然兼容

旧版 list 写法仍可用：

```yaml
flow:
  - send: "/start"
    expect: "积分商城"
  - send: "🛍️ 积分商城"
    expect_any: ["Plus 成品号", "商品列表"]
```

旧写法等价于：

- 只执行 1 轮。
- 不启用 `repeat.rules`。
- 每一步只按 `expect` / `expect_any` 判断是否继续。

如果需要 `count`、`abort_on_text`、`success_on_text`、`retry_on_text`，请使用新结构化写法：

```yaml
flow:
  mode: auto
  repeat:
    count: 50
  rules:
    abort_on_text: ["今日兑换上限已满"]
  steps:
    - action: send
      text: "/start"
      expect_any: ["积分商城"]
```

### `click` 为什么是发送按钮文本

很多 Telegram bot 的底部菜单是 ReplyKeyboard。用户点击按钮时，本质上就是发送一条与按钮文字相同的消息。

所以本项目里：

```yaml
- action: click
  button: "🛍️ 积分商城"
```

当前等价于发送：

```text
🛍️ 积分商城
```

注意：

- 按钮文本要完全一致。
- emoji 变体也可能不同，例如 `🛍` 和 `🛍️` 不是同一个字符串。
- 如果目标 bot 使用 InlineKeyboard callback，而不是 ReplyKeyboard，当前版本可能无法真正触发 callback。

## 配置字段速查

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
- `flow`：按钮式状态机流程。
- `tasks`：多个子任务列表。
- `parse_bot_command`：是否为 `/command` 自动构造 Telegram bot command entity。
- `cron`：调度时间。
- `run_on_start`：服务加载该任务后是否立即执行一次，测试时才建议开启。
- `stagger_seconds` / `stagger_mode`：覆盖默认错峰配置。

### task 字段

在 `tasks:` 下，每个子任务可以是普通 message，也可以是 flow。

普通 message：

```yaml
- name: 签到
  cron: ""
  message: "📅 每日签到"
```

flow：

```yaml
- name: 兑换
  cron: "0 0 0 * * *"
  flow:
    mode: auto
    repeat:
      count: 1
    steps:
      - action: send
        text: "/start"
        expect_any: ["积分商城"]
```

如果任务中存在 `flow`，程序会自动按 flow 任务处理；否则按 message 任务处理。

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

### flow.repeat 字段

```yaml
repeat:
  count: 50
  interval_seconds: 3
  jitter_seconds: 1
  stop_on_success: true
  max_runtime_seconds: 300
```

字段说明：

- `count`：最大尝试轮数，必须大于 0；成功或终止可提前停止。
- `interval_seconds`：每轮之间固定等待秒数。
- `jitter_seconds`：每轮额外随机等待秒数，实际等待约为 `interval_seconds` 到 `interval_seconds + jitter_seconds`。
- `stop_on_success`：命中成功文本后是否停止，默认 `true`。
- `success_quota`：成功次数配额，当前为高级字段；普通场景不建议使用。
- `max_runtime_seconds`：整个 flow 最大运行秒数。

### flow.rules 字段

```yaml
rules:
  abort_on_text:
    - "今日兑换上限已满"
  success_on_text:
    - "兑换成功"
  retry_on_text:
    - "库存不足"
  unknown_policy: retry
  max_unknown_replies: 3
```

字段说明：

- `abort_on_text`：不可继续文本，优先级最高，命中后立即停止整个 flow。
- `success_on_text`：成功文本，命中后按 `stop_on_success` 决定是否停止。
- `retry_on_text`：可重试文本，命中后结束当前轮并进入下一轮。
- `unknown_policy`：`retry` / `abort`。
- `max_unknown_replies`：允许 unknown 回复的最大次数。

推荐规则：

- 上限、积分不足、资格不足：放 `abort_on_text`。
- 兑换成功、领取成功、已生成：放 `success_on_text`。
- 库存不足、繁忙、排队、稍后再试：放 `retry_on_text`。

### flow.steps 字段

```yaml
steps:
  - action: send
    text: "/start"
    expect_any:
      text: ["积分商城"]
      buttons: ["积分商城"]
    timeout_seconds: 20
    delay_seconds: 0
```

字段说明：

- `action`：`send` / `click` / `wait`。
- `text`：`send` 步骤发送的文本。
- `button`：`click` 步骤发送的按钮文本。
- `expect`：期望回复中包含的单个文本。
- `expect_any`：期望回复中包含任一文本。
  - 可以是字符串：`expect_any: "积分商城"`
  - 可以是列表：`expect_any: ["积分商城", "商品列表"]`
  - 可以区分来源：`expect_any: {text: [...], buttons: [...]}`
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

修改 `config/config.yml` 后先运行：

```bash
docker compose run --rm --no-deps tg-checkin python /app/app.py validate /config/config.yml
```

看到类似输出表示配置能解析：

```text
OK: 4 jobs, timezone=Asia/Shanghai
```

### 本地测试

开发环境：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest
pytest tests -q
```

Docker 环境：

```bash
docker compose run --rm --no-deps tg-checkin pytest tests -q
```

### 查看服务状态

```bash
docker compose ps
docker compose logs --tail=100 tg-checkin
```

### 如何安全演练 flow

建议按下面顺序演练：

1. 把 flow 任务 `enabled` 所在 group 设为 `true`。
2. 先设置 `run_on_start: false`，避免服务启动立即执行。
3. 先把 `repeat.count` 设为 `1`。
4. 临时移除最后确认步骤，例如不要发送 `✅ 确认兑换`。
5. 重启后通过控制命令 `/test <task>`、临时 `run_on_start: true` 或临时 cron 触发。
6. 看日志确认每一步回复和 `expect_any` 匹配。
7. 如果测试按钮子串补全，应重点确认日志里 `send=` 已从短词补全成完整按钮文本。
8. 再恢复最后确认步骤并调大 `repeat.count`。

安全演练日志示例：

```text
flow reply ... step=2 ... text='... 💎 Plus 成品号(X渠道) · 5积分 ...'
flow step ... step=3/3 action=click send='💎 Plus 成品号(X渠道) · 5积分' expect_any=(...)
flow reply ... step=3 ... text='你选择的是「Plus 成品号(X渠道)」... ✅ 确认兑换 ...'
```

上面这种演练只跑到确认页，不发送 `✅ 确认兑换`，可验证按钮自动补全而不实际消耗积分。

### 常见问题

- **flow 在某一步停止，日志提示 unexpected reply**
  - 当前回复没有包含该步骤的 `expect` / `expect_any`。
  - 用手动账号重新走一遍菜单，确认最新回复正文和按钮文本。
  - 对会变化的商品按钮，优先把 `button` 写成稳定子串，例如 `成品号`，并在 `expect_any.buttons` 里放同样稳定的菜单关键词。
  - 检查 emoji 是否完全一致，例如 `🛍` vs `🛍️`；如果 emoji 经常变化，就不要把 emoji 放进稳定子串。

- **命中“今日上限已满”后还继续跑**
  - 确认该文本放在 `rules.abort_on_text`，不是只放在 step 的 `expect_any`。
  - 确认文本完全包含 bot 实际回复里的关键句。

- **成功后还继续下一轮**
  - 确认 `rules.success_on_text` 包含真实成功文本。
  - 确认 `repeat.stop_on_success: true`。

- **库存不足时直接失败，不重试**
  - 把“库存不足”“请稍后再试”等放进 `rules.retry_on_text`。
  - 确认 `repeat.count` 大于 1。

- **bot 回复“没识别到你当前在走哪一步”**
  - 说明上下文丢失或按钮文本不完全匹配。
  - 用 flow 从 `/start` 或主菜单重新走完整路径。
  - 检查是否多个 flow 同时打到同一个 bot。

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
- 不要给同一 bot 配置多个同时运行的 flow；当前版本尚未实现 chat-level lock。
- 终止条件优先写进 `abort_on_text`，不要只依赖某一步的 `expect_any`。
- 对抢购/兑换类任务设置合理的 `count`、`interval_seconds`、`max_runtime_seconds`，避免长时间刷屏。
