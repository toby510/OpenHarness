"""OpenHarness 4 层架构调试 Demo.

右键 Debug 直接跑，默认执行综合案例 (4 层全串联)。
需要单独调试某一层时，把 main() 里对应的注释打开即可。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from openharness.ui.runtime import build_runtime, start_runtime, close_runtime
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ToolExecutionStarted,
    ToolExecutionCompleted,
    ErrorEvent,
)


# ═══════════════════════════════════════════════════════════════════════
# Layer 1 Demo: 核心引擎 — 最简 Agent Loop
# ═══════════════════════════════════════════════════════════════════════

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
        max_turns=5,
        permission_mode="full_auto",
    )
    await start_runtime(bundle)

    # ====== 断点 2: submit_message 入口 ======
    # 内部: append user message → build QueryContext → run_query()
    async for event in bundle.engine.submit_message(
        "用 glob 工具找到 src/openharness/engine/ 目录下所有 .py 文件，"
        "然后用 read 工具读取 query.py 的前 20 行"
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
            print(f"\n  [轮次完成] tool_uses={len(event.message.tool_uses)}, text_len={len(event.message.text)}")

        elif isinstance(event, ErrorEvent):
            print(f"\n  [错误] {event.message}")

    await close_runtime(bundle)
    print("\n" + "=" * 60)
    print("Layer 1 Demo 完成!\n")


# ═══════════════════════════════════════════════════════════════════════
# Layer 2 Demo: 扩展能力 — Skills + Hooks + Plugins + MCP 验证
# ═══════════════════════════════════════════════════════════════════════

async def demo_layer2():
    """Layer 2: 扩展能力 — Skills + Hooks + Plugins + MCP 验证."""
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
    print("\n[Skills] 已加载的技能 (来自 extra_skill_dirs):")
    dirs = bundle.extra_skill_dirs
    print(f"  skill_dirs: {dirs if dirs else '(项目默认)'}")

    # ====== Hooks 检查 ======
    print(f"\n[Hooks] {bundle.hook_summary()}")

    # ====== Plugins 检查 ======
    print(f"\n[Plugins] {bundle.plugin_summary()}")

    # ====== MCP 检查 ======
    print(f"\n[MCP] {bundle.mcp_summary()}")

    # ====== Tools 检查 ======
    print("\n[Tools] 已注册的工具 (前 20 个):")
    for i, tool_name in enumerate(bundle.tool_registry.list_tools()):
        print(f"  {i+1}. {tool_name}")
        if i >= 19:
            print(f"  ... 共 {len(bundle.tool_registry.list_tools())} 个工具")
            break

    await close_runtime(bundle)
    print("\nLayer 2 Demo 完成!")


# ═══════════════════════════════════════════════════════════════════════
# Layer 3 Demo: 交互层 — 绕过 TUI 调试完整链路
# ═══════════════════════════════════════════════════════════════════════

async def demo_layer3():
    """Layer 3: 用 run_print_mode 模拟 oh -p，观察事件渲染."""
    from openharness.ui.app import run_print_mode

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


# ═══════════════════════════════════════════════════════════════════════
# Layer 4 Demo: Autopilot — 自动化交付流水线
# ═══════════════════════════════════════════════════════════════════════

async def demo_layer4(run: bool = False):
    """Layer 4: Autopilot 自动化执行.

    Args:
        run: 设为 True 才真正执行 run_card()，否则仅录入任务并展示队列状态。
             因为 run_card 会创建 git worktree + 调 LLM + subprocess 验证，
             需要 git 状态干净 (先 git stash 或 commit)。
    """
    from openharness.autopilot.service import RepoAutopilotStore

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

    if not run:
        print(
            "\n[跳过执行] 仅录入任务。要用 autopilot 执行修复，加 --layer 4 --run 参数。\n"
            "注意: run_card() 会创建 git worktree，请先确保 git 状态干净 (git stash / commit)。"
        )
        return

    # 3. 执行 — 走完整的 run_card 流程
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

    print("\nLayer 4 Demo 完成!")


# ═══════════════════════════════════════════════════════════════════════
# ★ 综合案例: 4 层串联 — "发现 Bug → Agent 修复 → 验证通过" 全流程
# ═══════════════════════════════════════════════════════════════════════

BUG_FILE = Path(__file__).parent / "autopilot" / "_demo_bug.py"
TEST_FILE = Path(__file__).parent.parent.parent / "tests" / "test_autopilot" / "test_demo_bug.py"

# 确保 bug 文件处于 Broken 状态，防止上次运行已修复
BROKEN_CODE = '''def get_user_display_name(user: dict | None) -> str:
    return user["name"].upper()
'''


async def demo_all_layers():
    """综合案例: 串联 4 层核心能力，一次跑通"发现 → 修复 → 验证"闭环.

    Layer 1 (引擎):    build_runtime → submit_message → Agent Loop 调工具
    Layer 2 (扩展):    Skills/Plugins/Tools 加载 + PreToolUse/PostToolUse Hooks 拦截
    Layer 3 (交互):    逐事件流式渲染，模拟 TUI 输出
    Layer 4 (应用):    pytest 前置验证(失败) → Agent 修复 → pytest 后置验证(通过)
    """
    import subprocess

    print("=" * 70)
    print("★ 综合案例: 4 层全串联 — Bug 发现 → Agent 修复 → 验证通过")
    print("=" * 70)

    # ── Phase 0: 重置 + 前置验证 (Layer 4 模式) ─────────────────────
    print("\n[Phase 0] 重置 bug 文件 + 前置验证...")
    BUG_FILE.write_text(BROKEN_CODE)

    result = subprocess.run(
        ["uv", "run", "pytest", "-q", str(TEST_FILE)],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent,
    )
    print(f"  pytest 前置 (期望 FAIL): rc={result.returncode}")
    for line in result.stdout.splitlines()[-4:]:
        print(f"  {line}")

    # ── Phase 1: build_runtime (Layer 1 入口) ─────────────────────────
    # 内部走完: settings → api_client → tools → skills → plugins
    #          → hooks → MCP → system_prompt → QueryEngine 完整装配
    print("\n[Phase 1] build_runtime 全量装配 (Layer 1 入口)...")
    bundle = await build_runtime(
        cwd=".",
        model="deepseek-chat",
        max_turns=8,
        permission_mode="full_auto",
    )
    await start_runtime(bundle)

    # ── Phase 2: 扩展能力快照 (Layer 2) ───────────────────────────────
    print("\n[Phase 2] 扩展能力快照 (Layer 2)...")
    print(f"  Tools:   {len(bundle.tool_registry.list_tools())} 个已注册")
    plugins = bundle.current_plugins()
    print(f"  Plugins: {len(plugins)} 个已加载")
    print(f"  Hooks:   {bundle.hook_summary()[:120]}")
    print(f"  MCP:     {bundle.mcp_summary()[:120]}")

    # ── Phase 3: Agent 修复 (Layer 1 Agent Loop + Layer 3 流式渲染) ──
    print("\n[Phase 3] Agent 执行修复 (Layer 1 Loop + Layer 3 流式)...")
    print("-" * 70)

    prompt = (
        f"Bug 文件 `{BUG_FILE}` 中 get_user_display_name 函数在传入 None 时会崩溃。\n\n"
        "请你:\n"
        "1. 先 read 文件理解当前代码\n"
        "2. 用 edit 工具在最开头加上 null 检查: if user is None: return 'Anonymous'\n"
        "3. 改完后用 Bash 工具跑一次 pytest 验证修改是否正确:\n"
        f"   uv run pytest -q {TEST_FILE}\n\n"
        "一次性完成修改+验证，不要反复确认。"
    )

    tool_count = 0
    async for event in bundle.engine.submit_message(prompt):
        # Layer 3 风格: 每种事件类型对应 TUI 的一类渲染
        if isinstance(event, ToolExecutionStarted):
            tool_count += 1
            # PreToolUse Hook 已执行 (Layer 2)
            input_preview = str(event.tool_input)[:500]
            print(f"  [{tool_count}] 工具开始>> {event.tool_name}: {input_preview}")

        elif isinstance(event, ToolExecutionCompleted):
            # PostToolUse Hook 已执行 (Layer 2)
            output_preview = (event.output or "")[:500]
            print(f"  [{tool_count}] 工具完成<< {event.tool_name}: {output_preview}")

        elif isinstance(event, AssistantTextDelta):
            print(event.text, end="", flush=True)

        elif isinstance(event, AssistantTurnComplete):
            print(f"\n  [LLM思考结束] tool_uses={len(event.message.tool_uses)}, text_len={len(event.message.text)}")

        elif isinstance(event, ErrorEvent):
            print(f"\n  [ERROR] {event.message}")

    print("\n" + "-" * 70)
    print(f"  共执行 {tool_count} 次工具调用")

    await close_runtime(bundle)

    # ── Phase 4: 后置验证 (Layer 4 模式) ──────────────────────────────
    print("\n[Phase 4] 后置验证 (Layer 4 验证模式)...")
    result = subprocess.run(
        ["uv", "run", "pytest", "-q", str(TEST_FILE)],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent,
    )
    passed = result.returncode == 0
    icon = "✓ 通过" if passed else "✗ 失败"
    print(f"  pytest: {icon} (rc={result.returncode})")
    for line in result.stdout.splitlines():
        print(f"  {line}")

    # 顺便展示修复后的代码
    print(f"\n  修复后代码:\n{BUG_FILE.read_text()}")

    # ── 总结 ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("4 层覆盖总结:")
    print(f"  Layer 1 (引擎):    build_runtime → submit_message → Agent Loop ({tool_count} 次工具调用)")
    print(f"  Layer 2 (扩展):    Skills/Tools/Plugins 全量加载 + Hook 拦截每次工具执行")
    print(f"  Layer 3 (交互):    StreamEvent 逐事件流式渲染 (共 {tool_count} 轮)")
    print(f"  Layer 4 (应用):    前置验证(fail) → Agent 修复 → 后置验证({'pass' if passed else 'fail'})")
    print("=" * 70)


# ═══════════════════════════════════════════════════════════════════════
# 综合调试: 打印所有 StreamEvent
# ═══════════════════════════════════════════════════════════════════════

async def demo_debug_all_events():
    """最完整的可观测性: 打印所有 StreamEvent 类型及其内容."""
    print("=" * 60)
    print("综合调试: 打印所有 StreamEvent")
    print("=" * 60)

    bundle = await build_runtime(
        cwd=".",
        model="deepseek-chat",
        max_turns=2,
        permission_mode="full_auto",
    )
    await start_runtime(bundle)

    async for event in bundle.engine.submit_message("计算 123 + 456"):
        print(f"[{type(event).__name__}] {event}")

    await close_runtime(bundle)
    print("\n综合调试完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 5: Hooks + 权限体系 — 6 层决策链完整观测
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_hooks_permission():
    """Hooks 拦截 + 权限 6 层决策链 完整观测.

    不调 LLM，纯 API 层验证：
    - HookExecutor 的 PreToolUse/PostToolUse/Stop 事件执行
    - PermissionChecker 的 6 层决策优先级
    - Command/Prompt/HTTP 三种 hook 类型的执行差异
    """
    from openharness.config.settings import load_settings
    from openharness.hooks.events import HookEvent
    from openharness.hooks.executor import HookExecutor, HookExecutionContext
    from openharness.hooks.loader import HookRegistry, load_hook_registry
    from openharness.hooks.schemas import CommandHookDefinition, HookDefinition
    from openharness.permissions.checker import PermissionChecker
    from openharness.permissions.modes import PermissionMode

    print("=" * 70)
    print("进阶 Demo 5: Hooks + 权限体系")
    print("=" * 70)

    # ── Part A: Hook 系统 ─────────────────────────────────────────
    print("\n[Part A] Hook 拦截链演示")

    # 构造一个包含 command hook 的 HookRegistry
    registry = HookRegistry()

    # 注册 PreToolUse hook: 拦截 bash 工具，打印日志
    pre_hook = CommandHookDefinition(
        type="command",
        command="echo '[HOOK] PreToolUse 拦截: tool=$OPENHARNESS_HOOK_EVENT'",
        matcher="bash",  # 只匹配 bash 工具
        timeout_seconds=5,
    )
    registry.register(HookEvent.PRE_TOOL_USE, pre_hook)

    # 注册 PostToolUse hook: 记录所有工具执行结果
    post_hook = CommandHookDefinition(
        type="command",
        command="echo '[HOOK] PostToolUse: 工具执行完毕'",
        timeout_seconds=5,
        matcher=None,  # 匹配所有工具
    )
    registry.register(HookEvent.POST_TOOL_USE, post_hook)

    print(f"  PreToolUse hooks:  {len(registry.get(HookEvent.PRE_TOOL_USE))}")
    print(f"  PostToolUse hooks: {len(registry.get(HookEvent.POST_TOOL_USE))}")

    # 用临时 API client 构造 HookExecutor
    from unittest.mock import AsyncMock

    mock_client = AsyncMock()
    mock_client.stream_message.return_value = []
    executor = HookExecutor(
        registry,
        HookExecutionContext(
            cwd=Path.cwd(),
            api_client=mock_client,
            default_model="deepseek-chat",
        ),
    )

    print("\n  执行 PreToolUse hook (针对 bash 工具):")
    result = await executor.execute(
        HookEvent.PRE_TOOL_USE,
        {"event": "pre_tool_use", "tool_name": "bash", "tool_input": {"command": "ls"}},
    )
    print(f"    blocked={result.blocked}, results={len(result.results)}")
    for r in result.results:
        print(f"    → hook_type={r.hook_type}, success={r.success}, output={r.output[:80]}")

    print("\n  执行 PreToolUse hook (针对 glob 工具，matcher 不匹配):")
    result = await executor.execute(
        HookEvent.PRE_TOOL_USE,
        {"event": "pre_tool_use", "tool_name": "glob", "tool_input": {"pattern": "*.py"}},
    )
    print(f"    blocked={result.blocked}, results={len(result.results)} (matcher 不匹配, hook 跳过)")

    # ── Part B: 权限 6 层决策链 ──────────────────────────────────
    print("\n[Part B] PermissionChecker 6 层决策链")

    settings = load_settings()
    checker = PermissionChecker(settings.permission)

    test_cases = [
        # (tool_name, is_read_only, file_path, command, expected)
        ("read_file", True, "src/main.py", None, "允许: 只读工具"),
        ("bash", False, None, "ls -la", "确认: 变更工具 default 模式"),
        ("write_file", False, "/etc/.ssh/authorized_keys", None, "拒绝: 敏感路径 Layer 1"),
        ("write_file", False, ".env", None, "确认: 非敏感路径"),  # user might allow
    ]

    print("  决策链层级: Sensitive → DenyList → AllowList → PathRules → CommandRules → Mode")
    for tool, ro, path, cmd, expected in test_cases:
        decision = checker.evaluate(
            tool_name=tool,
            is_read_only=ro,
            file_path=path,
            command=cmd,
        )
        icon = "✓" if decision.allowed else "✗"
        print(f"  {icon} {tool} path={path or '-'}: "
              f"allowed={decision.allowed}, confirm={decision.requires_confirmation}, "
              f"reason={decision.reason[:60]}")

    # 演示 full_auto 模式差异
    print("\n  切换到 full_auto 模式:")
    from openharness.config.settings import PermissionSettings

    full_auto_settings = settings.model_copy(update={
        "permission": PermissionSettings(mode=PermissionMode.FULL_AUTO),
    })
    checker_auto = PermissionChecker(full_auto_settings.permission)
    for tool, ro, path, cmd, expected in [
        ("bash", False, None, "rm -rf /tmp/test", "允许: full_auto"),
    ]:
        decision = checker_auto.evaluate(tool_name=tool, is_read_only=ro, file_path=path, command=cmd)
        print(f"  {tool}: allowed={decision.allowed}, confirm={decision.requires_confirmation}")

    print("\n进阶 Demo 5 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 6: 多 Agent 协调 — 用 InProcessBackend 并行派活
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_multi_agent():
    """多 Agent 协调: spawn 子 Agent → mailbox IPC → 收集结果.

    使用 InProcessBackend 在同进程内 spawn 子 Agent (asyncio.Task)，
    无需额外子进程。演示完整的 Agent 生命周期。
    **注意**: 此 demo 不传 query_context，子 Agent 只走 stub 分支（不调 LLM）。
    要体验真实 LLM 调用的 SubAgent 协作，请运行 demo_advanced_multi_agent_real()。
    """
    from openharness.config.settings import load_settings
    from openharness.swarm.in_process import InProcessBackend
    from openharness.swarm.types import TeammateSpawnConfig
    from openharness.swarm.mailbox import TeammateMailbox, create_user_message

    print("=" * 70)
    print("进阶 Demo 6: 多 Agent 协调 — InProcessBackend + Mailbox IPC (stub)")
    print("=" * 70)

    backend = InProcessBackend()
    settings = load_settings()

    # ── 创建 2 个子 Agent ──────────────────────────────────────────
    print("\n[创建] 启动 2 个 InProcess Agent...")

    config1 = TeammateSpawnConfig(
        name="researcher",
        team="demo",
        prompt=(
            "You are a research agent. Investigate the file "
            "src/openharness/engine/query.py and report: "
            "1) what function runs the Agent Loop? "
            "2) how many tool execution phases are there? "
            "Keep your answer under 100 words."
        ),
        cwd=str(Path.cwd()),
        parent_session_id="demo_session",
        model=settings.model,
        system_prompt="You are a research agent. Answer concisely based on file contents.",
        task_type="local_agent",
    )

    config2 = TeammateSpawnConfig(
        name="reviewer",
        team="demo",
        prompt=(
            "Read the file src/openharness/permissions/checker.py and report "
            "the layers of the permission decision chain. Keep under 100 words."
        ),
        cwd=str(Path.cwd()),
        parent_session_id="demo_session",
        model=settings.model,
        system_prompt="You are a code reviewer. Be precise and concise.",
        task_type="local_agent",
    )

    result1 = await backend.spawn(config1)
    result2 = await backend.spawn(config2)
    print(f"  {result1.agent_id}: success={result1.success}")
    print(f"  {result2.agent_id}: success={result2.success}")

    # ── Mailbox 演示 ───────────────────────────────────────────────
    print("\n[Mailbox] 跨 Agent 消息传递演示")

    mailbox1 = TeammateMailbox(team_name="demo", agent_id="researcher")
    mailbox2 = TeammateMailbox(team_name="demo", agent_id="reviewer")

    # 写入消息
    msg = create_user_message(sender="leader", recipient="researcher", content="请加速完成")
    await mailbox1.write(msg)
    print(f"  写入消息: {msg.id} → researcher")

    # 读取消息
    unread = await mailbox1.read_all(unread_only=True)
    print(f"  researcher 收件箱: {len(unread)} 条未读")
    for m in unread:
        print(f"    [{m.sender}] {m.payload.get('content', '')}")

    # ── 查询活跃 Agent ────────────────────────────────────────────
    print("\n[状态] 活跃 Agent 列表:")
    active = backend.active_agents()
    for agent_id in active:
        print(f"  - {agent_id}")

    # ── 清理 ───────────────────────────────────────────────────────
    print("\n[清理] 关闭所有 Agent...")
    await backend.shutdown_all()
    print("  所有 Agent 已关闭")

    print("\n进阶 Demo 6 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 6b: 真实 SubAgent 协作 — 主 Agent + 2 子 Agent 全部调 LLM
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_multi_agent_real():
    """真实 SubAgent 协作: 覆盖 4 种主 Agent ↔ 子 Agent 通信模式.

    Case 1 — 串行 vs 并行: 同样任务先串行再并行执行，对比耗时。
    Case 2 — Mailbox 动态推送: 子 Agent 运行中途，主 Agent 通过 mailbox 追加新任务。
    Case 3 — 优雅取消: 主 Agent 对运行中的子 Agent 发 cancel 信号，子 Agent 完成当前
              turn 后退出。
    Case 4 — 综合协作: 2 子 Agent 并行调研不同模块，主 Agent 汇总交叉分析。

    核心链路:
    - 主 Agent: build_runtime → QueryEngine.submit_message
    - 子 Agent: start_in_process_teammate + QueryContext → 真实 LLM 调用
    - 跨 Agent 通信: TeammateMailbox (文件 IPC) + TeammateAbortController (取消信号)
    """
    import tempfile
    import time as _time

    from openharness.engine.query import QueryContext
    from openharness.swarm.in_process import start_in_process_teammate, TeammateAbortController
    from openharness.swarm.mailbox import TeammateMailbox, create_user_message
    from openharness.swarm.types import TeammateSpawnConfig
    from openharness.tools.base import ToolRegistry
    from openharness.tools.file_read_tool import FileReadTool
    from openharness.tools.file_write_tool import FileWriteTool
    from openharness.tools.glob_tool import GlobTool
    from openharness.tools.grep_tool import GrepTool

    print("=" * 70)
    print("进阶 Demo 6b: 真实 SubAgent 协作 — 4 种通信模式全覆盖")
    print("=" * 70)

    # ══════════════════════════════════════════════════════════════════
    # Setup: 构建主 Agent + 共享组件
    # ══════════════════════════════════════════════════════════════════
    print("\n[Setup] 构建主 Agent (build_runtime)...")
    bundle = await build_runtime(
        cwd=".",
        model="deepseek-chat",
        max_turns=5,
        permission_mode="full_auto",
    )
    await start_runtime(bundle)

    tmpdir = Path(tempfile.mkdtemp(prefix="subagent_demo_"))
    engine = bundle.engine
    api_client = engine._api_client       # 子 Agent 复用同一 API 连接
    base_checker = engine._permission_checker

    # todo @Toby注释: [工厂函数] 抽取公共逻辑——创建子 Agent 所需的 4 件套:
    # ToolRegistry / QueryContext / TeammateSpawnConfig / TeammateAbortController。
    # 每个子 Agent 可以有不同的 prompt 和 tools，但共享 api_client 和 permission_checker。
    def _make_subagent(name: str, prompt: str, tools: list, system_prompt: str = ""):
        """创建子 Agent 的四件套: (registry, ctx, config, abort_controller)."""
        reg = ToolRegistry()
        for t in tools:
            reg.register(t)
        ctx = QueryContext(
            api_client=api_client,
            tool_registry=reg,
            permission_checker=base_checker,
            cwd=Path.cwd(),
            model="deepseek-chat",
            max_tokens=4096,
            max_turns=8,
            tool_metadata={},
            system_prompt=system_prompt,
        )
        config = TeammateSpawnConfig(
            name=name,
            team="demo",
            prompt=prompt,
            cwd=str(Path.cwd()),
            parent_session_id="demo_real_session",
            system_prompt=system_prompt,
        )
        abort = TeammateAbortController()
        return reg, ctx, config, abort

    # ══════════════════════════════════════════════════════════════════
    # Case 1: 串行 vs 并行 — 同样任务，对比耗时
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 1: 串行 vs 并行 — 同样 2 个任务，对比执行耗时")
    print("=" * 70)

    serial_report_a = tmpdir / "serial_a.txt"
    serial_report_b = tmpdir / "serial_b.txt"
    parallel_report_a = tmpdir / "parallel_a.txt"
    parallel_report_b = tmpdir / "parallel_b.txt"

    task_a_prompt = (
        f"Read the file src/openharness/engine/query.py (first 100 lines only). "
        f"Write a ONE-sentence summary of what run_query does to {serial_report_a}. "
        f"Do NOT read other files. Only 1 turn."
    )
    task_b_prompt = (
        f"Read the file src/openharness/permissions/checker.py (first 80 lines only). "
        f"Write a ONE-sentence summary of what PermissionChecker.evaluate does "
        f"to {serial_report_b}. Do NOT read other files. Only 1 turn."
    )

    base_tools = [FileReadTool(), FileWriteTool()]

    # ── 串行 ──────────────────────────────────────────────────────
    print("\n[串行] 依次执行 worker_a → worker_b...")
    t0 = _time.time()

    _, ctx_a, cfg_a, abort_a = _make_subagent(
        "worker_a", task_a_prompt, base_tools,
        "You read a file and write ONE sentence. Do nothing else.",
    )
    await start_in_process_teammate(
        config=cfg_a, agent_id="worker_a@demo",
        abort_controller=abort_a, query_context=ctx_a,
    )
    t1 = _time.time()
    print(f"  worker_a 完成, 耗时 {t1 - t0:.1f}s")

    # 注意: 这里改了 prompt 里的文件路径指向 serial_report_b
    _, ctx_b, cfg_b, abort_b = _make_subagent(
        "worker_b", task_b_prompt, base_tools,
        "You read a file and write ONE sentence. Do nothing else.",
    )
    await start_in_process_teammate(
        config=cfg_b, agent_id="worker_b@demo",
        abort_controller=abort_b, query_context=ctx_b,
    )
    t2 = _time.time()
    serial_time = t2 - t0
    print(f"  worker_b 完成, 耗时 {t2 - t1:.1f}s")
    print(f"  >> 串行总耗时: {serial_time:.1f}s")

    # ── 并行 ──────────────────────────────────────────────────────
    print("\n[并行] 同时启动 worker_c + worker_d (asyncio.gather)...")

    async def _run_worker(name, prompt, report_path):
        _, ctx, cfg, abort = _make_subagent(name, prompt, base_tools,
            "You read a file and write ONE sentence. Do nothing else.")
        await start_in_process_teammate(
            config=cfg, agent_id=f"{name}@demo",
            abort_controller=abort, query_context=ctx,
        )

    t0 = _time.time()
    await asyncio.gather(
        _run_worker("worker_c",
            f"Read src/openharness/engine/query.py (first 100 lines). "
            f"Write ONE sentence summary to {parallel_report_a}.",
            parallel_report_a),
        _run_worker("worker_d",
            f"Read src/openharness/permissions/checker.py (first 80 lines). "
            f"Write ONE sentence summary to {parallel_report_b}.",
            parallel_report_b),
    )
    parallel_time = _time.time() - t0
    print(f"  >> 并行总耗时: {parallel_time:.1f}s")

    speedup = serial_time / parallel_time if parallel_time > 0 else 0
    print(f"\n  对比: 串行 {serial_time:.1f}s vs 并行 {parallel_time:.1f}s "
          f"(加速比 {speedup:.1f}x)")

    # ══════════════════════════════════════════════════════════════════
    # Case 2: Mailbox 动态推送 — 子 Agent 运行中追加新任务
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 2: Mailbox 动态推送 — 主 Agent 向运行中的子 Agent 追加任务")
    print("=" * 70)

    mailbox_report = tmpdir / "mailbox_result.txt"

    # todo @Toby注释: [Mailbox 推送模式] 主 Agent 通过 TeammateMailbox.write()
    # 向子 Agent 的文件 mailbox 写入 user_message。子 Agent 在 _run_query_loop 的
    # 每轮 LLM 响应之间调用 _drain_mailbox() 拉取新消息，注入到对话历史中。
    _, ctx_mb, cfg_mb, abort_mb = _make_subagent(
        "mailbox_worker",
        (
            f"Phase 1: read src/openharness/engine/query.py (first 50 lines). "
            f"Write a 1-sentence summary to {mailbox_report}. "
            f"Then stop and wait — the leader may send more instructions."
        ),
        base_tools,
        "You are a worker. Complete each phase, then wait for further instructions. "
        "When you receive a new message, execute it.",
    )

    print("\n  启动子 Agent (asyncio.Task)...")
    # 包装为 asyncio.Task，这样可以在子 Agent 运行期间操作 mailbox
    mb_task = asyncio.create_task(
        start_in_process_teammate(
            config=cfg_mb, agent_id="mailbox_worker@demo",
            abort_controller=abort_mb, query_context=ctx_mb,
        ),
        name="mailbox_worker",
    )

    # 等待子 Agent 完成第一个任务（给它一些时间启动 + 执行）
    await asyncio.sleep(8)
    print("  主 Agent: 向 mailbox 推送追加任务...")

    # todo @Toby注释: [消息写入] create_user_message 创建一条 user_message 类型的
    # MailboxMessage，写入子 Agent 的文件 mailbox。子 Agent 的 _drain_mailbox
    # 会在下轮 LLM 响应结束后拉取并注入到对话历史。
    mailbox = TeammateMailbox(team_name="demo", agent_id="mailbox_worker")
    msg = create_user_message(
        sender="leader",
        recipient="mailbox_worker",
        content=(
            f"New task from leader: also read src/openharness/swarm/in_process.py "
            f"(first 60 lines) and APPEND a 1-sentence summary of "
            f"_run_query_loop to {mailbox_report}. "
            f"Use read_file then write_file to append (not overwrite)."
        ),
    )
    await mailbox.write(msg)
    print(f"  消息已写入 mailbox: {msg.id}")

    # 等待子 Agent 完成（它会拉取 mailbox 消息并执行）
    await mb_task
    print(f"  子 Agent 完成! 报告内容:")
    if mailbox_report.exists():
        print(f"    {mailbox_report.read_text()[:300]}")

    # ══════════════════════════════════════════════════════════════════
    # Case 3: 优雅取消 — 主 Agent 终止正在运行的子 Agent
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 3: 优雅取消 — 主 Agent 终止多步执行的子 Agent")
    print("=" * 70)

    cancel_report = tmpdir / "cancel_result.txt"
    cancel_report.write_text("(未完成)")

    _, ctx_c, cfg_c, abort_c = _make_subagent(
        "cancel_worker",
        (
            f"You have a multi-step task:\n"
            f"Step 1: read src/openharness/engine/query.py (first 80 lines), "
            f"write a 1-sentence summary to {cancel_report}\n"
            f"Step 2: read src/openharness/permissions/checker.py (first 80 lines), "
            f"APPEND a 1-sentence summary to {cancel_report}\n"
            f"Step 3: read src/openharness/swarm/in_process.py (first 80 lines), "
            f"APPEND a 1-sentence summary to {cancel_report}\n"
            f"Do each step sequentially, one at a time, with a separate tool call per step."
        ),
        base_tools,
        "You are a worker. Execute steps one at a time. Be brief.",
    )

    print("\n  启动多步子 Agent (asyncio.Task)...")
    cancel_task = asyncio.create_task(
        start_in_process_teammate(
            config=cfg_c, agent_id="cancel_worker@demo",
            abort_controller=abort_c, query_context=ctx_c,
        ),
        name="cancel_worker",
    )

    # 等子 Agent 完成 Step 1（约 8-12 秒），然后在 Step 2 中途取消
    await asyncio.sleep(10)
    print("  主 Agent: 发送优雅取消信号 (graceful cancel)...")

    # todo @Toby注释: [优雅取消] request_cancel(force=False) 设置 cancel_event，
    # 子 Agent 在 _run_query_loop 每轮 event 后检查 abort_controller.is_cancelled，
    # 发现信号后 return 退出循环。force=False 意味着不会立即 kill asyncio.Task，
    # 子 Agent 会完成当前正在执行的工具调用后才退出。
    abort_c.request_cancel(reason="主 Agent 决定终止任务", force=False)

    await cancel_task
    print(f"  子 Agent 已退出, 报告内容:")
    if cancel_report.exists():
        content = cancel_report.read_text()
        print(f"    {content[:300]}")
        # 确认是部分完成而非全部完成
        if "Step 1" in content and "Step 3" not in content.split("Step 2")[-1] if "Step 2" in content else True:
            print("  >> 确认: 仅完成部分步骤，优雅取消生效")

    # ══════════════════════════════════════════════════════════════════
    # Case 4: 综合协作 — 2 子 Agent 并行深度调研 + 主 Agent 交叉分析
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 4: 综合协作 — 2 子 Agent 深度调研 + 主 Agent 交叉分析")
    print("=" * 70)

    report1_path = tmpdir / "deep_explorer.md"
    report2_path = tmpdir / "deep_auditor.md"

    async def run_explorer():
        _, ctx, cfg, abort = _make_subagent(
            "explorer",
            (
                f"Investigate OpenHarness Agent Loop:\n"
                f"1. read_file src/openharness/engine/query.py (focus on "
                f"run_query ~line 632 and _execute_tool_call ~line 921)\n"
                f"2. grep for '_execute_tool_call' callers\n"
                f"3. Write report to {report1_path}: loop flow, pipeline phases, "
                f"exit conditions. Under 300 words."
            ),
            [FileReadTool(), GlobTool(), GrepTool(), FileWriteTool()],
            "You are a senior code explorer. Write a markdown report.",
        )
        t0 = _time.time()
        await start_in_process_teammate(
            config=cfg, agent_id="explorer@demo",
            abort_controller=abort, query_context=ctx,
        )
        print(f"  [explorer] 完成 ({_time.time() - t0:.1f}s)")

    async def run_auditor():
        _, ctx, cfg, abort = _make_subagent(
            "auditor",
            (
                f"Audit OpenHarness permissions:\n"
                f"1. read_file src/openharness/permissions/checker.py\n"
                f"2. grep for PermissionMode usage\n"
                f"3. Write report to {report2_path}: 6-layer chain, modes, "
                f"hook interaction. Under 300 words."
            ),
            [FileReadTool(), GlobTool(), GrepTool(), FileWriteTool()],
            "You are a security auditor. Write a markdown report.",
        )
        t0 = _time.time()
        await start_in_process_teammate(
            config=cfg, agent_id="auditor@demo",
            abort_controller=abort, query_context=ctx,
        )
        print(f"  [auditor] 完成 ({_time.time() - t0:.1f}s)")

    print("\n  并行启动 explorer + auditor...")
    t0 = _time.time()
    await asyncio.gather(run_explorer(), run_auditor())
    print(f"  并行总耗时: {_time.time() - t0:.1f}s")

    # 主 Agent 交叉分析
    print("\n  [主 Agent 交叉分析]")
    if report1_path.exists() and report2_path.exists():
        async for event in bundle.engine.submit_message(
            f"Read {report1_path} and {report2_path}. Summarize both modules, "
            f"explain how permissions intercept tool execution, and give "
            f"3 key architectural insights."
        ):
            if isinstance(event, ToolExecutionStarted):
                print(f"  [{event.tool_name}] {str(event.tool_input)[:80]}")
            elif isinstance(event, ToolExecutionCompleted):
                print(f"  [{event.tool_name}] → {(event.output or '')[:100]}")
            elif isinstance(event, AssistantTextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, AssistantTurnComplete):
                print(f"\n  [turn done]")
            elif isinstance(event, ErrorEvent):
                print(f"\n  [ERROR] {event.message}")

    # ══════════════════════════════════════════════════════════════════
    await close_runtime(bundle)
    print(f"\n\n全部 4 个 Case 完成! 报告保留在: {tmpdir}")
    print("=" * 70)


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 7: Autopilot 进阶 — 修复失败 → repair 重试 → 成功
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_autopilot():
    """Autopilot 进阶: 触发 repair loop.

    制造一个"第一次修复不完整"的场景：
    - Agent 需要同时修复函数体 + 添加类型注解
    - 验证命令: ruff check (类型检查) + pytest
    - 如果只修了一半，ruff 会报类型错误 → 触发 repair

    需要 git 状态干净 (worktree 隔离模式)。
    """
    import subprocess

    from openharness.autopilot.service import RepoAutopilotStore

    print("=" * 70)
    print("进阶 Demo 7: Autopilot 进阶 — Repair Loop")
    print("=" * 70)

    # 先用 git status 检查
    result = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=Path.cwd()
    )
    if result.stdout.strip():
        print("\n  [警告] git 有未提交变更，worktree 创建可能失败。")
        print("  建议先 git stash 或 commit。")
        print(result.stdout[:200])

    store = RepoAutopilotStore(Path.cwd())

    # 确保 bug 文件处于 Broken 状态
    bug_file = Path(__file__).parent / "autopilot" / "_demo_bug.py"
    bug_file.write_text(BROKEN_CODE)

    # 录入任务
    card, created = store.enqueue_card(
        source_kind="manual_idea",
        title="Fix NoneType crash AND add proper type hints",
        body=(
            "Two things to fix in _demo_bug.py:\n"
            "1. Add null check: when user is None, return 'Anonymous'\n"
            "2. Add proper return type annotation (-> str)\n"
            "The ruff check will fail if type annotation is missing."
        ),
    )
    print(f"\n[任务录入] {card.id}: {card.title}")

    # 确保 verification_policy 包含 ruff check
    vp_path = Path.cwd() / ".openharness" / "autopilot" / "verification_policy.yaml"
    original_vp = vp_path.read_text()
    need_restore = 'ruff check' not in original_vp

    print(f"\n[队列状态] {store.stats()}")
    print("\n[说明] run_card() 内部状态机:")
    print("  preparing → running → verifying → (失败?) → repairing → running → ...")
    print("  如需真正执行, 调用 demo_advanced_autopilot with run=True")
    print("  调用方式: await demo_advanced_autopilot(run_card=True)")

    if need_restore:
        print("\n  提示: verification_policy.yaml 需要包含 ruff check 以触发类型检查修复。"
              "当前已有 pytest 命令。")

    print("\n进阶 Demo 7 完成 (仅展示状态机结构，执行需 run_card=True)")


async def demo_advanced_autopilot_run():
    """真正执行 autopilot run_card."""
    from openharness.autopilot.service import RepoAutopilotStore

    store = RepoAutopilotStore(Path.cwd())
    bug_file = Path(__file__).parent / "autopilot" / "_demo_bug.py"
    bug_file.write_text(BROKEN_CODE)

    print("[执行] run_card 启动...")
    result = await store.run_next()
    print(f"\n[结果] {result.card_id} → {result.status}")
    for step in (result.verification_steps or []):
        icon = "✓" if step.status == "success" else "✗"
        print(f"  {icon} {step.command} (rc={step.returncode})")
    print("\n进阶 Demo 7 (run) 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 8: 自定义插件开发 — 从零构建 Plugin
# ═══════════════════════════════════════════════════════════════════════

PLUGIN_DEMO_DIR = Path(__file__).parent.parent.parent / ".openharness" / "plugins" / "demo-plugin"


def _ensure_demo_plugin() -> Path:
    """创建 demo 插件目录结构。"""
    PLUGIN_DEMO_DIR.mkdir(parents=True, exist_ok=True)

    # plugin.json
    manifest = PLUGIN_DEMO_DIR / "plugin.json"
    if not manifest.exists():
        manifest.write_text("""{
    "name": "demo-plugin",
    "version": "1.0.0",
    "description": "OpenHarness demo plugin with custom tool"
}
""")

    # tools 目录 + 自定义工具
    tools_dir = PLUGIN_DEMO_DIR / "tools"
    tools_dir.mkdir(exist_ok=True)
    tool_py = tools_dir / "demo_tool.py"
    if not tool_py.exists():
        tool_py.write_text('''"""Demo custom tool: echo."""
from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class EchoInput(BaseModel):
    message: str = Field(description="Message to echo back")
    repeat: int = Field(default=1, description="Number of repetitions")


class DemoTool(BaseTool):
    name = "demo_echo"
    description = "Echo a message back (demo custom plugin tool)"

    def is_read_only(self) -> bool:
        return True

    @property
    def input_model(self):
        return EchoInput

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> ToolResult:
        inp = EchoInput.model_validate(arguments)
        output = (inp.message + "\\n") * inp.repeat
        return ToolResult(output=output.strip())


def get_tool():
    return DemoTool()
''')

    # skills 目录 — 必须是 skills/<name>/SKILL.md 结构
    skill_dir = PLUGIN_DEMO_DIR / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md.write_text("""---
name: demo-skill
description: Demo skill that explains plugin architecture
user-invocable: true
---

# Demo Plugin Skill

This skill is loaded from a custom plugin. It demonstrates:
1. Skills can be bundled inside plugins
2. The plugin loader auto-discovers SKILL.md files
3. Skills show up in the skills system prompt section
""")

    return PLUGIN_DEMO_DIR


async def demo_advanced_custom_plugin():
    """自定义 Plugin: 从零构建 plugin.json + 自定义 tool + skill.

    演示: 创建插件 → 加载 → 检查 tools/skills 是否注册成功。
    """
    from openharness.plugins.loader import load_plugins
    from openharness.ui.runtime import build_runtime, close_runtime

    print("=" * 70)
    print("进阶 Demo 8: 自定义插件开发")
    print("=" * 70)

    # ── Part A: 创建插件 ───────────────────────────────────────────
    print("\n[Part A] 创建 demo 插件")
    plugin_dir = _ensure_demo_plugin()
    print(f"  路径: {plugin_dir}")
    for root, dirs, files in plugin_dir.walk():
        for f in files:
            print(f"  {root / f}")

    # ── Part B: 加载插件 ──────────────────────────────────────────
    print("\n[Part B] 加载插件 (allow_project_plugins=True)")
    from openharness.config.settings import load_settings

    s = load_settings()
    # 必须启用 allow_project_plugins 才能加载项目本地插件
    plugins = load_plugins(
        s.model_copy(update={"allow_project_plugins": True}),
        str(Path.cwd()),
        extra_roots=None,
    )
    demo = next((p for p in plugins if p.manifest.name == "demo-plugin"), None)

    if demo:
        print(f"  ✓ 发现插件: {demo.manifest.name} v{demo.manifest.version}")
        print(f"    enabled: {demo.enabled}")
        print(f"    tools:   {[t.name for t in demo.tools]}")
        print(f"    skills:  {[s.name for s in demo.skills]}")
    else:
        print("  ✗ 未发现 demo 插件! 检查 .openharness/plugins/ 目录")

    # ── Part C: 直接实例化并测试自定义工具 ─────────────────────────
    print("\n[Part C] 测试自定义 demo_echo 工具")
    from openharness.tools.base import ToolExecutionContext

    tool = demo.tools[0] if (demo and demo.tools) else None
    if tool:
        ctx = ToolExecutionContext(cwd=Path.cwd())
        result = await tool.execute({"message": "Hello from plugin!", "repeat": 2}, ctx)
        print(f"  输入: message='Hello from plugin!', repeat=2")
        print(f"  输出: {result.output}")

    print("\n进阶 Demo 8 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 9: MCP 集成 — 本地 stdio MCP Server
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_mcp():
    """MCP 集成: 启动本地 fake MCP Server → 连接 → 发现工具 → 调用。

    使用项目自带的 tests/fixtures/fake_mcp_server.py 作为 MCP Server。
    """
    import os
    import subprocess
    import sys

    from openharness.mcp.client import McpClientManager
    from openharness.mcp.types import McpStdioServerConfig

    print("=" * 70)
    print("进阶 Demo 9: MCP 集成 — stdio MCP Server")
    print("=" * 70)

    # 获取 fake_mcp_server.py 路径
    fixture_server = (
        Path(__file__).parent.parent.parent / "tests" / "fixtures" / "fake_mcp_server.py"
    )
    if not fixture_server.exists():
        print(f"  ✗ MCP server 文件不存在: {fixture_server}")
        return

    python_bin = sys.executable
    print(f"  Python: {python_bin}")
    print(f"  Server: {fixture_server}")

    # ── 配置并连接 ─────────────────────────────────────────────────
    print("\n[连接] 启动 MCP Client Manager...")
    config = McpStdioServerConfig(
        type="stdio",
        command=python_bin,
        args=[str(fixture_server)],
        env=None,
        cwd=None,
    )
    manager = McpClientManager({"fixture-demo": config})

    try:
        await manager.connect_all()
        print("  ✓ 连接成功!")

        # ── 查看状态 ───────────────────────────────────────────────
        print("\n[状态] MCP 连接状态:")
        for status in manager.list_statuses():
            print(f"  {status.name}: {status.state}")
            if status.tools:
                print(f"    tools ({len(status.tools)}):")
                for t in status.tools:
                    print(f"      - {t.name}: {t.description}")

        # ── 调用工具 ───────────────────────────────────────────────
        if manager.list_tools():
            print("\n[调用] 测试 MCP 工具调用:")
            try:
                result = await manager.call_tool(
                    server_name="fixture-demo",
                    tool_name="hello",
                    arguments={"name": "OpenHarness"},
                )
                print(f"  hello('OpenHarness') → {result}")
            except Exception as e:
                print(f"  调用异常: {e}")

        # ── 读取资源 ───────────────────────────────────────────────
        print("\n[资源] MCP 资源列表:")
        for status in manager.list_statuses():
            if status.resources:
                for r in status.resources:
                    print(f"  {r.name}: {r.uri} - {r.description}")

    finally:
        await manager.close()
        print("\n  连接已关闭")

    print("\n进阶 Demo 9 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 10: Memory 跨会话持久化
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_memory():
    """Memory 跨会话: 写入 → 持久化 → 读取 → 搜索。

    演示完整的记忆生命周期，可在多次运行间验证持久化。
    """
    from openharness.memory.manager import (
        add_memory_entry,
        list_memory_files,
        remove_memory_entry,
    )
    from openharness.memory.memdir import load_memory_prompt
    from openharness.memory.search import find_relevant_memories
    from openharness.memory.paths import get_project_memory_dir

    print("=" * 70)
    print("进阶 Demo 10: Memory 跨会话持久化")
    print("=" * 70)

    # ── 查看存储路径 ───────────────────────────────────────────────
    mem_dir = get_project_memory_dir(Path.cwd())
    print(f"\n[路径] 记忆存储目录: {mem_dir}")

    # ── 写入记忆 ──────────────────────────────────────────────────
    print("\n[写入] 添加 3 条记忆...")
    mem1 = add_memory_entry(
        Path.cwd(),
        "user_prefers_deepseek_chat",
        "User prefers deepseek-chat model for all debugging and daily development tasks.",
    )
    print(f"  ✓ {mem1.name}")

    mem2 = add_memory_entry(
        Path.cwd(),
        "project_focus_on_agent_loop",
        "The user is studying OpenHarness architecture, with focus on the Agent Loop "
        "(_execute_tool_call pipeline) and autopilot delivery flow.",
    )
    print(f"  ✓ {mem2.name}")

    mem3 = add_memory_entry(
        Path.cwd(),
        "code_style_use_type_hints",
        "User prefers using from __future__ import annotations everywhere.",
    )
    print(f"  ✓ {mem3.name}")

    # ── 查看所有记忆 ──────────────────────────────────────────────
    print("\n[列表] 当前所有记忆:")
    for f in list_memory_files(Path.cwd()):
        print(f"  - {f.name}")

    # ── 读取 MEMORY.md ─────────────────────────────────────────────
    print("\n[MEMORY.md] 系统提示词注入内容 (前 500 字符):")
    prompt = load_memory_prompt(Path.cwd(), max_entrypoint_lines=200)
    if prompt:
        print(f"  {prompt[:500]}")

    # ── 搜索记忆 ──────────────────────────────────────────────────
    print("\n[搜索] 语义搜索 'model preference':")
    results = find_relevant_memories("model preference", Path.cwd(), max_results=3)
    for r in results:
        print(f"  - {r.title}: {r.description}")

    # ── 清理测试记忆 ─────────────────────────────────────────────
    print("\n[清理] 删除测试记忆...")
    for name in ["user_prefers_deepseek_chat", "project_focus_on_agent_loop", "code_style_use_type_hints"]:
        removed = remove_memory_entry(Path.cwd(), name)
        print(f"  {'✓' if removed else '✗'} {name}")

    print("\n进阶 Demo 10 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 11: Coordinator 模式 — 对比 SubAgent 理解差异
# ═══════════════════════════════════════════════════════════════════════



async def demo_advanced_coordinator():
    """Coordinator 模式: LLM自主调度workers，subprocess执行，XML通知驱动。

    与 SubAgent (swarm) 模式的核心区别:
    ┌──────────────────┬─────────────────────────┬──────────────────────────┐
    │                  │ SubAgent 模式            │ Coordinator 模式          │
    ├──────────────────┼─────────────────────────┼──────────────────────────┤
    │ 谁决定 spawn     │ 代码显式调用             │ LLM 通过 agent tool 决定  │
    │                  │ start_in_process_teammate│                          │
    │ 执行后端         │ InProcessBackend         │ SubprocessBackend(硬编码) │
    │                  │ (同进程 asyncio task)    │ (独立 python 子进程)       │
    │ Worker 如何运行  │ 同进程 QueryContext       │ python -m openharness     │
    │                  │ + _run_query_loop()      │ --task-worker             │
    │ 结果返回方式     │ Mailbox JSON 文件         │ TaskNotification XML      │
    │                  │ + _drain_mailbox()       │ + drain_coordinator...()  │
    │ 激活条件         │ 无，手动调用即可         │ CLAUDE_CODE_COORDINATOR   │
    │                  │                          │ _MODE=1 环境变量          │
    │ 系统提示         │ 常规 CLAUDE.md prompt    │ 270行 coordinator 专用    │
    │                  │                          │ prompt (4阶段工作流)      │
    └──────────────────┴─────────────────────────┴──────────────────────────┘

    本 Demo 覆盖 3 个 Case:
    Case 1 — Coordinator 基础设施展示 (系统提示/用户上下文/工具限制/)
    Case 2 — agent tool → subprocess worker → drain → XML 通知 (真实执行)
    Case 3 — LLM 自主调度: 提交任务让协调器 LLM 自己决定 spawn 策略
    """
    import json
    import os
    import tempfile
    import time as _time
    from pathlib import Path

    from openharness.coordinator.coordinator_mode import (
        TaskNotification,
        format_task_notification,
        parse_task_notification,
        get_coordinator_system_prompt,
        get_coordinator_user_context,
        get_coordinator_tools,
        get_team_registry,
    )
    from openharness.coordinator.agent_definitions import (
        get_agent_definition,
        get_builtin_agent_definitions,
    )
    from openharness.prompts.context import build_runtime_system_prompt
    from openharness.config.settings import Settings, load_settings
    from openharness.ui.coordinator_drain import (
        pending_async_agent_entries,
        wait_for_completed_async_agent_entries,
        format_completed_task_notifications,
    )
    from openharness.tasks.manager import get_task_manager
    from openharness.tools.agent_tool import AgentTool
    from openharness.tools.base import ToolExecutionContext, ToolRegistry
    from openharness.engine.query import QueryContext

    print("=" * 70)
    print("进阶 Demo 11: Coordinator 模式 — LLM 自主调度 Worker (真实执行)")
    print("=" * 70)

    # ══════════════════════════════════════════════════════════════════
    # Setup: 启用 Coordinator 模式 + 构建 Runtime
    # ══════════════════════════════════════════════════════════════════
    print("\n[Setup] 启用 Coordinator 模式 (CLAUDE_CODE_COORDINATOR_MODE=1)...")

    # todo @Toby注释: [激活方式] 设置环境变量后，build_runtime 内部会自动:
    #   1. build_runtime_system_prompt() → get_coordinator_system_prompt() (270行专用prompt)
    #   2. QueryEngine._build_coordinator_context_message() → 注入worker工具列表
    #   3. 生成子进程时 spawn_utils 自动设置 CLAUDE_CODE_COORDINATOR_MODE=0
    os.environ["CLAUDE_CODE_COORDINATOR_MODE"] = "1"

    from openharness.ui.runtime import build_runtime, start_runtime

    bundle = await build_runtime(
        cwd=".",
        model="deepseek-chat",
        max_turns=10,
        permission_mode="full_auto",
    )
    await start_runtime(bundle)

    engine = bundle.engine
    api_client = engine._api_client
    base_checker = engine._permission_checker
    settings = bundle.current_settings()

    print("  [ok] Runtime 已就绪, Coordinator 模式已激活")
    print(f"  model={settings.model}, max_turns={settings.max_turns}")

    # ══════════════════════════════════════════════════════════════════
    # Case 1: Coordinator 基础设施 — 系统提示/用户上下文/工具限制
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 1: Coordinator 基础设施 — 确认模式已激活")
    print("=" * 70)

    # 1a. 检查系统提示
    sys_prompt = engine._system_prompt
    is_coord_prompt = "You are a **coordinator**" in sys_prompt
    print(f"\n  1a. 系统提示检查:")
    print(f"     包含 'You are a **coordinator**': {is_coord_prompt}")
    print(f"     系统提示长度: {len(sys_prompt)} 字符 (常规 ~3000, coordinator ~8000+)")
    # 展示提示的关键段落
    for keyword in ["## 1. Your Role", "## 2. Your Tools", "agent tool have access"]:
        for line in sys_prompt.split("\n"):
            if keyword in line:
                print(f"     → {line.strip()[:100]}")
                break

    # 1b. 检查用户上下文消息 (worker 工具列表)
    print(f"\n  1b. 用户上下文 (Coordinator User Context):")
    ctx = get_coordinator_user_context()
    worker_tools_ctx = ctx.get("workerToolsContext", "")
    print(f"     长度: {len(worker_tools_ctx)} 字符")
    # 展示 worker 工具列表的前几个
    for line in worker_tools_ctx.split("\n"):
        if "Workers spawned" in line:
            print(f"     {line.strip()[:120]}...")
            break

    # 1c. 检查 Coordinator 专用工具
    print(f"\n  1c. Coordinator 专用工具 (prompt中告知LLM只能用这些):")
    coord_tools = get_coordinator_tools()
    print(f"     {coord_tools}")
    all_tools = engine._tool_registry.list_tools()
    print(f"     实际注册了 {len(all_tools)} 个工具, 但LLM被prompt限制只用上面3个")

    # 1d. 检查 7 种内置 AgentDefinition (worker 类型)
    print(f"\n  1d. 内置 AgentDefinition (coordinator可spawn的worker类型):")
    agent_types = get_builtin_agent_definitions()
    for ad in agent_types[:7]:
        tool_count = len(ad.tools) if ad.tools else "all"
        print(f"     {ad.name:25s}  tools={str(tool_count):5s}  bg={ad.background}")

    # ══════════════════════════════════════════════════════════════════
    # Case 2: agent tool → subprocess worker → drain → XML 通知
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 2: agent tool 真实流程 — subprocess spawn → drain → XML")
    print("=" * 70)
    print("这是 Coordinator 模式的核心链路:")
    print("  Coordinator LLM 调用 agent tool")
    print("    → AgentTool.execute() 调用 SubprocessBackend.spawn()")
    print("    → 启动子进程: python -m openharness --task-worker")
    print("    → worker 完成后, drain_coordinator_async_agents() 轮询")
    print("    → 格式化为 <task-notification> XML")
    print("    → 作为 user-role message 注入 coordinator 对话")

    # 创建临时目录存放 worker 输出
    tmpdir = Path(tempfile.mkdtemp(prefix="coordinator_demo_"))
    worker_report = tmpdir / "research_result.txt"

    # 手动调用 agent tool (模拟 Coordinator LLM 的 tool_use)
    from openharness.tools.agent_tool import AgentToolInput
    agent_tool = AgentTool()
    agent_input = AgentToolInput(
        description="Research engine/query.py run_query function",
        prompt=(
            f"You are a research worker. Do these steps ONLY:\n"
            f"1. Read file src/openharness/engine/query.py (first 50 lines)\n"
            f"2. Write a 1-sentence summary of what run_query does to {worker_report}\n"
            f"3. Report DONE and stop.\n"
            f"Do NOT modify any files. Do NOT read additional files."
        ),
        subagent_type="Explore",
        mode="local_agent",
    )

    print(f"\n  [agent tool input] description={agent_input.description}")
    print(f"  [agent tool input] subagent_type={agent_input.subagent_type}")
    print(f"  [agent tool input] mode={agent_input.mode}")

    # 创建 ToolExecutionContext
    tool_ctx = ToolExecutionContext(
        cwd=Path.cwd(),
        hook_executor=engine._hook_executor,
        metadata=engine._tool_metadata,
    )

    print("\n  [执行] AgentTool.execute() → SubprocessBackend.spawn()...")
    print("  注意: AgentTool 硬编码使用 subprocess backend (agent_tool.py:66)")
    print("     registry.get_executor('subprocess') → SubprocessBackend.spawn()")
    t0 = _time.time()
    result = await agent_tool.execute(agent_input, tool_ctx)
    spawn_time = _time.time() - t0
    print(f"  [完成] spawn 耗时 {spawn_time:.1f}s")
    print(f"  [结果] {result.output}")
    if result.metadata:
        agent_id = result.metadata.get("agent_id", "?")
        task_id = result.metadata.get("task_id", "?")
        backend = result.metadata.get("backend_type", "?")
        print(f"  [元数据] agent_id={agent_id}, task_id={task_id}, backend={backend}")

    # 手动将 task 记录到 async_agent_tasks (模拟 _remember_async_agent_task)
    # 在真实的 Coordinator 流程中, 这个记录由 _record_tool_carryover() →
    # _remember_async_agent_task() 在 run_query() 的 _execute_tool_call() 中完成。
    # 这里直接调用了 agent_tool.execute(), 绕过了 engine loop, 所以需要手动记录。
    task_id = result.metadata.get("task_id", "") if result.metadata else ""
    if task_id:
        entry = {
            "agent_id": agent_id,
            "task_id": task_id,
            "description": agent_input.description,
            "status": "spawned",
            "notification_sent": False,
            "spawned_at": _time.time(),
        }
        bucket = engine._tool_metadata.setdefault("async_agent_tasks", [])
        bucket.append(entry)
        print(f"\n  [手动记录] async_agent_tasks 已添加: agent_id={agent_id}")

    # 检查 BackgroundTaskManager — worker 作为子进程运行
    manager = get_task_manager()
    if task_id:
        task_record = manager.get_task(task_id)
        if task_record:
            print(f"  [BackgroundTaskManager] task 状态: {task_record.status}")

    # Drain: 等待 worker 完成 (模拟 drain_coordinator_async_agents 核心逻辑)
    print(f"\n  [Drain] 轮询 BackgroundTaskManager, 等待 worker 完成...")
    print(f"  源码: ui/coordinator_drain.py:159 drain_coordinator_async_agents()")
    print(f"    → pending_async_agent_entries() 从 tool_metadata 读取待处理列表")
    print(f"    → wait_for_completed_async_agent_entries() 轮询 BackgroundTaskManager")
    t1 = _time.time()
    completed = await wait_for_completed_async_agent_entries(
        engine._tool_metadata, poll_interval_seconds=0.5
    )
    wait_time = _time.time() - t1

    if completed:
        print(f"  [Drain] {len(completed)} worker(s) 完成, 等待耗时 {wait_time:.1f}s")
        # 格式化 TaskNotification XML — Coordinator 收到的 user-role message
        xml = format_completed_task_notifications(completed)
        print(f"\n  [TaskNotification XML] 这是 Coordinator LLM 收到的 user-role message:")
        print(f"  源码: coordinator/coordinator_mode.py format_task_notification()")
        for line in xml.split("\n"):
            print(f"    | {line}")

        # 解析 XML 验证序列化/反序列化往返
        tn = parse_task_notification(xml)
        print(f"\n  [解析验证] parse_task_notification() 往返成功:")
        print(f"    task_id={tn.task_id}, status={tn.status}")
        print(f"    summary={tn.summary[:80]}")
        result_preview = (tn.result or "(无输出)")[:200]
        print(f"    result={result_preview}...")

        print(f"\n  [注入对话] 此时 drain_coordinator_async_agents() 会调用")
        print(f"    engine.submit_message(xml) 将此 XML 作为 user-role message")
        print(f"    注入 Coordinator LLM 的对话历史, LLM 看到后可以综合结果或继续调度")
    else:
        print(f"  [Drain] drain 函数未找到完成记录, 改用 BackgroundTaskManager 直接查询...")
        if task_id:
            # 手动轮询 BackgroundTaskManager
            for i in range(30):  # 最多等 15 秒
                task_record = manager.get_task(task_id)
                if task_record and task_record.status in ("completed", "failed", "killed"):
                    print(f"  [直接查询] task status={task_record.status}, "
                          f"return_code={task_record.return_code}")
                    output = manager.read_task_output(task_id, max_bytes=2000)
                    if output:
                        print(f"  [Worker 输出]:\n{output[:500]}")
                    break
                await asyncio.sleep(0.5)
            else:
                print(f"  [超时] worker 15s 内未完成, 最终状态: "
                      f"{manager.get_task(task_id).status if manager.get_task(task_id) else 'unknown'}")

    # 检查 worker 输出文件
    if worker_report.exists():
        print(f"\n  [Worker 产出文件] {worker_report}:")
        print(f"    {worker_report.read_text()[:300]}")
    else:
        print(f"\n  [Worker 产出文件] {worker_report} 不存在 (worker 可能启动失败或权限不足)")

    # ══════════════════════════════════════════════════════════════════
    # Case 3: 对比 SubAgent (mailbox) vs Coordinator (XML) 结果格式
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 3: 结果格式对比 — Coordinator XML vs SubAgent Mailbox JSON")
    print("=" * 70)

    print("""
    Coordinator 模式 — TaskNotification XML (注入为 user-role message):
    ┌────────────────────────────────────────────────────────────┐
    │ <task-notification task_id="worker@team" status="completed"│
    │   summary="Agent completed">                               │
    │   <result><![CDATA[worker output here...]]></result>       │
    │ </task-notification>                                       │
    └────────────────────────────────────────────────────────────┘
    """)

    from openharness.swarm.mailbox import TeammateMailbox, create_user_message

    print("""
    SubAgent 模式 — Mailbox JSON (文件IPC, inbox目录轮询):
    ┌────────────────────────────────────────────────────────────┐
    │ File: ~/.openharness/teams/<team>/agents/<id>/inbox/       │
    │       <timestamp>_<uuid>.json                              │
    │ Content: {                                                 │
    │   "type": "user_message",                                  │
    │   "sender": "worker_name",                                 │
    │   "payload": {"content": "worker output here..."}          │
    │ }                                                          │
    └────────────────────────────────────────────────────────────┘
    """)

    print("  关键源码路径对比:")
    print("    Coordinator drain: ui/coordinator_drain.py:159 drain_coordinator_async_agents()")
    print("      → wait_for_completed_async_agent_entries() 轮询 BackgroundTaskManager")
    print("      → format_completed_task_notifications() 生成 XML")
    print("      → submit_follow_up() 注入为 user message")
    print("    SubAgent mailbox: swarm/in_process.py _drain_mailbox()")
    print("      → TeammateMailbox.drain_inbox() 读 JSON 文件")
    print("      → 注入为 ConversationMessage(role='user')")

    # ══════════════════════════════════════════════════════════════════
    # Case 4: LLM 自主调度 — 真正的 Coordinator 模式！
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 4: LLM 自主调度 — Coordinator LLM 自己决定 spawn 策略")
    print("=" * 70)
    print("这才是 Coordinator 模式的核心价值:")
    print("  Step 1: 用户提交高层任务")
    print("  Step 2: Coordinator LLM 分析任务 → 决定 spawn 哪些 workers")
    print("  Step 3: Workers 并行/串行执行, Coordinator LLM 等待结果")
    print("  Step 4: XML 通知到达 → Coordinator LLM 综合结果 → 回复用户")

    # 简单的打印回调
    async def _print_system(msg: str) -> None:
        print(f"  [system] {msg}")

    async def _render_event(event) -> None:
        # 只打印关键事件
        from openharness.engine.stream_events import (
            AssistantTextDelta,
            ToolExecutionStarted,
            ToolExecutionCompleted,
            AssistantTurnComplete,
        )
        if isinstance(event, ToolExecutionStarted):
            inp = str(event.tool_input)[:80]
            print(f"  [tool_start] {event.tool_name} — {inp}")
        elif isinstance(event, ToolExecutionCompleted):
            out_preview = (event.output or "")[:100]
            print(f"  [tool_done] {event.tool_name} → {out_preview}")
        elif isinstance(event, AssistantTurnComplete):
            text = event.message.text[:200] if event.message.text else "(no text)"
            print(f"  [turn_complete] {text}")
        elif isinstance(event, AssistantTextDelta):
            # 不打印每个 delta，太吵
            pass

    print("\n  [提交任务] 让 Coordinator LLM 研究代码库...")
    print("  任务: 'I need to understand two things about OpenHarness:'")
    print("         '1. What does engine/query.py run_query do?'")
    print("         '2. What does permissions/checker.py do?'")
    print("         'Spawn research agents to investigate and report back.'")

    try:
        # 收集所有事件
        events = []
        async for event in engine.submit_message(
            "I need to understand two things about the OpenHarness codebase:\n"
            "1. What does the run_query function in src/openharness/engine/query.py do?\n"
            "2. What does PermissionChecker.evaluate in src/openharness/permissions/checker.py do?\n\n"
            "Spawn research agents to investigate each file and report back. "
            "Each agent should read the relevant file, write a 1-sentence summary "
            f"to /tmp/coord_demo_research_{{topic}}.txt, and stop."
        ):
            events.append(event)
            await _render_event(event)

        print(f"\n  [结果] Coordinator 完成, 共 {len(events)} 个事件")

        # 分析事件看 Coordinator LLM 的行为
        from openharness.engine.stream_events import (
            ToolExecutionStarted,
            ToolExecutionCompleted,
            AssistantTurnComplete,
        )
        tool_calls = [e for e in events if isinstance(e, ToolExecutionStarted)]
        if tool_calls:
            print(f"\n  [分析] Coordinator LLM 调用了 {len(tool_calls)} 个工具:")
            for tc in tool_calls:
                print(f"    - {tc.tool_name}: {str(tc.tool_input)[:120]}")
        else:
            print(f"\n  [分析] Coordinator LLM 没有调用任何工具")
            print(f"  提示: 某些模型(如deepseek-chat)可能不严格遵循coordinator prompt")
            print(f"  这是预期的 — coordinator prompt 是为 Claude 优化的")

        # 显示最终消息
        for e in reversed(events):
            if isinstance(e, AssistantTurnComplete) and e.message.text:
                print(f"\n  [最终回复] {e.message.text[:500]}")
                break

    except Exception as exc:
        print(f"\n  [异常] Coordinator 执行出错: {type(exc).__name__}: {exc}")

    # ══════════════════════════════════════════════════════════════════
    # 总结
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Demo 总结: Coordinator vs SubAgent 架构对比")
    print("=" * 70)
    print("""
    Coordinator 模式 = LLM 当项目经理
    ┌─────────────────────────────────────────────────────────────┐
    │ 用户: "研究代码库,找出所有安全问题"                            │
    │                                                              │
    │ Coordinator LLM (只能用 agent/send_message/task_stop):        │
    │   → agent(Explore, "扫描 auth/ 目录")                        │
    │   → agent(Explore, "扫描 permissions/ 目录")                  │
    │   → agent(Explore, "扫描 api/ 目录的密钥处理")               │
    │   [等待 workers 完成...]                                      │
    │   [收到 3 个 <task-notification> XML]                        │
    │   → agent(worker, "编写安全报告,综合上面3个发现")             │
    │   [收到综合报告]                                              │
    │   → "发现 5 个安全问题: ..." (回复用户)                       │
    │                                                              │
    │ 特点: LLM 自主决策拆分策略, adapts to task complexity         │
    └─────────────────────────────────────────────────────────────┘

    SubAgent 模式 = 代码当调度器
    ┌─────────────────────────────────────────────────────────────┐
    │ 代码:                                                        │
    │   config_a = TeammateSpawnConfig(name="worker_a", ...)       │
    │   config_b = TeammateSpawnConfig(name="worker_b", ...)       │
    │   asyncio.gather(                                            │
    │     start_in_process_teammate(config_a, ...),                │
    │     start_in_process_teammate(config_b, ...),                │
    │   )                                                          │
    │                                                              │
    │ 特点: 调度逻辑硬编码, 适合固定工作流                          │
    └─────────────────────────────────────────────────────────────┘

    关键源码路径:
    - Coordinator 激活: coordinator/coordinator_mode.py:186 is_coordinator_mode()
    - Coordinator prompt: coordinator/coordinator_mode.py:252 get_coordinator_system_prompt()
    - Agent tool: tools/agent_tool.py:45 AgentTool.execute()
    - Subprocess spawn: swarm/subprocess_backend.py:47 SubprocessBackend.spawn()
    - Worker 命令: swarm/spawn_utils.py:70 get_teammate_command()
    - Drain 循环: ui/coordinator_drain.py:159 drain_coordinator_async_agents()
    - XML 序列化: coordinator/coordinator_mode.py format_task_notification()
    - Agent 定义: coordinator/agent_definitions.py:510 _BUILTIN_AGENTS
    """)

    # 清理
    del os.environ["CLAUDE_CODE_COORDINATOR_MODE"]
    print("  [cleanup] CLAUDE_CODE_COORDINATOR_MODE 已恢复")
    print("\n" + "=" * 70)
    print("进阶 Demo 11 结束")
    print("=" * 70)

# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 12: Skill & MCP 命中后加载 — 纯 API 层验证
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_skill_mcp_hit_api():
    """纯 API 层验证 skill 和 MCP 的命中后加载机制.

    Skill 命中前: system prompt 中只注入 name/description (轻量索引)
    Skill 命中后: skill_tool.execute() 返回完整 content (完整加载)

    MCP 命中前: ToolRegistry 中只有 name/description/schema (轻量索引)
    MCP 命中后: mcp_tool_adapter.execute() 实际转发到 MCP server (完整执行)
    """
    import shutil
    import sys
    import tempfile

    from openharness.mcp.client import McpClientManager
    from openharness.mcp.types import McpStdioServerConfig
    from openharness.skills.loader import load_skill_registry
    from openharness.tools.base import ToolExecutionContext
    from openharness.tools.mcp_tool import McpToolAdapter
    from openharness.tools.skill_tool import SkillTool, SkillToolInput

    print("=" * 70)
    print("进阶 Demo 12: Skill & MCP 命中后加载 — API 层验证")
    print("=" * 70)

    # ── Part A: Skill 命中后加载 ────────────────────────────────────
    print("\n[Part A] Skill 命中后加载...")

    tmpdir = Path(tempfile.mkdtemp(prefix="skill_hit_demo_"))
    skill_dir = tmpdir / "test-hit-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_content = """---
name: test-hit-skill
description: A test skill to demonstrate hit-load mechanism
user-invocable: true
---

# Core Directive

Always think step by step. When analyzing code:
1. Read the function signature first
2. Trace the call graph
3. Verify edge cases
"""
    skill_md.write_text(skill_content, encoding="utf-8")

    registry = load_skill_registry(extra_skill_dirs=[tmpdir])
    skill_def = registry.get("test-hit-skill")
    print(f"  [命中前] registry.get('test-hit-skill') -> name={skill_def.name}")
    print(f"  [命中前] description={skill_def.description}")
    print(f"  [命中前] content_len={len(skill_def.content)}")

    skill_tool = SkillTool()
    ctx = ToolExecutionContext(cwd=Path.cwd(), metadata={"extra_skill_dirs": [tmpdir]})
    result = await skill_tool.execute(SkillToolInput(name="test-hit-skill"), ctx)
    print(f"  [命中后] skill_tool.execute() -> output_len={len(result.output)}")
    print(f"  [命中后] output_preview: {result.output[:200].replace(chr(10), ' ')}...")

    assert len(skill_def.content) == len(result.output), "命中前后 content 长度应一致"
    assert "Always think step by step" in result.output, "应返回完整的 skill content"
    print("  ✓ Skill 命中后加载验证通过")

    # ── Part B: MCP 命中后加载 ────────────────────────────────────
    print("\n[Part B] MCP 命中后加载...")

    fixture_server = (
        Path(__file__).parent.parent.parent / "tests" / "fixtures" / "fake_mcp_server.py"
    )
    if not fixture_server.exists():
        print(f"  ✗ MCP server 文件不存在: {fixture_server}")
        return

    config = McpStdioServerConfig(
        type="stdio",
        command=sys.executable,
        args=[str(fixture_server)],
    )
    manager = McpClientManager({"fixture-demo": config})
    await manager.connect_all()

    tools = manager.list_tools()
    hello_tool_info = next((t for t in tools if t.name == "hello"), None)
    if hello_tool_info is None:
        print("  ✗ fake_mcp_server 中没有 hello 工具")
        await manager.close()
        return

    print(f"  [命中前] manager.list_tools() -> found '{hello_tool_info.name}': {hello_tool_info.description}")
    print(f"  [命中前] input_schema keys={list(hello_tool_info.input_schema.get('properties', {}).keys())}")

    adapter = McpToolAdapter(manager, hello_tool_info)
    mcp_ctx = ToolExecutionContext(cwd=Path.cwd())
    InputModel = adapter.input_model
    mcp_result = await adapter.execute(InputModel(name="OpenHarness"), mcp_ctx)
    print(f"  [命中后] adapter.execute() -> {mcp_result.output}")

    assert "OpenHarness" in mcp_result.output, "MCP 工具应返回包含输入名字的问候"
    print("  ✓ MCP 命中后加载验证通过")

    await manager.close()
    shutil.rmtree(tmpdir)
    print("\n进阶 Demo 12 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 13: Skill & MCP 命中后加载 — Agent Loop 层
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_skill_mcp_hit_agent_loop():
    """Agent Loop 层: LLM 自主发现并调用 skill 和 MCP 工具.

    Setup: 创建临时 test skill + 启动 fake MCP server + build_runtime
    Case A: 让 LLM 使用 test-hit-skill 技能
    Case B: 让 LLM 使用 MCP hello 工具
    """
    import shutil
    import sys
    import tempfile

    from openharness.mcp.client import McpClientManager
    from openharness.mcp.types import McpStdioServerConfig
    from openharness.tools.list_mcp_resources_tool import ListMcpResourcesTool
    from openharness.tools.mcp_tool import McpToolAdapter
    from openharness.tools.read_mcp_resource_tool import ReadMcpResourceTool

    print("=" * 70)
    print("进阶 Demo 13: Skill & MCP 命中后加载 — Agent Loop 层")
    print("=" * 70)

    # ── Setup: 创建 test skill ─────────────────────────────────────
    print("\n[Setup] 创建临时 test skill...")
    tmpdir = Path(tempfile.mkdtemp(prefix="skill_mcp_loop_demo_"))
    skill_dir = tmpdir / "test-hit-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_content = """---
name: test-hit-skill
description: A test skill to demonstrate hit-load mechanism
user-invocable: true
---

# Core Directive

Always think step by step. When analyzing code:
1. Read the function signature first
2. Trace the call graph
3. Verify edge cases
"""
    skill_md.write_text(skill_content, encoding="utf-8")
    print(f"  skill 路径: {skill_md}")

    # ── Setup: 启动 MCP server ─────────────────────────────────────
    print("\n[Setup] 启动 fake MCP server...")
    fixture_server = (
        Path(__file__).parent.parent.parent / "tests" / "fixtures" / "fake_mcp_server.py"
    )
    if not fixture_server.exists():
        print(f"  ✗ MCP server 文件不存在: {fixture_server}")
        return

    config = McpStdioServerConfig(
        type="stdio",
        command=sys.executable,
        args=[str(fixture_server)],
    )
    manager = McpClientManager({"fixture-demo": config})
    await manager.connect_all()
    print("  MCP server 已连接")

    # ── Setup: build_runtime ───────────────────────────────────────
    print("\n[Setup] build_runtime...")
    bundle = await build_runtime(
        cwd=".",
        model="deepseek-chat",
        max_turns=5,
        permission_mode="full_auto",
        extra_skill_dirs=[tmpdir],
    )
    await start_runtime(bundle)

    # 手动将 MCP 工具注册到已有 runtime 的 tool_registry
    bundle.tool_registry.register(ListMcpResourcesTool(manager))
    bundle.tool_registry.register(ReadMcpResourceTool(manager))
    for tool_info in manager.list_tools():
        bundle.tool_registry.register(McpToolAdapter(manager, tool_info))
    print(f"  MCP 工具已注入 registry, 当前共 {len(bundle.tool_registry.list_tools())} 个工具")

    # ══════════════════════════════════════════════════════════════════
    # Case A: Skill 命中
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case A: Skill 命中 — LLM 调用 skill 工具加载完整内容")
    print("=" * 70)

    skill_prompt = (
        "请使用 test-hit-skill 技能，告诉我它的核心指令是什么。"
        "直接调用 skill 工具获取内容后回答。"
    )
    skill_found = False
    async for event in bundle.engine.submit_message(skill_prompt):
        if isinstance(event, ToolExecutionStarted):
            print(f"  [tool_start] {event.tool_name}: {str(event.tool_input)[:100]}")
            if event.tool_name == "skill":
                skill_found = True
        elif isinstance(event, ToolExecutionCompleted):
            out_preview = (event.output or "")[:200]
            print(f"  [tool_done] {event.tool_name} -> {out_preview}...")
        elif isinstance(event, AssistantTextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, AssistantTurnComplete):
            print(f"\n  [turn_complete] tool_uses={len(event.message.tool_uses)}")
        elif isinstance(event, ErrorEvent):
            print(f"\n  [ERROR] {event.message}")

    if skill_found:
        print("\n  ✓ Skill 命中验证通过 (LLM 调用了 skill 工具)")
    else:
        print("\n  ⚠ Skill 未被调用 (可能 LLM 直接回答了或模型不支持)")

    # ══════════════════════════════════════════════════════════════════
    # Case B: MCP 命中
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case B: MCP 命中 — LLM 调用 MCP 工具执行远程逻辑")
    print("=" * 70)

    mcp_prompt = (
        "请使用 MCP 工具 hello，向它打招呼并说你的名字是 OpenHarness。"
        "工具名是 mcp__fixture_demo__hello，参数是 name='OpenHarness'。"
    )
    mcp_found = False
    async for event in bundle.engine.submit_message(mcp_prompt):
        if isinstance(event, ToolExecutionStarted):
            print(f"  [tool_start] {event.tool_name}: {str(event.tool_input)[:100]}")
            if "mcp__" in event.tool_name:
                mcp_found = True
        elif isinstance(event, ToolExecutionCompleted):
            out_preview = (event.output or "")[:200]
            print(f"  [tool_done] {event.tool_name} -> {out_preview}")
        elif isinstance(event, AssistantTextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, AssistantTurnComplete):
            print(f"\n  [turn_complete] tool_uses={len(event.message.tool_uses)}")
        elif isinstance(event, ErrorEvent):
            print(f"\n  [ERROR] {event.message}")

    if mcp_found:
        print("\n  ✓ MCP 命中验证通过 (LLM 调用了 MCP 工具)")
    else:
        print("\n  ⚠ MCP 工具未被调用 (可能模型未识别或工具名不匹配)")

    # ── Cleanup ────────────────────────────────────────────────────
    await close_runtime(bundle)
    await manager.close()
    shutil.rmtree(tmpdir)
    print("\n进阶 Demo 13 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 14: Plugin 完整生命周期 — 发现 → 加载 → 命中使用
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_plugin_full_lifecycle():
    """Plugin 完整生命周期: 发现 → 加载 → 命中使用.

    使用已安装的 superpowers 插件作为真实案例，展示:
    Part A — 插件发现: 扫描目录，找到 plugin.json manifest
    Part B — 插件加载: 解析 manifest → load_plugin() → LoadedPlugin
    Part C — Skills 注册: plugin skills 被自动注册到 SkillRegistry
    Part D — Skill 命中加载: 通过 SkillTool.execute() 加载完整 content
    Part E — Hooks 验证: 检查插件提供的 SessionStart hook

    关键源码路径:
    - 发现: plugins/loader.py:61 discover_plugin_paths()
    - 加载: plugins/loader.py:126 load_plugin()
    - 注册: prompts/context.py:68 _build_skills_section() → load_skill_registry()
    - 命中: tools/skill_tool.py:28 SkillTool.execute()
    - Hook: plugins/loader.py:621 _load_plugin_hooks()
    """
    import json

    from openharness.config.paths import get_config_dir
    from openharness.config.settings import load_settings
    from openharness.plugins.loader import _find_manifest, load_plugin
    from openharness.skills.loader import load_skill_registry
    from openharness.tools.base import ToolExecutionContext
    from openharness.tools.skill_tool import SkillTool, SkillToolInput

    print("=" * 70)
    print("进阶 Demo 14: Plugin 完整生命周期")
    print("=" * 70)

    # ══════════════════════════════════════════════════════════════════
    # Part A: 插件发现 — 扫描目录找到 plugin.json
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part A] 插件发现 — 扫描 claude-code 缓存目录...")
    cache_base = Path.home() / ".claude" / "plugins" / "cache" / "claude-plugins-official"
    superpowers_dir = cache_base / "superpowers"

    superpowers_root = None
    if superpowers_dir.exists():
        for candidate in sorted(superpowers_dir.iterdir()):
            if candidate.is_dir():
                manifest = _find_manifest(candidate)
                if manifest is not None:
                    superpowers_root = candidate
                    print(f"  ✓ 发现插件: {superpowers_root}")
                    print(f"    manifest: {manifest}")
                    break

    if superpowers_root is None:
        print("  ⚠ 未找到 superpowers 插件 (尝试路径: ~/.claude/plugins/.../superpowers/)")
        return

    raw = json.loads(manifest.read_text(encoding="utf-8"))
    print(f"  manifest 内容:")
    for key in ("name", "version", "description"):
        print(f"    {key}: {raw.get(key, 'N/A')}")
    print(f"    author: {raw.get('author', {}).get('name', 'N/A')}")

    # ══════════════════════════════════════════════════════════════════
    # Part B: 插件加载 — load_plugin() → LoadedPlugin
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part B] 插件加载 — load_plugin()...")
    settings = load_settings()
    plugin = load_plugin(superpowers_root, settings.enabled_plugins)

    if plugin is None:
        print("  ✗ load_plugin() 返回 None")
        return

    print(f"  manifest.name:      {plugin.manifest.name}")
    print(f"  manifest.version:   {plugin.manifest.version}")
    print(f"  enabled:            {plugin.enabled}")
    print(f"  path:               {plugin.path}")

    print(f"\n  Skills ({len(plugin.skills)} 个):")
    for i, skill in enumerate(plugin.skills):
        icon = "🙋" if skill.user_invocable else "🤖"
        print(f"    {i+1:2d}. {icon} {skill.name:35s} {skill.description[:65]}")

    print(f"\n  Commands: {len(plugin.commands)} 个")
    for cmd in plugin.commands[:3]:
        print(f"    - {cmd.name}: {cmd.description[:60]}")

    print(f"\n  Agents: {len(plugin.agents)} 个")
    for agent in plugin.agents:
        print(f"    - {agent.name}: {agent.description[:60]}")

    print(f"\n  Hooks: {len(plugin.hooks)} 个事件类型")
    for event_name, hook_list in plugin.hooks.items():
        print(f"    Event: {event_name} ({len(hook_list)} entries)")
        for h in hook_list:
            if isinstance(h, dict):
                print(f"      matcher={h.get('matcher', '(none)')} "
                      f"commands={len(h.get('hooks', []))}")

    # ══════════════════════════════════════════════════════════════════
    # Part C: Skills 注册 — plugin skills → SkillRegistry
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part C] Skills 注册 — plugin skills → SkillRegistry")
    print("  load_skill_registry() 扫描路径:")
    print("    1. bundled skills (包内置)")
    print("    2. ~/.openharness/skills/ + ~/.claude/skills/ (用户级)")
    print("    3. .openharness/skills/ 等 (项目级, 从 cwd 向上到 git root)")
    print("    4. load_plugins() → 所有已启用的 plugin.skills")
    print("  superpowers 不在默认扫描路径，需要 extra_plugin_roots 参数")
    print()

    superpowers_parent = cache_base / "superpowers"
    registry = load_skill_registry(
        cwd=".",
        extra_plugin_roots=[superpowers_parent],
    )
    plugin_skill_count = 0
    print("  registry 中的 plugin skills:")
    for skill in registry.list_skills():
        if skill.source == "plugin":
            plugin_skill_count += 1
            print(f"    ✓ {skill.name:35s} [{skill.source}] — {skill.description[:50]}")

    print(f"\n  共 {plugin_skill_count} 个 plugin skills 已注册到 SkillRegistry")

    # ══════════════════════════════════════════════════════════════════
    # Part D: Skill 命中加载 — 通过 SkillTool 获取完整 content
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part D] Skill 命中加载 — SkillTool.execute()")
    print("  命中前: system prompt 中只注入 name+description (_build_skills_section)")
    print("  命中后: SkillTool.execute() → load_skill_registry() → registry.get() → content")

    # 选第一个 plugin skill 来测试
    test_skill = "brainstorming"
    skill_def = registry.get(test_skill)

    if skill_def:
        print(f"\n  [命中前] registry.get('{test_skill}')")
        print(f"    name:        {skill_def.name}")
        print(f"    source:      {skill_def.source}")
        print(f"    path:        {skill_def.path}")
        print(f"    content_len: {len(skill_def.content)} 字符")
        print(f"    description: {skill_def.description[:80]}...")

        skill_tool = SkillTool()
        context = ToolExecutionContext(
            cwd=Path.cwd(),
            metadata={"extra_plugin_roots": [superpowers_parent]},
        )
        result = await skill_tool.execute(SkillToolInput(name=test_skill), context)

        print(f"\n  [命中后] skill_tool.execute(name='{test_skill}')")
        print(f"    output_len: {len(result.output)} 字符")
        preview = result.output[:400]
        for line in preview.split("\n")[:8]:
            print(f"    | {line}")
        line_count = len(result.output.split("\n"))
        if line_count > 8:
            print(f"    ... ({line_count - 8} more lines)")

        assert not result.is_error, f"技能加载不应失败: {result.output}"
        print("\n  ✓ Plugin skill 命中加载验证通过")
    else:
        print(f"  ⚠ registry 中未找到 skill '{test_skill}'")

    # ══════════════════════════════════════════════════════════════════
    # Part E: Hooks 配置检查 — 展示两种 hooks.json 格式差异
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part E] Hooks 配置检查...")
    print("  hooks.json 存在两种格式:")
    print("    flat 格式:     {EventName: [{type, command, matcher}]}  → _load_plugin_hooks")
    print("    structured 格式: {hooks: {EventName: [{matcher, hooks: [...]}]}}  → _load_plugin_hooks_structured")
    print("  当前 hooks.json 是 structured 格式, _load_plugin_hooks 无法正确拆解")
    print("  (flat parser 找不到 'type' 字段 → hooks 被计入 event 但无法执行)")
    print()
    if plugin.hooks:
        for event, hook_list in plugin.hooks.items():
            print(f"  {event}: {len(hook_list)} entries (parsed incorrectly by flat parser)")
            for h in hook_list:
                if isinstance(h, dict):
                    matcher = h.get("matcher", "(none)")
                    sub_hooks = h.get("hooks", [])
                    is_structured_format = len(sub_hooks) > 0
                    print(f"    matcher='{matcher}' is_structured={is_structured_format}")
                    if is_structured_format:
                        print(f"    ↓ 正确解析后应有 {len(sub_hooks)} 个实际 hook:")
                        for sh in sub_hooks:
                            print(f"      type={sh.get('type')}, async={sh.get('async')}")
                            cmd = str(sh.get('command', ''))
                            print(f"      cmd={cmd[:100]}")
                    else:
                        print(f"    [flat parser 未识别 — 缺少 'type' 字段]")
    else:
        print("  (无 hooks 数据)")

    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Plugin 完整生命周期总结:")
    print("  1. [发现] _find_manifest() 扫描目录找 .claude-plugin/plugin.json")
    print("  2. [加载] load_plugin() 解析 manifest + skills/ + commands/ + hooks")
    print("  3. [注册] load_skill_registry() 自动把 plugin.skills 注入 SkillRegistry")
    print("  4. [命中] SkillTool.execute() 返回完整的 skill markdown content")
    print("  5. [拦截] plugin hooks 被 HookExecutor 在工具执行前后触发")
    print("=" * 70)
    print("\n进阶 Demo 14 完成!")


# ═══════════════════════════════════════════════════════════════════════
# 进阶 Demo 15: .claude/ 约定目录加载机制 — 揭开"魔力"
# ═══════════════════════════════════════════════════════════════════════

async def demo_advanced_claude_conventions():
    """揭开 .claude/ 目录约定加载的黑盒.

    原理: OpenHarness (和 Claude Code) 通过"约定优于配置"的方式，
    从 cwd 向上遍历目录，自动加载特定路径的文件:

      {dir}/CLAUDE.md               → 项目指令 (最高优先级)
      {dir}/.claude/CLAUDE.md       → 同 CLAUDE.md，放在 .claude/ 下
      {dir}/.claude/rules/*.md      → 规则文件 (按字母序)
      {dir}/.claude/skills/         → 技能目录
      {dir}/.openharness/skills/    → OpenHarness 原生路径
      {dir}/.agents/skills/         → 兼容路径
      ~/.openharness/local_rules/rules.md → 用户全局 Rule

    每个 loader 独立扫描，互不依赖，最终在 build_runtime_system_prompt()
    中汇聚为完整的 system prompt。本 demo 复现完整扫描链路。
    """
    import shutil
    import tempfile

    from openharness.prompts.claudemd import discover_claude_md_files, load_claude_md_prompt
    from openharness.skills.loader import (
        discover_project_skill_dirs,
        load_skill_registry,
        _DEFAULT_PROJECT_SKILL_DIRS,
    )
    from openharness.personalization.rules import load_local_rules

    print("=" * 70)
    print("进阶 Demo 15: .claude/ 约定目录加载机制")
    print("=" * 70)

    # ══════════════════════════════════════════════════════════════════
    # Part A: 构建模拟项目目录树
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part A] 构建模拟项目 — 含 .claude/ 全家桶...")
    tmpdir = Path(tempfile.mkdtemp(prefix="claude_conventions_")).resolve()
    project = tmpdir / "my-project"
    project.mkdir()

    # 子目录: src/ (模拟更深的 cwd)
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    # 根级 CLAUDE.md
    (project / "CLAUDE.md").write_text(
        "# Project Root CLAUDE.md\nRun tests with pytest.",
        encoding="utf-8",
    )

    # 根级 .claude/CLAUDE.md
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    (claude_dir / "CLAUDE.md").write_text(
        "# Project .claude/CLAUDE.md\nUse ruff for linting.",
        encoding="utf-8",
    )

    # 根级 .claude/rules/
    rules_dir = claude_dir / "rules"
    rules_dir.mkdir()
    (rules_dir / "01-security.md").write_text(
        "# Security Rule\nNever check in secrets or API keys.",
        encoding="utf-8",
    )
    (rules_dir / "02-testing.md").write_text(
        "# Testing Rule\nAll PRs must include tests.",
        encoding="utf-8",
    )

    # 子目录级 CLAUDE.md (src/)
    (src_dir / "CLAUDE.md").write_text(
        "# src/ CLAUDE.md\nThis is the source directory.",
        encoding="utf-8",
    )

    # 子目录级 .claude/rules/ (src/.claude/rules/)
    src_claude = src_dir / ".claude"
    src_claude.mkdir()
    src_rules = src_claude / "rules"
    src_rules.mkdir()
    (src_rules / "01-security.md").write_text(
        "# src/ Security Rule (override)\nSource code needs extra scrutiny.",
        encoding="utf-8",
    )

    # .claude/skills/ (根级)
    skills_dir = claude_dir / "skills"
    skills_dir.mkdir()
    cli_skill = skills_dir / "cli-guide"
    cli_skill.mkdir()
    (cli_skill / "SKILL.md").write_text(
        "---\nname: cli-guide\ndescription: CLI usage guide\nuser-invocable: true\n---\n"
        "# CLI Guide\nRun `oh --help` for available commands.",
        encoding="utf-8",
    )

    # .openharness/skills/ (根级, OpenHarness 原生路径)
    oh_skills = project / ".openharness" / "skills"
    oh_skills.mkdir(parents=True)
    deploy_skill = oh_skills / "deploy"
    deploy_skill.mkdir()
    (deploy_skill / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: Deploy to staging\nuser-invocable: true\n---\n"
        "# Deploy\n1. Build 2. Test 3. Ship",
        encoding="utf-8",
    )

    # .agents/skills/ (根级, 兼容路径)
    agents_skills = project / ".agents" / "skills"
    agents_skills.mkdir(parents=True)
    review_skill = agents_skills / "review"
    review_skill.mkdir()
    (review_skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Code review helper\nuser-invocable: true\n---\n"
        "# Code Review\nCheck for common issues.",
        encoding="utf-8",
    )

    print(f"  项目根: {project}")
    print(f"  src/:   {src_dir}")
    print(f"\n  创建的约定文件:")
    for path in sorted(project.rglob("*"), key=str):
        if path.is_file() and not any(p.startswith('.') for p in path.parts if p != project.name):
            rel = path.relative_to(tmpdir)
            print(f"    {rel}")

    # ══════════════════════════════════════════════════════════════════
    # Part B: CLAUDE.md + Rules 发现 — discover_claude_md_files()
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part B] discover_claude_md_files() — 从 src/ 向上扫描...")
    print("  源码: prompts/claudemd.py:8")

    discovered = discover_claude_md_files(src_dir)
    print(f"\n  发现 {len(discovered)} 个指令文件 (从远到近):")
    for i, f in enumerate(discovered):
        rel = f.relative_to(tmpdir)
        print(f"    {i+1}. {rel}")

    claude_md_prompt = load_claude_md_prompt(src_dir, max_chars_per_file=500)
    print(f"\n  load_claude_md_prompt() 输出 (前 600 字符):")
    for line in claude_md_prompt.split("\n")[:20]:
        print(f"    | {line}")
    if len(claude_md_prompt.split("\n")) > 20:
        print(f"    ... (共 {len(claude_md_prompt)} 字符)")

    # 优先级说明
    print(f"\n  [优先级规则] list 中后面的文件 (更近的目录) 覆盖前面的:")

    # ══════════════════════════════════════════════════════════════════
    # Part C: Skills 目录发现 — discover_project_skill_dirs()
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part C] discover_project_skill_dirs() — Skills 目录扫描...")
    print("  源码: skills/loader.py:83")
    print(f"  默认扫描目录: {list(_DEFAULT_PROJECT_SKILL_DIRS)}")

    project_skill_dirs = discover_project_skill_dirs(src_dir)
    print(f"\n  发现 {len(project_skill_dirs)} 个 skill 目录:")
    for d in project_skill_dirs:
        rel = d.relative_to(tmpdir)
        skills_in_dir = list(d.glob("*/SKILL.md"))
        print(f"    {rel}/ (包含 {len(skills_in_dir)} 个 skill)")

    # ══════════════════════════════════════════════════════════════════
    # Part D: Skills 注册 — load_skill_registry() 完整扫描
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part D] load_skill_registry() — 全量加载...")
    print("  源码: skills/loader.py:42")
    print("  扫描链路: bundled → user → extra_skill_dirs → project → plugins")

    # 注意: load_skill_registry 需要 cwd 在 git repo 内才能扫描 project dirs
    # 我们的 tmpdir 不在 git repo 内, 所以 project dirs 不会被扫描
    # 需要 create_missing=True (默认) - 只在调用 load_skills_from_dirs 时
    # 这里项目 dirs 不存在所以不会加载

    # 用 extra_skill_dirs 绕过
    import os as _os

    orig_cwd = _os.getcwd()
    try:
        _os.chdir(str(project))
        (project / ".git").mkdir(exist_ok=True)
        registry = load_skill_registry(cwd=project)
    finally:
        _os.chdir(orig_cwd)

    project_skills = [s for s in registry.list_skills() if s.source == "project"]
    print(f"\n  项目级 skills: {len(project_skills)} 个")
    for s in project_skills:
        print(f"    ✓ {s.name} [{s.source}] — {s.description}")

    # ══════════════════════════════════════════════════════════════════
    # Part E: 全局 User Rules — load_local_rules()
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part E] load_local_rules() — 用户级全局规则...")
    print("  源码: personalization/rules.py:18")
    print("  路径: ~/.openharness/local_rules/rules.md")

    local_rules = load_local_rules()
    if local_rules:
        print(f"\n  rules.md 内容 ({len(local_rules)} 字符):")
        for line in local_rules.split("\n")[:6]:
            print(f"    | {line}")
    else:
        print(f"\n  (未配置 — 文件不存在，返回空字符串)")
        print(f"  创建方式: oh rules 命令 或手动编辑 ~/.openharness/local_rules/rules.md")

    # ══════════════════════════════════════════════════════════════════
    # Part F: 完整的 System Prompt 装配流程
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part F] build_runtime_system_prompt() 完整装配流程...")
    print("  源码: prompts/context.py:77")
    print()
    print("  装配步骤 (按拼接顺序):")
    print("  ┌─────────────────────────────────────────────────────────────┐")
    print("  │ 1. build_system_prompt(custom_prompt, cwd)                  │")
    print("  │    → 基础系统提示 + 工具列表 + CLAUDE.md(来自discover)       │")
    print("  │                                                              │")
    print("  │ 2. Reasoning Settings (effort/passes)                       │")
    print("  │                                                              │")
    print("  │ 3. _build_skills_section()                ← todo @Toby 7.1  │")
    print("  │    → SkillRegistry 的所有 name+description                   │")
    print("  │                                                              │")
    print("  │ 4. _build_delegation_section()                               │")
    print("  │    → 子 Agent 使用说明                                       │")
    print("  │                                                              │")
    print("  │ 5. load_claude_md_prompt(cwd)              ← todo @Toby 7.2  │")
    print("  │    → CLAUDE.md + .claude/CLAUDE.md + .claude/rules/*.md     │")
    print("  │                                                              │")
    print("  │ 6. load_local_rules()                     ← todo @Toby 7.3  │")
    print("  │    → ~/.openharness/local_rules/rules.md                     │")
    print("  │                                                              │")
    print("  │ 7. Issue / PR / ActiveRepo context files                    │")
    print("  │                                                              │")
    print("  │ 8. load_memory_prompt()                   ← todo @Toby 7.4  │")
    print("  │    → MEMORY.md + 语义搜索 relevant memories                  │")
    print("  └─────────────────────────────────────────────────────────────┘")

    # ══════════════════════════════════════════════════════════════════
    # Part G: 模拟实际 build_runtime 来验证所有约定加载
    # ══════════════════════════════════════════════════════════════════
    print("\n[Part G] 用 build_runtime 实际验证...")
    print("  从 src/ 启动 runtime，验证所有 .claude/ 约定被自动加载")

    # 需要在 project 下创建 .git 假装是 git repo
    (project / ".git").mkdir(exist_ok=True)

    try:
        _os.chdir(str(src_dir))
        bundle = await build_runtime(
            cwd=str(src_dir),
            model="deepseek-chat",
            max_turns=1,
            permission_mode="full_auto",
        )
        await start_runtime(bundle)

        # 提取 system prompt 中的关键段
        sys_prompt = bundle.engine._system_prompt
        print(f"\n  system prompt 总长: {len(sys_prompt)} 字符\n")

        # 查找 CLAUDE.md 内容是否被注入
        checks = [
            ("CLAUDE.md 根级", "Run tests with pytest"),
            (".claude/CLAUDE.md", "Use ruff for linting"),
            (".claude/rules/01-security.md", "Never check in secrets"),
            ("src/CLAUDE.md", "source directory"),
            ("skills section", "cli-guide"),
            ("deploy skill", "deploy"),
        ]
        for label, keyword in checks:
            found = keyword in sys_prompt
            icon = "✓" if found else "✗"
            print(f"    {icon} {label}: '{keyword}' → {'找到' if found else '未找到'}")

        await close_runtime(bundle)
    finally:
        _os.chdir(orig_cwd)

    # ══════════════════════════════════════════════════════════════════
    # 总结
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print(".claude/ 约定目录加载机制总结:")
    print()
    print("  核心原理: cwd-relative path discovery + glob 扫描 + 优先级排序")
    print()
    print("  三条扫描链:")
    print("  1. CLAUDE.md 链:  {dir}/CLAUDE.md → .claude/CLAUDE.md → .claude/rules/*.md")
    print("  2. Skills 链:     bundled → user(~/.openharness/skills/) → project(.claude/skills/) → plugin")
    print("  3. Rules 链:      项目级(.claude/rules/) + 用户级(~/.openharness/local_rules/rules.md)")
    print()
    print("  关键特性:")
    print("  - 从 cwd 向上遍历到 git root (或 home)")
    print("  - 子目录文件优先级高于父目录 (后面拼入，语义覆盖)")
    print("  - 每个 loader 独立扫描，互不依赖")
    print("  - 兼容多个命名空间: .claude/ / .openharness/ / .agents/")
    print("=" * 70)

    shutil.rmtree(tmpdir)
    print("\n进阶 Demo 15 完成!")


# ═══════════════════════════════════════════════════════════════════════
# ★ Sandbox Demo: 命令隔离执行 — 核心原理 + 完整链路串联
# ═══════════════════════════════════════════════════════════════════════


async def demo_sandbox():
    """Sandbox 沙箱隔离: 完整串联所有核心组件，无需 Docker/LLM。

    覆盖 4 个 Case:
    Case 1 — 配置模型 + 可用性检测 (config/settings + adapter.availability)
    Case 2 — srt 命令包装 (adapter.wrap_command_for_sandbox)
    Case 3 — 路径边界校验 (path_validator.validate_sandbox_path)
    Case 4 — Docker 镜像管理 (docker_image + Dockerfile 内容验证)

    架构原理:
    ┌──────────────────────────────────────────────────────────────┐
    │  Sandbox 解决的核心问题:                                      │
    │  Agent 拥有 bash + 文件读写等强大工具，可能:                    │
    │    1. 读取 /etc/passwd, ~/.ssh/ 等敏感文件                    │
    │    2. 外发数据到恶意服务器 (network exfiltration)              │
    │    3. 安装恶意软件或修改系统配置                               │
    │    4. 窃取环境变量中的凭证                                     │
    │                                                               │
    │  Sandbox 的应对:                                              │
    │    → 文件系统隔离: 限制只能访问 project directory              │
    │    → 网络隔离: allow/deny domain 白名单/黑名单                │
    │    → 两层后端: srt (OS级 sandbox-exec/bwrap) + Docker        │
    │    → 所有 shell 命令都经过 sandbox wrapping 执行              │
    └──────────────────────────────────────────────────────────────┘

    两个后端的区别:
    ┌───────────────────┬──────────────────────┬──────────────────────┐
    │                   │ srt (sandbox-runtime) │ Docker              │
    ├───────────────────┼──────────────────────┼──────────────────────┤
    │ 实现原理          │ OS 级沙箱            │ 容器隔离             │
    │ 依赖              │ srt CLI + sandbox-   │ Docker CLI + daemon  │
    │                   │ exec(macOS)/bwrap    │                      │
    │ 网络控制粒度      │ 域名级 allow/deny    │ 完全禁用 (--net none)│
    │ 文件系统控制      │ allow/deny read/write│ bind-mount 项目目录  │
    │ 命令包装方式      │ srt --settings ...   │ docker exec ...      │
    │                   │   -c "command"       │                      │
    │ 生命周期          │ 每条命令独立         │ 会话级长生命周期容器 │
    └───────────────────┴──────────────────────┴──────────────────────┘

    文件结构:
      sandbox/__init__.py      — 公开 API 导出
      sandbox/adapter.py       — srt 可用性检测 + 命令包装
      sandbox/docker_backend.py — Docker 容器生命周期管理
      sandbox/session.py       — 模块级 session 注册表
      sandbox/path_validator.py — 文件路径边界校验
      sandbox/docker_image.py  — Docker 镜像 ensure/build

    集成点 (外部调用者):
      utils/shell.py           — create_shell_subprocess() 调度入口
      ui/runtime.py            — start_runtime/close_runtime 管理 session
      tools/bash_tool.py       — bash 工具执行时被 sandbox 包装
      swarm/permission_sync.py — 子进程同步 sandbox 配置
    """
    import json
    import os
    import tempfile

    from pathlib import Path

    from openharness.config.settings import (
        Settings,
        SandboxSettings,
        SandboxNetworkSettings,
        SandboxFilesystemSettings,
        DockerSandboxSettings,
        load_settings,
    )
    from openharness.sandbox.adapter import (
        SandboxAvailability,
        SandboxUnavailableError,
        build_sandbox_runtime_config,
        get_sandbox_availability,
        wrap_command_for_sandbox,
    )
    from openharness.sandbox.path_validator import validate_sandbox_path
    from openharness.sandbox.docker_image import (
        get_dockerfile_content,
        _image_exists,
        ensure_image_available,
    )
    from openharness.sandbox.docker_backend import get_docker_availability

    print("=" * 70)
    print("★ Sandbox 沙箱隔离 Demo: 核心原理 + 完整链路串联")
    print("=" * 70)

    # ══════════════════════════════════════════════════════════════════
    # Case 1: 配置模型 + 可用性检测
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 1: 配置模型 + 可用性检测 (adapter.py)")
    print("=" * 70)

    # 1a. 默认配置 — sandbox 默认关闭
    print("\n  1a. 默认 SandboxSettings:")
    default_sandbox = SandboxSettings()
    print(f"      enabled:              {default_sandbox.enabled}")
    print(f"      backend:              {default_sandbox.backend}")
    print(f"      fail_if_unavailable:  {default_sandbox.fail_if_unavailable}")
    print(f"      filesystem.allow_write: {default_sandbox.filesystem.allow_write}")

    # 1b. 完整配置示例 — 展示所有可配置项
    print("\n  1b. 完整配置示例 (展示所有可配置项):")
    full_sandbox = SandboxSettings(
        enabled=True,
        backend="docker",
        fail_if_unavailable=False,
        enabled_platforms=["macos", "linux"],
        network=SandboxNetworkSettings(
            allowed_domains=["api.github.com", "pypi.org"],
            denied_domains=["*"],
        ),
        filesystem=SandboxFilesystemSettings(
            allow_read=["."],
            deny_read=["/etc", "/home"],
            allow_write=["."],
            deny_write=["/etc", "/usr"],
        ),
        docker=DockerSandboxSettings(
            image="openharness-sandbox:latest",
            auto_build_image=True,
            cpu_limit=2.0,
            memory_limit="512m",
        ),
    )
    print(f"      backend:              {full_sandbox.backend}")
    print(f"      network.allowed:      {full_sandbox.network.allowed_domains}")
    print(f"      network.denied:       {full_sandbox.network.denied_domains}")
    print(f"      filesystem.allow_read: {full_sandbox.filesystem.allow_read}")
    print(f"      docker.image:         {full_sandbox.docker.image}")
    print(f"      docker.cpu_limit:     {full_sandbox.docker.cpu_limit}")
    print(f"      docker.memory_limit:  {full_sandbox.docker.memory_limit}")

    # 1c. 可用性检测 — 默认 sandbox 关闭时
    print("\n  1c. 可用性检测 (sandbox disabled, 默认配置):")
    avail = get_sandbox_availability()
    print(f"      enabled={avail.enabled}, available={avail.available}, "
          f"active={avail.active}, reason={avail.reason}")

    # 1d. 可用性检测 — 手动开启 sandbox
    print("\n  1d. 可用性检测 (手动开启 sandbox.enabled=True):")
    s = load_settings()
    s_with_sandbox = s.model_copy(
        update={"sandbox": SandboxSettings(enabled=True)}
    )
    avail2 = get_sandbox_availability(s_with_sandbox)
    print(f"      enabled={avail2.enabled}, available={avail2.available}, "
          f"active={avail2.active}")
    print(f"      reason={avail2.reason}")
    if avail2.command:
        print(f"      srt command: {avail2.command}")

    # 1e. Docker 可用性
    print("\n  1e. Docker 可用性:")
    docker_s = s.model_copy(
        update={"sandbox": SandboxSettings(enabled=True, backend="docker")}
    )
    docker_avail = get_docker_availability(docker_s)
    print(f"      available={docker_avail.available}, reason={docker_avail.reason}")

    # ══════════════════════════════════════════════════════════════════
    # Case 2: srt 命令包装 (adapter.py wrap_command_for_sandbox)
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 2: srt 命令包装 (adapter.py wrap_command_for_sandbox)")
    print("=" * 70)

    # 2a. srt settings payload 构建 (需要完整 Settings 对象)
    print("\n  2a. build_sandbox_runtime_config() → srt settings JSON:")
    full_settings = Settings(sandbox=full_sandbox)
    srt_config = build_sandbox_runtime_config(full_settings)
    print(f"      {json.dumps(srt_config, indent=6)}")

    # 2b. 命令包装（sandbox 不可用时直接返回原命令）
    print("\n  2b. wrap_command_for_sandbox() — sandbox 不可用时:")
    cmd, cleanup = wrap_command_for_sandbox(
        ["bash", "-c", "echo hello"],
        settings=s,  # sandbox.enabled=False
    )
    print(f"      wrapped: {cmd}")
    print(f"      cleanup: {cleanup}")

    # 2c. 命令包装（sandbox 可用时 — 需要 srt/sandbox-exec 存在才展示真实包装）
    print("\n  2c. wrap_command_for_sandbox() — sandbox.enabled=True 时:")
    cmd2, cleanup2 = wrap_command_for_sandbox(
        ["bash", "-c", "echo hello"],
        settings=s_with_sandbox,
    )
    if cleanup2 is not None:
        # srt 可用: cmd2 是 ["srt", "--settings", "...", "-c", "bash -c echo hello"]
        print(f"      wrapped ({len(cmd2)} tokens): {' '.join(cmd2)}")
        print(f"      settings file: {cleanup2}")
        if cleanup2.exists():
            raw = cleanup2.read_text().strip()
            print(f"      settings content: {raw}")
        else:
            print("      (settings file already cleaned up)")
    else:
        # srt 不可用, fail_if_unavailable=False → 返回原命令
        print(f"      wrapped: {cmd2}")
        print(f"      (srt 不可用, 返回原始命令)")

    # ══════════════════════════════════════════════════════════════════
    # Case 3: 路径边界校验 (path_validator.py)
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 3: 路径边界校验 (path_validator.py)")
    print("=" * 70)

    cwd = Path.cwd()
    test_paths = [
        (cwd / "src" / "main.py", "项目内文件"),
        (cwd / ".env", "项目内配置文件"),
        (Path("/etc/passwd"), "系统敏感文件"),
        (Path.home() / ".ssh" / "id_rsa", "SSH 私钥"),
        (Path("/tmp/test.txt"), "临时文件"),
    ]

    print(f"\n  工作目录 (sandbox boundary): {cwd}")
    print()
    for path, desc in test_paths:
        ok, reason = validate_sandbox_path(path, cwd)
        icon = "✓ ALLOW" if ok else "✗ DENY"
        print(f"      {icon}  {desc:20s} → {path}")
        if not ok:
            print(f"             reason: {reason}")

    # 3b. extra_allowed 路径
    print(f"\n  3b. 带 extra_allowed 路径 (/tmp):")
    ok, reason = validate_sandbox_path(
        Path("/tmp/test.txt"), cwd, extra_allowed=["/tmp"]
    )
    print(f"      /tmp/test.txt → {'ALLOW' if ok else 'DENY'} ({reason})")

    # ══════════════════════════════════════════════════════════════════
    # Case 4: Docker 镜像管理 + Dockerfile 验证
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Case 4: Docker 镜像管理 (docker_image.py + Dockerfile)")
    print("=" * 70)

    # 4a. Dockerfile 内容
    print("\n  4a. 自带 Dockerfile 内容:")
    dockerfile = get_dockerfile_content()
    for line in dockerfile.strip().split("\n"):
        print(f"      | {line}")

    # 4b. Dockerfile 文件存在性
    dockerfile_path = Path(__file__).parent / "sandbox" / "Dockerfile"
    print(f"\n  4b. Dockerfile 文件: {dockerfile_path}")
    print(f"      exists: {dockerfile_path.exists()}")

    # 4c. 镜像可用性检查 (需要 Docker daemon)
    print("\n  4c. 镜像可用性检查 (ensure_image_available):")
    print(f"      默认镜像名: openharness-sandbox:latest")
    if docker_avail.available:
        try:
            # 检查默认镜像是否存在
            exists = await _image_exists("openharness-sandbox:latest")
            print(f"      image exists locally: {exists}")
            if exists:
                print(f"      → 镜像已就绪, sandbox 可以直接启动")
        except Exception as exc:
            print(f"      检查失败: {exc}")
    else:
        print(f"      Docker 不可用, 跳过镜像检查")

    # 4d. 展示 Shell 集成点 (utils/shell.py create_shell_subprocess 调度逻辑)
    print("\n  4d. 集成点 — create_shell_subprocess() 调度决策:")
    print(f"""
      源码: utils/shell.py:62 create_shell_subprocess()

      决策树 (运行时每个 shell 命令都走这里):
      ┌─ sandbox.enabled=True, backend="docker"
      │   ├─ get_docker_sandbox() 返回活跃 session?
      │   │   ├─ YES → session.exec_command(argv, cwd=...)
      │   │   │         实际执行: docker exec -w {cwd} <container> bash -c "..."
      │   │   └─ NO
      │   │       ├─ fail_if_unavailable=True  → raise SandboxUnavailableError
      │   │       └─ fail_if_unavailable=False → fall through to srt / bare exec
      │   │
      │   └─ sandbox.enabled=True, backend="srt" (默认)
      │       └─ wrap_command_for_sandbox(argv)
      │           ├─ srt 可用 → srt --settings ... -c "bash -c ..."
      │           └─ srt 不可用
      │               ├─ fail_if_unavailable=True  → raise SandboxUnavailableError
      │               └─ fail_if_unavailable=False → 返回原始命令 (裸执行)
      │
      └─ sandbox.enabled=False (默认)
          └─ 直接 asyncio.create_subprocess_exec(*argv)
      """)

    # ══════════════════════════════════════════════════════════════════
    # Case 5: Session 生命周期模拟 (session.py)
    # ══════════════════════════════════════════════════════════════════
    print("=" * 70)
    print("Case 5: Session 生命周期模拟 (session.py)")
    print("=" * 70)

    from openharness.sandbox.session import (
        is_docker_sandbox_active,
        get_docker_sandbox,
    )

    print(f"\n  当前活跃 session: {get_docker_sandbox()}")
    print(f"  is_docker_sandbox_active: {is_docker_sandbox_active()}")
    print(f"\n  生命周期 (正常流程):")
    print(f"    start_runtime()")
    print(f"      → start_docker_sandbox(settings, session_id, cwd)")
    print(f"        → DockerSandboxSession(settings, session_id, cwd)")
    print(f"        → session.start()  # docker run -d ...")
    print(f"        → _active_session = session")
    print(f"        → atexit.register(session.stop_sync)  # 安全兜底")
    print(f"    ... Agent runs, tools execute via docker exec ...")
    print(f"    close_runtime()")
    print(f"      → stop_docker_sandbox()")
    print(f"        → session.stop()  # docker stop ...")

    # ══════════════════════════════════════════════════════════════════
    # 总结
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Sandbox 架构总结")
    print("=" * 70)
    print("""
  核心原理: OS-level / Container-level 命令执行隔离

  解决什么问题:
    当 LLM Agent 拥有 bash 工具、文件读写工具等能力时,
    Sandbox 确保 Agent 的所有操作被限制在项目目录内,
    并且网络访问受控 — 防止数据泄露、系统破坏、凭证窃取。

  两层后端:
    1. srt (sandbox-runtime): OS 级沙箱, 每命令独立, 适合 macOS/Linux
       依赖: npm install -g @anthropic-ai/sandbox-runtime
             + sandbox-exec (macOS) / bwrap (Linux)

    2. Docker: 容器隔离, 会话级长生命周期, 跨平台
       依赖: Docker Desktop / Docker Engine

  关键文件:
    sandbox/adapter.py          — 可用性检测 + 命令包装 (srt backend)
    sandbox/docker_backend.py   — Docker 容器生命周期
    sandbox/session.py          — 模块级 session 注册表
    sandbox/path_validator.py   — 文件路径边界校验
    sandbox/docker_image.py     — 镜像 ensure/build
    sandbox/Dockerfile          — 自带镜像定义 (Python 3.11-slim + ripgrep/git)
    config/settings.py          — SandboxSettings 完整配置模型
    utils/shell.py              — create_shell_subprocess() 调度入口
    ui/runtime.py               — start/close_runtime 触发 session 启停

  默认行为:
    sandbox.enabled = False  → 所有命令裸执行, 无隔离
    要启用: 设置 OPENHARNESS_SANDBOX_ENABLED=true 或在 settings.json 中配置
    """)

    print("=" * 70)
    print("Sandbox Demo 完成!")
    print("=" * 70)


async def main():
    """需要测哪个就把注释打开，测完注释回去即可。"""

    # ── 基础 4 层 ──────────────────────────────────────────────
    # await demo_layer1()
    # await demo_layer2()
    # await demo_layer3()
    # await demo_layer4()          # 仅录入任务，不执行 LLM
    # await demo_layer4(run=True)  # 真正执行 run_card（需 git 干净）

    # ── 综合案例 (4 层全串联) ─────────────────────────────────
    # await demo_all_layers()

    # ── 调试工具 ──────────────────────────────────────────────
    # await demo_debug_all_events()

    # ── Sandbox 沙箱隔离 (无需 Docker/LLM, 纯 API 层) ────────
    # await demo_sandbox()

    # ── 进阶 Demo ─────────────────────────────────────────────
    # await demo_advanced_hooks_permission()
    # await demo_advanced_multi_agent()
    # await demo_advanced_multi_agent_real()
    # await demo_advanced_autopilot()
    # await demo_advanced_autopilot_run()
    # await demo_advanced_custom_plugin()
    # await demo_advanced_mcp()
    # await demo_advanced_memory()
    # await demo_advanced_skill_mcp_hit_api()       # 纯 API 层验证，无需 LLM
    # await demo_advanced_skill_mcp_hit_agent_loop()  # Agent Loop 层，需 LLM
    # await demo_advanced_plugin_full_lifecycle()  # Plugin 完整生命周期
    # await demo_advanced_claude_conventions()    # .claude/ 约定目录加载机制
    await demo_advanced_coordinator()


if __name__ == "__main__":
    asyncio.run(main())
