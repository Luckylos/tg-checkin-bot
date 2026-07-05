# tg-checkin-bot 开发与验证工作流

本文件定义当前项目的**推荐开发/验证闭环**。重点是：

1. **宿主源码（host source）** 与 **已构建镜像（built image）** 是两个不同验证面
2. 运行中容器与 `docker compose run` 默认都依赖 **镜像内容**，不是宿主目录里尚未 rebuild 的源码状态
3. 因此，**代码改完后必须 rebuild，再运行验证**，否则很容易对旧镜像下结论

---

## 1. 两个验证面

### 1.1 宿主源码（host source）

指 `/opt/tg-checkin-bot` 目录里的当前文件内容。

适合做：
- `git diff`
- 计划文档更新
- 只读审查
- 权限、备份、目录结构检查

不适合直接作为最终结论的依据：
- 宿主机 Python 环境未安装完整项目依赖
- 当前生产运行不直接 bind-mount `/opt/tg-checkin-bot/tg_checkin` 代码到容器
- 运行中的容器不会自动感知宿主源码变更

### 1.2 已构建镜像（built image）

指 `docker compose build tg-checkin` 之后生成的镜像内容。

以下动作都验证的是 built image：
- `docker compose run --rm --no-deps tg-checkin ...`
- `systemctl restart compose-tg-checkin-bot.service`
- `docker exec tg-checkin ...`

**因此，built image 是当前项目的主验证面。**

---

## 2. 推荐开发闭环

每次代码修改后，按这个顺序执行：

```bash
cd /opt/tg-checkin-bot

# 1) 查看本地变更
git status --short
git diff --stat

# 2) 重建镜像
docker compose build tg-checkin

# 3) 在镜像内跑测试
docker compose run --rm --no-deps tg-checkin sh -lc \
  'python -m pip install -q pytest >/tmp/pytest-install.log && pytest -q'

# 4) 配置校验
docker compose run --rm --no-deps tg-checkin \
  python /app/app.py validate /config/config.yml

# 5) 切换常驻服务到新镜像
systemctl restart compose-tg-checkin-bot.service
sleep 5

# 6) 读日志做运行时 smoke
docker logs --tail 60 tg-checkin
```

---

## 3. 最低交付验证门槛

若是结构重构或运行时逻辑变更，至少要满足：

1. **built image 全量测试通过**
2. **validate 通过**
3. **容器重启后日志正常**
4. **容器成功装载当前 jobs**

推荐关注日志关键字：

- `authorized account=...`
- `config loaded: N enabled jobs`
- `scheduler started`
- `skip noop flow ... (repeat.count=0)`

不应出现：
- import error
- permission denied
- traceback
- account 未授权

---

## 4. focused tests 建议

### 4.1 runtime / reconnect 逻辑

```bash
docker compose run --rm --no-deps tg-checkin sh -lc \
  'python -m pip install -q pytest >/tmp/pytest-install.log && pytest -q tests/test_multi_account.py'
```

### 4.2 配置解析 / 调度建模

```bash
docker compose run --rm --no-deps tg-checkin sh -lc \
  'python -m pip install -q pytest >/tmp/pytest-install.log && pytest -q tests/test_config_scheduler.py tests/test_multi_account.py'
```

### 4.3 控制平面

```bash
docker compose run --rm --no-deps tg-checkin sh -lc \
  'python -m pip install -q pytest >/tmp/pytest-install.log && pytest -q tests/test_control_service.py tests/test_config_scheduler.py'
```

### 4.4 flow / state-machine

```bash
docker compose run --rm --no-deps tg-checkin sh -lc \
  'python -m pip install -q pytest >/tmp/pytest-install.log && pytest -q tests/test_flow_runner_repeat.py tests/test_state_machine.py'
```

---

## 5. 手动补跑与副作用边界

### 5.1 scheduler 错过窗口后的手动补跑

当前推荐手动补跑方式是：
- 停常驻服务（必要时，避免旧 session 干扰）
- 用 `docker compose run --rm --no-deps tg-checkin python - <<'PY' ...` 的 one-shot 脚本加载相同配置并派发任务

这条路径验证的也是 **built image**。

### 5.2 `flow` 的真实副作用边界

当前项目中：
- 普通 `message` 任务可以做真实补跑/真实 smoke
- `flow` 任务若涉及兑换/确认，不应默认做完整真实端到端执行

特别是：
- `PP_PLUS_FLOW_1` 的真实兑换 smoke 只应验证到 **“确认兑换”页面**
- 不默认发送最终 `✅ 确认兑换`
- 若要做真实权益消耗型 smoke，需要用户明确授权

---

## 6. 本项目当前的工程约束

1. **built image 为主验证面**
2. 宿主机 `pytest` 不能作为主验证面
3. 新增源码文件后要特别注意权限（至少 `0644`），否则容器内运行用户可能无法读
4. `.env` 备份、临时 helper、session staging 文件不应留在 repo worktree 中
5. 非当前 slice 目标的变更（例如 Compose 资源参数差异）不应混入重构提交

---

## 7. 推荐提交前检查

```bash
cd /opt/tg-checkin-bot

git status --short
git diff --stat
docker compose build tg-checkin
docker compose run --rm --no-deps tg-checkin sh -lc \
  'python -m pip install -q pytest >/tmp/pytest-install.log && pytest -q'
docker compose run --rm --no-deps tg-checkin \
  python /app/app.py validate /config/config.yml
```

只有以上全部通过，才适合进入：
- `git add ...`
- `git commit ...`

---

## 8. 结论

对于当前 `tg-checkin-bot`：

> **最终能不能交付，不取决于宿主源码“看起来改好了”，而取决于重建后的镜像、运行中的容器、以及 built image 内测试是否通过。**
