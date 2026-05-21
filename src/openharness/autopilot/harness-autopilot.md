现在完整了。用一个具体案例串起来。

---

## 案例：用户想修一个登录 Bug

### 总览：两条完全不同的路径

```
路径 A: 直接对话（无验证）    路径 B: Autopilot 交付（有验证）
─────────────────────────    ─────────────────────────────
oh -p "fix login bug"        /autopilot add → /autopilot run-next
      │                            │
      ▼                            ▼
  handle_line                _autopilot_handler
      │                            │
      ▼                            ▼
  submit_message              store.run_next()
      │                            │
      ▼                            ▼
  run_query (LLM 循环)        run_card()  ← 包含验证的完整交付流水线
      │                            │
      ▼                            ├─ preparing (创建 worktree)
  文本回复给用户                   ├─ running → _run_agent_prompt()
                                   │               └─ submit_message → run_query
                                   ├─ verifying → _run_verification_steps()
                                   ├─ repairing (失败则重试)
                                   ├─ git commit → create PR
                                   └─ waiting_ci → merge
```

---

### 路径 A 详解：`oh -p "fix the login bug"`（纯对话，无验证）

这是你最熟悉的链路，一句话概括：

```
CLI (cli.py) 解析参数
  → handle_line("fix the login bug")
    → submit_message("fix the login bug")
      → run_query (while 循环)
          ├─ 自动压缩 + 图片预处理
          ├─ stream_message (调用 LLM)
          ├─ 拿到 final_message (文本 or tool_use)
          ├─ 有工具调用？→ 权限校验 → 并发执行 → 结果 append → 回到循环
          └─ 无工具调用？→ return
      → 文本流式返回给终端
```

**没有验证环节**。LLM 说修好了就是修好了，代码改了但没人跑过 lint/test。

---

### 路径 B 详解：`/autopilot add` → `/autopilot run-next`（完整交付流水线）

#### 第 0 步：用户录入任务

```
用户输入: /autopilot add idea fix login bug :: users can't login after password reset
                │
                ▼
commands/registry.py:2039  _autopilot_handler("add idea fix login bug :: ...")
                │
                ▼
autopilot/service.py:265   store.enqueue_card(
                              source_kind="manual_idea",
                              title="fix login bug",
                              body="users can't login after password reset"
                            )
                │
                ▼
                           card 写入 registry.json，状态=queued
                           返回: "Queued autopilot card task-001 (score=50): fix login bug"
```

#### 第 1 步：用户触发执行

```
用户输入: /autopilot run-next
                │
                ▼
commands/registry.py:2098  _autopilot_handler("run-next")
                │
                ▼
autopilot/service.py:627   store.run_next()
                │
                ├─ pick_next_card() → 找到 task-001
                │
                └─ run_card("task-001")
```

#### 第 2 步：`run_card` —— 准备阶段

```
run_card("task-001")
  │
  ├─ 1. 读策略: load_policies()
  │     - autopilot_policy.yaml: max_turns=12, permission_mode=full_auto
  │     - verification_policy.yaml: commands=[lint, test, build]
  │
  ├─ 2. 更新状态: status="preparing"
  │
  ├─ 3. 创建隔离 worktree:
  │     WorktreeManager.create_worktree(branch="autopilot/task-001")
  │     working_cwd = /tmp/worktree-xxx
  │
  └─ 4. 组装 prompt (含 autopilot policy + 任务描述 + verification policy)
```

#### 第 3 步：`run_card` —— 执行阶段（进入 attempt 循环）

```
for attempt_count in range(1, max_attempts+1):    ← 最多重试 N 轮
  │
  ├─ 状态切为 "running"
  │
  ├─ _run_agent_prompt(prompt)                   ← ★ 这里才调用 run_query
  │   │
  │   │  autopilot/service.py:2041
  │   │
  │   ├─ build_runtime(cwd=worktree, permission_mode="full_auto", ...)
  │   │     └─ 组装 QueryEngine + ToolRegistry + PermissionChecker + Hooks
  │   │
  │   ├─ start_runtime(bundle)
  │   │
  │   ├─ async for event in bundle.engine.submit_message(prompt):
  │   │     │
  │   │     │  这就是你之前分析的核心链路:
  │   │     │  handle_line → submit_message → run_query
  │   │     │    ├─ 自动压缩 + 图片预处理
  │   │     │    ├─ stream_message (LLM 思考)
  │   │     │    ├─ 工具调用? → 权限校验(此处 full_auto 全放行) → 并发执行
  │   │     │    └─ 无工具调用? → 结束
  │   │     │
  │   │     └─ 收集 AssistantTextDelta 文本 → assistant_summary
  │   │
  │   └─ close_runtime(bundle)
  │
  │   返回: "I fixed the login bug by updating auth.py..."
  │
  ├─ 写入 run report (状态=pending)
  │
  ├─ 状态切为 "verifying"                    ← ★ 里程碑验证在这里
  │
  ├─ _run_verification_steps(policies)        autopilot/service.py:2094
  │   │
  │   for cmd in verification_commands:
  │     ├─ "ruff check src/"     → 执行 → returncode=0 ✓
  │     ├─ "pytest -q"           → 执行 → returncode=1 ✗ (2 tests failed)
  │     └─ "mypy src/"           → 执行 → returncode=0 ✓
  │
  │   返回: [step(passed), step(failed, "2 tests failed"), step(passed)]
  │
  ├─ 检查失败项:
  │   failing = [step for step in steps if step.status in {"failed", "error"}]
  │
  ├─ 有失败 + 还没到最大重试次数?
  │   │
  │   ├─ 状态切为 "repairing"
  │   ├─ 记录失败原因: "pytest -q rc=1"
  │   ├─ continue  ← 回到 for 循环顶部，带着失败信息重新跑 agent
  │   │
  │   │  第 2 轮 attempt:
  │   │  _prepare_repair_prompt(...) → prompt 里包含:
  │   │    "上一轮验证失败: pytest -q 返回码=1, 2 tests failed.
  │   │     请修复失败的测试后重新提交。"
  │   │  → _run_agent_prompt() → agent 修复测试 → 再验证 → 通过 ✓
  │   │
  │   └─ ...
  │
  └─ 全部通过!
```

#### 第 4 步：`run_card` —— 交付阶段

```
验证通过后:
  │
  ├─ git commit -m "autopilot(task-001): fix login bug"
  │
  ├─ git push → create PR (title: "Autopilot: fix login bug", label: autopilot)
  │
  ├─ 状态切为 "pr_open" → "waiting_ci"
  │
  ├─ 轮询 GitHub CI status
  │
  ├─ CI 通过 + 有 autopilot:merge label?
  │   └─ auto-merge PR
  │
  └─ 状态: "merged" → 写入 journal → 返回 RepoRunResult
```

---

### 完整调用链（一张图）

```
┌─ 用户交互层 ────────────────────────────────────────────────────┐
│                                                                    │
│  /autopilot add ...   /autopilot run-next   oh autopilot run-next │
│        │                     │                      │              │
│        ▼                     ▼                      ▼              │
│  _autopilot_handler    _autopilot_handler     autopilot_run_next_cmd
│        │                     │                      │              │
│        ▼                     ▼                      ▼              │
│  store.enqueue_card()  store.run_next()      store.run_next()     │
│                              │                                     │
├──────────────────────────────┼─────────────────────────────────────┤
│   Autopilot 编排层            ▼                                     │
│                         run_card(card_id)                          │
│                           │                                        │
│  ┌────────────────────────┼─────────────────────────────────┐     │
│  │  preparing             │  创建 worktree, 同步分支         │     │
│  │  running               │  _run_agent_prompt() ───────┐    │     │
│  │  ★ verifying           │  _run_verification_steps()  │    │     │
│  │  repairing (loop)      │  失败→修复→重试验证          │    │     │
│  │  pr_open/waiting_ci    │  git commit/PR/CI轮询       │    │     │
│  │  merged/completed      │  最终交付                    │    │     │
│  └────────────────────────┴─────────────────────────────┘    │     │
│                                                               │     │
├───────────────────────────────────────────────────────────────┼─────┤
│   Engine 层 (你之前分析的)                                     │     │
│                                                               ▼     │
│                    _run_agent_prompt                             │
│                      → build_runtime()                           │
│                      → submit_message(prompt)                    │
│                        → handle_line → submit_message → run_query│
│                          (LLM 对话循环, 无验证)                   │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

### 关键结论

| 问题                         | 答案                                                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `run_query` 里有验证吗？     | **没有**，它只管 LLM 对话循环                                                                                |
| 验证在哪？                   | `run_card` 的 `verifying` 阶段，`_run_verification_steps()`                                                  |
| `run_query` 和验证什么关系？ | `run_card` 先调 `_run_agent_prompt`（内部调 `run_query`），agent 干完活后，`run_card` **独立执行**验证命令   |
| 为什么要分层？               | `run_query` 是通用 LLM 交互引擎，验证是 Autopilot 交付流水线的专属概念。直接对话不需要验证，Autopilot 才需要 |
| 验证失败怎么办？             | Autopilot 自动进入 `repairing` → 把失败信息塞回 prompt → 重新 `run_query` → 再验证，最多 N 轮                |