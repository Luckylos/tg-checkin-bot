# Flow Runner 重构实施计划

## 背景

当前项目已经支持普通 `message` 任务和简单顺序 `flow` 步骤，但旧实现仍偏向“定时发消息工具”：

- `flow` 只是顺序发送若干文本并检查期望回复；
- 没有 `count` 轮次语义；
- 没有高优先级终止规则；
- 成功、可重试失败、不可继续失败没有被显式建模；
- Flow Runner 直接依赖 Telethon 客户端协议，后续测试和扩展成本偏高。

本次重构目标是将项目升级为 Telegram Userbot Flow Runner，同时保留普通签到任务兼容性。

## 目标

1. 保留普通 `message` 定时任务。
2. 新增结构化 `flow` 任务能力。
3. 支持 `flow.repeat.count`，语义为最大尝试轮数。
4. 支持 `abort_on_text` / `success_on_text` / `retry_on_text`。
5. `abort_on_text` 优先级最高，命中后立即停止整个 flow，即使未达到 `count`。
6. 将 `今日 Plus 成品号上限已满，明天再来` 作为典型不可继续终止文本。
7. 使用 TDD 锁定核心行为：ReplyClassifier、RepeatController、FlowRunner、Config validator。
8. 避免同一 chat/bot 的并发 flow 串线，后续引入 chat-level lock。

## 非目标

- 不实现验证码绕过、风控绕过或支付确认自动化。
- 不修改 Telegram 登录凭据或 `.env`。
- 不直接修改 RN 线上 `/opt/tg-checkin-bot` 作为源码来源。
- 本阶段不做公网入口、systemd、Docker 网络等部署层变更。

## 新配置形态

推荐新 flow 任务配置：

```yaml
groups:
  - name: 公益Plus/Team机器人
    enabled: true
    chat_id: freexzteam_bot
    parse_bot_command: true
    tasks:
      - name: 签到
        type: message
        cron: ""
        message: "📅 每日签到"

      - name: plus兑换
        type: flow
        cron: "0 0 0 * * *"
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
            - action: click
              button: "积分商城"
              expect_any:
                text:
                  - "Plus 成品号"
                buttons:
                  - "Plus 成品号"
            - action: click
              button: "Plus 成品号"
              expect_any:
                text:
                  - "确认兑换"
                  - "今日 Plus 成品号上限已满，明天再来"
                buttons:
                  - "确认兑换"
            - action: click
              button: "确认兑换"
              expect_any:
                text:
                  - "兑换成功"
                  - "今日 Plus 成品号上限已满，明天再来"
                  - "库存不足"
```

## 兼容策略

旧配置仍可继续使用：

```yaml
flow:
  - send: "/start"
    expect: "积分商城"
```

解析器将旧 list 形式转换为新版 flow：

- `repeat.count = 1`；
- `rules` 为空；
- 每个旧步骤转换为 `action: send`、`text: <send>`；
- `expect` / `expect_any` 转换为文本期望。

这样可以避免现有线上配置在重构后失效。

## 核心模块

### domain models

- `FlowTask`
- `FlowStep`
- `RepeatPolicy`
- `MatchRules`
- `ExpectRule`
- `FlowRunResult`
- `ReplyClassification`

### ReplyClassifier

职责：将 bot 回复按规则分类。

优先级固定：

```text
abort_on_text > success_on_text > retry_on_text > unknown
```

### RepeatController / FlowRunner

职责：控制最大尝试轮数、成功停止、abort 提前终止、retry 进入下一轮、unknown 熔断。

### Telegram Adapter

职责：隔离 Telethon 细节，后续目标接口：

```python
class TelegramAdapter(Protocol):
    async def send_text(self, entity, text: str, *, parse_bot_command: bool) -> TgMessage: ...
    async def wait_reply(self, entity, *, after_message_id: int, timeout_seconds: float) -> TgMessage | None: ...
    async def click_button(self, message: TgMessage, button_text: str) -> TgMessage | None: ...
```

本阶段可以先在现有 `BotFlowRunner` 中实现核心语义，再逐步抽象 adapter。

## TDD 验收用例

### P0 ReplyClassifier

- abort 文本优先级高于 success；
- `今日 Plus 成品号上限已满，明天再来` 命中 abort；
- success 文本命中 success；
- retry 文本命中 retry；
- 无规则命中时为 unknown。

### P0 Repeat / FlowRunner

- `count=50` 时第 3 轮 abort，结果为 `STOPPED_ABORT_TEXT`，不执行第 4 轮；
- 第一轮 success 且 `stop_on_success=true`，结果为 `DONE_SUCCESS`；
- 每轮 retry 时最多运行 `count` 轮，最终 `DONE_COUNT_REACHED`；
- unknown 超过 `max_unknown_replies` 后停止；
- 旧 list flow 仍能执行 1 轮。

### P0 Config validator

- `repeat.count` 必须为正整数；
- `unknown_policy` 只能为 `retry` 或 `abort`；
- `steps` 不能为空；
- `send` step 必须有 `text`；
- `click` step 必须有 `button`；
- 旧 list flow 配置仍兼容。

## 执行顺序

1. 建重构分支和回滚点。
2. 写本文档，固化范围。
3. 写 P0 失败测试。
4. 实现配置模型和解析兼容层。
5. 实现 ReplyClassifier。
6. 实现 `count` 轮次 FlowRunner。
7. 用 FakeTelegramClient 做集成测试。
8. 更新 README 和 `config.example.yml`。
9. 运行质量门禁：
   - `.venv/bin/python -m pytest tests -q`
   - `.venv/bin/python app.py validate config.example.yml`
   - `python3 -m py_compile tg_checkin/*.py`
   - `git diff --check`
10. 通过后再考虑 GitHub 推送与 RN 灰度部署。

## 回滚

本地代码回滚：

```bash
git checkout main
git branch -D refactor/flow-runner-count-abort
```

或回到本次开始前的本地回滚分支：

```bash
git checkout rollback/pre-flow-runner-<timestamp>
```

RN 线上回滚应在部署阶段另行创建远端备份，且保留 `.env`、`config/config.yml` 和实例特有 compose 字段。
