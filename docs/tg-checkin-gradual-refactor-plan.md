# tg-checkin-bot 渐进式重构计划

## 1. 目标

在**不改变现有业务语义**、不打断当前生产运行方式的前提下，对 `tg-checkin-bot` 做一次**小规模、渐进式、模块化**重构，解决当前代码的职责耦合、运行时/配置/控制平面混杂、验证路径分裂，以及后续继续叠加功能会越来越像“修修补补”的问题。

本计划是**执行前文档**，不是交付结果。后续所有实现都应按本计划的 slice 顺序推进，并在每个 slice 完成后更新本文件状态。

---

## 2. 设计原则（冻结）

### 2.1 必须保持稳定的外部行为

以下行为在重构过程中视为**冻结合同**，除非后续单独立项，不在本轮重构里改变：

1. **部署形态不变**
   - 继续使用 `/opt/tg-checkin-bot`
   - 继续使用 Docker Compose + systemd
   - 继续由 `compose-tg-checkin-bot.service` 托管

2. **配置入口不变**
   - 根配置继续使用 `accounts:`
   - 凭据继续由 `env_prefix` 读取
   - `config/config.yml` + `.env` 继续是运行时入口

3. **任务语义不变**
   - 普通签到/消息任务：`message`
   - 菜单/按钮流程：`flow`
   - `repeat.count=0` 继续表示 noop flow（跳过执行）
   - 同一 account + 同一 `chat_id` 继续串行
   - 多 account 继续并行

4. **控制命令表面不变**
   - `/help /id /list /add /del /enable /disable /set /test`
   - 已有 group/task 名称解析语义不变

5. **容器内验证路径不变**
   - `python /app/app.py validate /config/config.yml`
   - `docker compose run --rm --no-deps tg-checkin ...`

### 2.2 本轮不做的事（明确排除）

1. 不把项目改成 Bot API
2. 不引入数据库或持久化队列
3. 不把 ReplyKeyboard 流程改成真正 callback query 驱动
4. 不重写为多容器微服务
5. 不在本轮引入新的公网入口、管理 UI、外部控制面
6. 不修改现有业务配置内容（除重构过程必要的测试/样例）
7. 不顺手处理与本轮重构无关的 `docker-compose.yml` 资源限制差异

---

## 3. 当前 live 基线（2026-07-05）

### 3.1 仓库与运行状态

- 仓库路径：`/opt/tg-checkin-bot`
- 当前分支：`master`
- 当前最近提交：
  - `309af32 feat: support repeat.count=0 as noop flow to gracefully disable flow tasks`
  - `5978e5a feat: add Telethon proxy support for ShellCrash egress`
  - `2b83b10 docs: document single-container multi-account deployment`
- 当前托管方式：`compose-tg-checkin-bot.service`
- 当前容器：`tg-checkin`

### 3.2 当前 worktree 不是干净基线

当前 live worktree 存在未提交变更：

- `M tg_checkin/app.py`
- `M tests/test_multi_account.py`
- `M docker-compose.yml`
- `?? .env.bak.20260705-170250`

含义：
- `app.py` 与 `tests/test_multi_account.py` 是本轮“断线自愈”相关增量
- `docker-compose.yml` 当前也有未提交差异，但不是本轮重构目标本身
- `.env.bak.*` 是运维备份，不应混入代码重构提交

**因此，真正进入重构前必须先做基线冻结。**

### 3.3 当前运行时验证状态

已观察到：
- 运行容器可启动
- 三个 account 当前都能授权
- scheduler 已装载 `5 enabled jobs`
- 补跑过漏掉的任务，`5/5` 成功
- built image 内测试通过：`39 passed`

### 3.4 当前模块规模（按 `wc -l`）

核心源文件：

- `tg_checkin/control.py` — 474 行
- `tg_checkin/config.py` — 324 行
- `tg_checkin/app.py` — 323 行
- `tg_checkin/flow.py` — 276 行
- `tg_checkin/flow_config.py` — 140 行
- `tg_checkin/models.py` — 133 行
- `tg_checkin/telegram.py` — 111 行
- `tg_checkin/cli.py` — 55 行
- `tg_checkin/scheduler.py` — 53 行

测试：

- `tests/test_flow_runner_repeat.py` — 258 行
- `tests/test_control_service.py` — 203 行
- `tests/test_multi_account.py` — 200 行
- `tests/test_config_scheduler.py` — 170 行
- `tests/test_state_machine.py` — 111 行

### 3.5 当前结构性问题（按职责边界归组，不按 severity）

#### A. 运行时控制面耦合过重

`app.py` 同时承担了：
- Telethon client 生命周期
- runtime/job 分发
- scheduler reload
- config watcher
- signal shutdown
- control bot handler 注册

结果：
- 运行时恢复逻辑容易继续膨胀在 `AccountRuntime`
- scheduler 与 runtime lifecycle 难以单独验证
- 新增自愈后，`app.py` 更像“所有运行时逻辑的汇合点”

#### B. 配置解析职责混在单文件

`config.py` 同时承担：
- YAML load/save
- env 读取
- defaults 解析
- account 解析
- job 展开
- group/task 合并
- config 结构校验

结果：
- 配置模型演化时容易牵动整个文件
- 很难只对某一层（accounts/jobs/defaults）做 focused 验证
- 后续继续加字段会进一步提高回归成本

#### C. 控制平面 `control.py` 过大，读写解析/变更/格式化耦合

`control.py` 当前同时做：
- 命令解析
- target 解析
- config 就地修改
- cron 校验
- 列表展示格式化
- `/test` 任务构建与发送

结果：
- CLI/控制语义与配置写入逻辑绑在一起
- 任何新命令或现有命令语义变化都会触及大文件
- 后续容易继续向“超大 service 文件”演化

#### D. flow 与 transport 的边界还不够清晰

`flow.py` 现在负责：
- flow round/step 编排
- reply 分类
- 按钮文本选择
- 消息等待与消息读取
- 发送动作驱动

而 `telegram.py` 负责：
- client 构造
- entity 解析
- bot command entity

结果：
- flow 编排与 Telegram I/O 的边界不够清楚
- 中途断线恢复策略无法进一步细化到“步骤级安全恢复”
- 后续如果要对 flow 做更强自愈，会继续堆在同一文件

#### E. 验证路径分裂：宿主源码 vs built image

本轮已实际观察到：
- 宿主机 `pytest` 因缺依赖不可直接作为主验证面
- `docker compose run` 验证的是**built image**，不是宿主目录未 build 的源码状态
- 若改了源码但没 rebuild，验证会落到旧镜像，结论会失真

结果：
- 开发/验证流程本身缺少清晰“以谁为准”的约束
- 这是后续继续演进时的重要工程风险

---

## 4. 重构总策略

本轮采用 **slice-based 渐进式重构**，原则如下：

1. **先冻结基线，再做模块切分**
2. **先低风险职责拆分，再碰运行时中心模块**
3. **先把“同文件多职责”拆开，再考虑更细的运行时优化**
4. **每个 slice 只改一个责任边界**
5. **每个 slice 都要有 focused 验证 + full 验证**
6. **每个 slice 单独本地 commit**

---

## 5. 计划切片

## Slice 0 — 基线冻结与执行卫生

### 目标
建立一个可回退、可审计、可继续的重构起点。

### 范围
- 不做业务逻辑重构
- 只处理基线、验证与提交卫生

### 任务
1. 盘点当前未提交变更并分类：
   - 自愈功能改动
   - 测试改动
   - 运维备份
   - 非本轮重构目标差异（`docker-compose.yml`）
2. 形成干净的重构起点：
   - 要么把“自愈 + 测试”固化为一个基线 commit
   - 要么显式拆离非目标差异，避免混入后续重构
3. 在 `docs/` 中更新本计划的执行状态
4. 明确后续验证统一以 **built image** 为主

### 预期产出
- 干净且可复用的重构基线
- 本计划进入可执行状态

### 验证
- `git status --short`
- `docker compose build tg-checkin`
- `docker compose run --rm --no-deps tg-checkin sh -lc 'python -m pip install -q pytest && pytest -q'`

---

## Slice 1 — 运行时控制面收缩：把 `app.py` 变薄

### 目标
把 `app.py` 从“所有运行时逻辑汇合点”收缩成**应用编排层**。

### 责任边界
保留在 `app.py` 的内容：
- `CheckinApp` 顶层编排
- scheduler 启停
- reload loop
- signal shutdown
- runtime registry

迁出的内容：
- `AccountRuntime` 的 client lifecycle / reconnect / single-job dispatch
- 可能的 runtime helper

### 建议拆分
可选新文件（名称允许微调，但职责必须稳定）：
- `tg_checkin/runtime.py` — `AccountRuntime` 与 runtime lifecycle
- `tg_checkin/runtime_recovery.py`（若需要）— reconnect / rebuild policy

### 约束
- 不改变现有 control command 表面
- 不改变 scheduler 配置载入路径
- 不改变 current self-heal 语义

### 验证
- focused：`tests/test_multi_account.py`
- full：全量 `pytest -q`
- runtime smoke：
  - 断开后 `ensure_client_ready`
  - 容器重启后 3 account 可授权

---

## Slice 2 — 配置域拆分：`config.py` 按读/写/解析分层

### 目标
把配置解析从“单大文件”改成清晰的配置域。

### 责任边界
建议拆分为：
- `config_io.py`：YAML `load_config` / `save_config`
- `config_settings.py`：`load_settings_from_env`
- `config_accounts.py`：`parse_accounts`
- `config_jobs.py`：`parse_jobs` / `_parse_group_jobs` / `_build_job`
- `config_defaults.py`（可选）：default_* 与 normalize helper

### 约束
- 外部导入路径暂保持兼容；必要时 `config.py` 做 facade
- 不改变配置 schema
- 不在此 slice 引入新字段

### 验证
- focused：`tests/test_config_scheduler.py` + `tests/test_multi_account.py`
- full：全量 `pytest -q`
- config smoke：`python /app/app.py validate /config/config.yml`

---

## Slice 3 — 控制平面拆分：`control.py` 从大 service 拆为解析/目标解析/变更器

### 目标
把控制命令体系拆成可维护的控制平面，而不是继续叠加在一个大文件里。

### 责任边界
建议拆分：
- `control_parse.py`：命令解析、usage/help 文本
- `control_target.py`：group/task 目标解析
- `control_mutations.py`：`add/del/enable/disable/set`
- `control_render.py`：`/list` 输出格式化
- `control_service.py`：薄 orchestrator

### 约束
- `/help /id /list /add /del /enable /disable /set /test` 行为不变
- 当前群内短名解析优先级不变
- `/test` 的真实发送路径不变

### 验证
- focused：`tests/test_control_service.py`
- full：全量 `pytest -q`
- runtime smoke：容器内 `/test` 路径至少做结构验证（若不做真实发送，则读代码+tests 证明）

---

## Slice 4 — flow / transport 边界重构

### 目标
给未来“flow 级安全恢复”留出结构空间，但本 slice 只做边界清理，不做新功能扩张。

### 责任边界
建议方向：
- `flow.py` 保留 round/step orchestration 与 classification
- `telegram.py` 保留 client factory / entity resolve / command_entities
- 新增一层轻量 transport adapter（可命名为 `flow_transport.py`），承接：
  - send message
  - latest message id
  - wait for reply
  - iter_messages bridge

### 约束
- 当前 `click` 语义不变
- 不新增“中途断线自动重跑整条 flow”
- 不修改 `repeat.count=0` noop 行为

### 验证
- focused：
  - `tests/test_state_machine.py`
  - `tests/test_flow_runner_repeat.py`
- full：全量 `pytest -q`

---

## Slice 5 — 文档与验证流程统一

### 目标
把“怎么验证这项目”本身也收口成稳定工程实践，避免后续维护继续踩“源码变了但镜像没 rebuild”的坑。

### 范围
- README
- `docs/flow-runner-refactor-plan.md`
- 本计划文档
- 如有必要，新增 `docs/development-verification.md`

### 必须写清的事
1. Host source vs built image 的区别
2. 哪些验证必须在 built image 内跑
3. 当前推荐开发闭环：
   - 改源码
   - build image
   - run tests in image
   - recreate service
   - logs smoke
4. 控制命令/flow 测试的副作用边界

### 验证
- 文档 read-back
- 按文档实际跑一遍最小验证链

---

## 6. 推荐执行顺序

严格按以下顺序：

1. **Slice 0** — 基线冻结
2. **Slice 1** — `app.py` 变薄 / runtime 拆分
3. **Slice 2** — `config.py` 拆分
4. **Slice 3** — `control.py` 拆分
5. **Slice 4** — flow / transport 边界重构
6. **Slice 5** — 文档与验证流程统一

这样排序的原因：
- 先把运行时中心收缩，避免后续每个 slice 都碰 `app.py`
- 再拆配置与控制两大“高变更频率文件”
- flow 放后面，因为其副作用风险更高
- 文档最后统一，避免中间多次漂移

---

## 7. 每个 slice 的提交规则

每个 slice 结束时都必须满足：

1. focused tests 通过
2. full tests 通过
3. 若影响容器运行路径，则 built image 验证通过
4. 运行中容器 logs 无明显新异常
5. 本计划执行状态更新
6. 本地单独 commit

建议 commit 风格：
- `refactor(runtime): extract account runtime lifecycle from app`
- `refactor(config): split config parsing into io/settings/accounts/jobs`
- `refactor(control): separate command parsing and target mutation logic`
- `refactor(flow): isolate flow transport boundary`
- `docs(dev): unify host-vs-image verification workflow`

---

## 8. 验证基线（当前项目）

### 8.1 代码验证

优先使用容器内验证：

```bash
cd /opt/tg-checkin-bot

docker compose build tg-checkin

docker compose run --rm --no-deps tg-checkin sh -lc \
  'python -m pip install -q pytest && pytest -q'
```

### 8.2 配置验证

```bash
cd /opt/tg-checkin-bot

docker compose run --rm --no-deps tg-checkin \
  python /app/app.py validate /config/config.yml
```

### 8.3 运行时验证

```bash
systemctl restart compose-tg-checkin-bot.service
sleep 5
docker logs --tail 60 tg-checkin
```

关注点：
- `authorized account=...`
- `config loaded: 5 enabled jobs`
- `scheduler started`
- 无新的 traceback

---

## 9. 回滚策略

### 文档回滚
- 删除本计划文档或回退对应 commit

### 代码回滚
- 以 slice 为单位回滚本地 commit
- 若容器镜像已切换：
  - 回退代码
  - `docker compose build tg-checkin`
  - `systemctl restart compose-tg-checkin-bot.service`

### 配置/凭据回滚
- `.env` 如需回退，使用最新 `.env.bak.*`
- `config/config.yml` 不应在本轮结构重构中被业务性改写

---

## 10. 执行状态

- 状态：**Slice 0 已完成，已形成本地可执行冻结基线**
- Slice 0 结果：
  - 已创建本地回滚分支：`backup/pre-slice0-freeze-20260705-173645`
  - 已将运维 `.env` 备份移出 repo worktree：`/root/ops-backups/tg-checkin-bot/.env.bak.20260705-170250`
  - 已恢复与本轮无关的 `docker-compose.yml` 差异，避免混入后续重构提交
  - 已准备把“自愈增量 + 测试回归 + 重构计划文档”固化为 Slice 0 基线提交
- 当前建议下一步：**从 Slice 1 开始，先把 `app.py` 的 runtime 控制面变薄**
- 注意：后续每个 slice 都必须继续维持“built image 为主验证面”的约束

---

## 11. 计划外但需要记住的 live 事实

1. 当前运行时 `5 enabled jobs`，但 `validate`/`parse_jobs` 视角会看到更大的配置展开数（包含 noop flow）
2. 当前 `repeat.count=0` 已被定义为 noop flow，是现行正确语义
3. 宿主机 Python 环境缺项目依赖，不应把 host pytest 当主验证面
4. `docker compose run` 依赖 built image；源码改动若未 rebuild，会验证到旧镜像

---

## 12. 完成定义（针对本重构计划，不是当前任务）

当以下条件同时满足，才能说本轮重构完成：

1. `app.py`、`config.py`、`control.py` 都不再是“多职责中心文件”
2. 运行时、配置、控制、flow 四个边界清晰
3. 现有业务行为、配置语义、控制命令语义不变
4. built image 内全量测试稳定通过
5. 容器运行验证通过
6. README / docs / 计划文档与真实运行方式一致
7. 没有留下临时 shim、草率脚本或一次性验证胶水作为最终结构
