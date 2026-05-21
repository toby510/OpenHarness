# OpenHarness 本地调试指南：4 层架构全覆盖 Demo

## 目录

1. [准备工作](#准备工作)
2. [Layer 1 Demo: 核心引擎 — 最简 Agent Loop](#layer-1-demo)
3. [Layer 2 Demo: 扩展能力 — Skills + Hooks + MCP](#layer-2-demo)
4. [Layer 3 Demo: 交互层 — 绕过 TUI 调试完整链路](#layer-3-demo)
5. [Layer 4 Demo: Autopilot — 自动化交付流水线](#layer-4-demo)
6. [综合调试技巧](#综合调试技巧)
7. [推荐断点位置](#推荐断点位置)

---

## 准备工作

```bash
# 1. 确认 deepseek provider 可用 (已在 session 中配好)
uv run oh provider list | grep deepseek

# 2. 项目根目录
cd /Users/longxuebin/ai_project_reposity/pthon/OpenHarness
```

**VS Code 调试配置**（`.vscode/launch.json`）：

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Debug Demo Script",
            "type": "debugpy",
            "request": "launch",
            "module": "openharness.my_debug_demo",
            "cwd": "${workspaceFolder}",
            "console": "integratedTerminal"
        }
    ]
}
```

---

## Layer 1 Demo

**目标**：跑通最简 Agent Loop，观察 `run_query` 和 `_execute_tool_call` 全流程

创建一个脚本 `src/openharness/my_debug_demo.py`，内容：

```python
"""Layer 1 Demo: 核心引擎 — 最简 Agent Loop 调试."""
import asyncio
from openharness.ui.runtime import build_runtime, start_runtime, close_runtime
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ToolExecutionStarted,
    ToolExecutionCompleted,
    ErrorEvent,
)


async def demo_layer1():
    """Layer 1: 核心引擎。给 Agent 一个需要调工具的任务，观察完整流程。"""
    print("=" * 60)
    print("Layer 1 Demo: 核心引擎 — Agent Loop + 工具执行")
    print("=" * 60)

    # ====== 断点 1: build_runtime 入口 ======
    # 内部执行: load_settings → resolve_api_client → load MCP servers
    #          → create_default_tool_registry → load skills → load plugins
    #          → load hooks → build system prompt → assemble QueryEngine
    bundle = await build_runtime(
        cwd=".",
        model="deepseek-chat",
        max_turns=3,
        permission_mode="full_auto",  # 本地调试跳过权限弹窗
    )
    await start_runtime(bundle)

    # ====== 断点 2: submit_message 入口 ======
    # 内部: append user message → build QueryContext → run_query()
    async for event in bundle.engine.submit_message(
        "列出当前目录下所有 .py 文件的前 5 行内容"
    ):
        if isinstance(event, ToolExecutionStarted):
            # ====== 断点 3: 工具开始执行 ======
            # 此时在 _execute_tool_call 入口, 即将走:
            #   PreToolUse Hook → PermissionChecker → tool.execute()
            print(f"  [工具开始] {event.tool_name}: {event.tool_input}")

        elif isinstance(event, ToolExecutionCompleted):
            # ====== 断点 4: 工具执行完成 ======
            # 此时 PostToolUse Hook 已执行完毕
            output_preview = (event.output or "")[:120]
            print(f"  [工具完成] {event.tool_name} → {output_preview}...")

        elif isinstance(event, AssistantTextDelta):
            print(event.text, end="", flush=True)

        elif isinstance(event, AssistantTurnComplete):
            print(f"\n  [轮次完成] stop_reason={event.message.stop_reason}")

        elif isinstance(event, ErrorEvent):
            print(f"\n  [错误] {event.message}")

    await close_runtime(bundle)
    print("\n" + "=" * 60)
    print("Layer 1 Demo 完成!\n")


if __name__ == "__main__":
    asyncio.run(demo_layer1())
```

**运行**：

```bash
uv run python -m openharness.my_debug_demo
```

**观察点**：
- `build_runtime` 内部走了 settings → api_client → tools → skills → hooks → QueryEngine 6 步装配
- `run_query` 的 while 循环每轮输出 `ToolExecutionStarted` / `ToolExecutionCompleted`
- Agent 先 `glob` 找到文件，再 `read` 读取内容，最后用文本回复

---

## Layer 2 Demo

**目标**：验证 skills/plugins/hooks/MCP 是否正确加载

```python
"""Layer 2 Demo: 扩展能力 — Skills + Hooks + Plugins + MCP 验证."""
import asyncio
from openharness.ui.runtime import build_runtime, start_runtime, close_runtime


async def demo_layer2():
    print("=" * 60)
    print("Layer 2 Demo: 扩展能力检查")
    print("=" * 60)

    bundle = await build_runtime(
        cwd=".",
        model="deepseek-chat",
        max_turns=1,
        permission_mode="full_auto",
    )
    await start_runtime(bundle)

    # ====== Skills 检查 ======
    print("\n[Skills] 已加载的技能:")
    skill_registry = bundle.skill_registry
    for name in sorted(skill_registry.list_names())[:15]:
        skill = skill_registry.get(name)
        source = skill.source if skill else "?"
        print(f"  - {name} (from {source})")
    print(f"  ... 共 {len(skill_registry.list_names())} 个")

    # ====== Hooks 检查 ======
    print("\n[Hooks] 已注册的 hook:")
    hook_executor = bundle.engine._hook_executor
    if hook_executor and hasattr(hook_executor, '_registry'):
        registry = hook_executor._registry
        for event_name, hook_list in registry._hooks.items():
            if hook_list:
                print(f"  {event_name}: {len(hook_list)} hooks")

    # ====== Plugins 检查 ======
    print("\n[Plugins] 已加载的插件:")
    plugins = bundle.plugins
    for p in plugins:
        status = "enabled" if p.enabled else "disabled"
        print(f"  - {p.manifest.name} v{p.manifest.version} [{status}]")
        if p.skills:
            print(f"    提供 skills: {[s.name for s in p.skills]}")
        if p.tools:
            print(f"    提供 tools: {[t.name for t in p.tools]}")

    # ====== MCP 检查 ======
    print("\n[MCP] MCP 管理器:")
    mcp = getattr(bundle, 'mcp_manager', None)
    if mcp:
        servers = getattr(mcp, '_servers', {})
        print(f"  已配置 {len(servers)} 个 MCP server")
        for name in servers:
            print(f"  - {name}")

    # ====== Tools 检查 ======
    print("\n[Tools] 已注册的工具 (前 20 个):")
    for i, tool_name in enumerate(bundle.tool_registry.list_tools()):
        print(f"  {i+1}. {tool_name}")
        if i >= 19:
            print(f"  ... 共 {len(bundle.tool_registry.list_tools())} 个工具")
            break

    await close_runtime(bundle)
    print("\nLayer 2 Demo 完成!")


if __name__ == "__main__":
    asyncio.run(demo_layer2())
```

**运行**：追加到 `my_debug_demo.py` 或单独运行。

**观察点**：
- Skills 来自 4 个源（bundled/user/project/plugin），数量取决于安装情况
- Hooks 可能为空（除非配置了自定义 hook），这是正常行为
- Plugins 来自 `.openharness/plugins/` 和用户目录
- Tools 应有 43+ 个

---

## Layer 3 Demo

**目标**：直接用 `run_print_mode`（`oh -p` 模式）绕过 TUI，观察完整事件流

```python
"""Layer 3 Demo: 交互层 — 绕过 TUI 调试."""
import asyncio
from openharness.ui.app import run_print_mode


async def demo_layer3():
    """Layer 3: 用 run_print_mode 模拟 oh -p，观察事件渲染."""
    print("=" * 60)
    print("Layer 3 Demo: run_print_mode (模拟 oh -p)")
    print("=" * 60)

    await run_print_mode(
        prompt="用一句话解释什么是 Python asyncio",
        cwd=".",
        model="deepseek-chat",
        max_turns=2,
        permission_mode="full_auto",
    )

    print("\nLayer 3 Demo 完成!")


if __name__ == "__main__":
    asyncio.run(demo_layer3())
```

**运行**：

```bash
uv run python -c "
import asyncio
from openharness.ui.app import run_print_mode
asyncio.run(run_print_mode(
    prompt='算一下 123 + 456',
    cwd='.',
    model='deepseek-chat',
    max_turns=2,
    permission_mode='full_auto',
))
"
```

**观察点**：
- `run_print_mode` 内部走 `build_runtime → submit_message → 渲染 StreamEvent`
- 逐 token 流式输出到 stdout，和 `oh -p "..."` 行为一致
- 如果 Agent 调用了工具（如 Python 解释器），会看到工具执行的实时输出

---

## Layer 4 Demo

**目标**：用 Autopilot 跑一个真正的"改代码 + 验证"任务

**前置**：已在之前的对话中创建好了：
- `src/openharness/autopilot/_demo_bug.py` — 含 bug 的代码
- `tests/test_autopilot/test_demo_bug.py` — 验证测试
- `.openharness/autopilot/autopilot_policy.yaml` — 执行策略
- `.openharness/autopilot/verification_policy.yaml` — 验证命令

**方式 1 — 通过 oh TUI**（推荐调试）：

```bash
uv run oh
```

在 TUI 中输入：

```
/autopilot add idea Fix NoneType crash in get_user_display_name :: When user is None, the function crashes with "TypeError: 'NoneType' object is not subscriptable". Add a null check at the start: if user is None, return "Anonymous". Keep existing behavior for normal users.
```

然后：

```
/autopilot run-next
```

**方式 2 — 纯 Python 调试**（可打断点）：

```python
"""Layer 4 Demo: Autopilot 自动化执行."""
import asyncio
from openharness.autopilot.service import RepoAutopilotStore
from pathlib import Path


async def demo_layer4():
    print("=" * 60)
    print("Layer 4 Demo: Autopilot 自动化交付")
    print("=" * 60)

    store = RepoAutopilotStore(Path.cwd())

    # 1. 录入任务
    card, created = store.enqueue_card(
        source_kind="manual_idea",
        title="Fix NoneType crash in get_user_display_name",
        body=(
            "When user is None, the function crashes with "
            "TypeError: 'NoneType' object is not subscriptable. "
            "Add a null check at the start: if user is None, return 'Anonymous'."
        ),
    )
    status = "新建" if created else "已存在"
    print(f"\n[任务录入] {card.id} ({status}): {card.title}")

    # 2. 查看队列
    print("\n[队列状态]")
    stats = store.stats()
    for s in ("queued", "running", "completed", "failed"):
        print(f"  {s}: {stats.get(s, 0)}")

    # 3. 执行 — 这会走完整的 run_card 流程
    # ====== 断点 5: run_card 入口 ======
    # 内部: preparing(worktree) → running(_run_agent_prompt → submit_message → run_query)
    #       → verifying(_run_verification_steps → subprocess.run)
    #       → repairing(失败则重试) → git commit
    print(f"\n[执行中] run_card({card.id})...")
    result = await store.run_next()
    print(f"\n[结果] {result.card_id} → {result.status}")
    print(f"  run report: {result.run_report_path}")
    print(f"  verification report: {result.verification_report_path}")
    if result.verification_steps:
        for step in result.verification_steps:
            icon = "✓" if step.status == "success" else "✗"
            print(f"  {icon} {step.command} (rc={step.returncode})")


if __name__ == "__main__":
    asyncio.run(demo_layer4())
```

**重要**：`store.run_next()` 会创建 git worktree + 调 LLM + 跑 subprocess 验证。如果项目未 commit 当前变更，worktree 创建可能失败。建议先 `git stash` 或 commit。

**观察点**：
- `run_card` 内部状态机: preparing → running → verifying → (repairing?) → completed
- `_run_agent_prompt` 内部又走了完整的 `build_runtime → submit_message → run_query`
- `_run_verification_steps` 用 `subprocess.run` 独立执行验证命令
- 如果验证失败，`_prepare_repair_prompt` 把失败信息塞回 prompt 重试

---

## 综合调试技巧

### 1. 打印所有 StreamEvent（最完整的可观测性）

```python
async for event in bundle.engine.submit_message("你的 prompt"):
    print(f"[{type(event).__name__}] {event}")
```

### 2. 拦截_ execute_tool_call 看权限决策

在 VS Code 中对以下位置打断点：

```
engine/query.py:928   # PreToolUse Hook
engine/query.py:970   # PermissionChecker.evaluate()
engine/query.py:1011  # tool.execute()
```

### 3. 用 dry-run 不调 API 验证装配流程

```python
# 创建一个不调 API 的 mock client 来验证 build_runtime 装配
from unittest.mock import AsyncMock
mock_client = AsyncMock()
mock_client.stream_message.return_value = []
bundle = await build_runtime(cwd=".", api_client=mock_client, permission_mode="full_auto")
# 验证 bundle 里的所有组件都正常
print(bundle.tool_registry.list_tools())
```

---

## 推荐断点位置

按优先级排列，覆盖 4 层核心能力：

| 优先级 | 文件:行号 | 方法/位置 | 捕捉什么 |
|--------|----------|----------|---------|
| ★★★ | `engine/query.py:723` | `run_query` while 循环入口 | Agent Loop 每轮开始 |
| ★★★ | `engine/query.py:859` | `tool_calls = final_message.tool_uses` | LLM 决定调工具 |
| ★★★ | `engine/query.py:928` | `_execute_tool_call` PreHook | 工具执行前拦截 |
| ★★★ | `engine/query.py:970` | `PermissionChecker.evaluate()` | 权限决策 |
| ★★ | `engine/query_engine.py:165` | `QueryEngine.submit_message()` | 用户输入入口 |
| ★★ | `autopilot/service.py:718` | `run_card` for 循环 | Autopilot 每轮开始 |
| ★★ | `autopilot/service.py:824` | `_run_verification_steps()` | 验证命令执行 |
| ★ | `ui/runtime.py:245` | `build_runtime()` | 完整装配流程 |
| ★ | `hooks/executor.py:64` | `HookExecutor.execute()` | Hook 触发 |

**最佳调试起点**：Layer 1 的 `demo_layer1()` 脚本只依赖 deepseek provider 和项目运行环境，代码最简、覆盖最广，建议从这里开始。
