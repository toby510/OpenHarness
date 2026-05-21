# Skill & MCP 命中后加载 Demo 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `my_debug_demo.py` 中新增两个 demo 函数，分别展示 skill 和 MCP 的"命中后加载"机制（纯 API 层 + Agent Loop 层）。

**Architecture:** 复用已有 demo 模式：Demo 12 直接调用 `SkillTool`/`McpToolAdapter` 底层 API 展示命中前后差异；Demo 13 走完整 `build_runtime` + `submit_message` Agent Loop 让 LLM 自主触发。

**Tech Stack:** Python asyncio, OpenHarness internal APIs (`skills.loader`, `tools.skill_tool`, `tools.mcp_tool`, `mcp.client`, `ui.runtime`)

---

### File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/openharness/my_debug_demo.py` | Modify | 新增 `demo_advanced_skill_mcp_hit_api()` 和 `demo_advanced_skill_mcp_hit_agent_loop()` 两个函数 |

---

### Task 1: Demo 12 — 纯 API 层命中加载验证

**Files:**
- Modify: `src/openharness/my_debug_demo.py`

- [ ] **Step 1: 实现 `demo_advanced_skill_mcp_hit_api()` 函数**

  在 `demo_advanced_coordinator()` 之前插入新函数（保持 demo 编号顺序）。

  **Part A — Skill 命中后加载：**
  1. 用 `tempfile.mkdtemp()` 创建临时目录
  2. 写入 `test-hit-skill/SKILL.md`，frontmatter 包含 `name: test-hit-skill`、`user-invocable: true`、`description: A test skill to demonstrate hit-load mechanism`，body 包含一段可识别的 markdown 内容（如 `# Core Directive\n\nAlways think step by step.`）
  3. 调用 `load_skill_registry(extra_skill_dirs=[tmpdir])` 获取 registry
  4. 打印命中前状态：`registry.get("test-hit-skill")` 返回的 `SkillDefinition` 的 `name`、`description`、`content` 长度
  5. 实例化 `SkillTool()`，构造 `ToolExecutionContext(cwd=Path.cwd(), metadata={"extra_skill_dirs": [tmpdir]})`
  6. 调用 `skill_tool.execute(SkillToolInput(name="test-hit-skill"), context)`
  7. 打印命中后状态：返回的 `ToolResult.output` 的前 200 字符
  8. 断言对比：命中前 `content` 长度 == 命中后 output 长度（验证完整内容被加载）

  **Part B — MCP 命中后加载：**
  1. 定位 `tests/fixtures/fake_mcp_server.py`，用 `sys.executable` 启动 stdio MCP server
  2. 创建 `McpStdioServerConfig` + `McpClientManager` 并 `await manager.connect_all()`
  3. 打印命中前状态：`manager.list_tools()` 返回的工具 name/description
  4. 找到 `hello` 工具的 `McpToolInfo`，实例化 `McpToolAdapter(manager, tool_info)`
  5. 构造 `ToolExecutionContext`
  6. 调用 `adapter.execute(adapter.input_model(name="OpenHarness"), context)`
  7. 打印命中后状态：返回的 `ToolResult.output`
  8. 清理：`await manager.close()`，删除临时 skill 目录

  代码框架（需填入 `my_debug_demo.py`）：
  ```python
  async def demo_advanced_skill_mcp_hit_api():
      """纯 API 层验证 skill 和 MCP 的命中后加载机制."""
      import tempfile
      import sys

      from openharness.skills.loader import load_skill_registry
      from openharness.skills.types import SkillDefinition
      from openharness.tools.skill_tool import SkillTool, SkillToolInput
      from openharness.tools.mcp_tool import McpToolAdapter
      from openharness.tools.base import ToolExecutionContext
      from openharness.mcp.client import McpClientManager
      from openharness.mcp.types import McpStdioServerConfig

      print("=" * 70)
      print("进阶 Demo 12: Skill & MCP 命中后加载 — API 层验证")
      print("=" * 70)

      # ── Part A: Skill 命中后加载 ──
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
      print(f"  [命中前] registry.get() -> name={skill_def.name}, content_len={len(skill_def.content)}")

      skill_tool = SkillTool()
      ctx = ToolExecutionContext(cwd=Path.cwd(), metadata={"extra_skill_dirs": [tmpdir]})
      result = await skill_tool.execute(SkillToolInput(name="test-hit-skill"), ctx)
      print(f"  [命中后] skill_tool.execute() -> output_len={len(result.output)}")
      print(f"  [命中后] output_preview: {result.output[:200]}...")
      assert len(skill_def.content) == len(result.output), "命中前后 content 长度应一致"
      print("  ✓ Skill 命中后加载验证通过")

      # ── Part B: MCP 命中后加载 ──
      print("\n[Part B] MCP 命中后加载...")
      fixture_server = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "fake_mcp_server.py"
      config = McpStdioServerConfig(
          type="stdio",
          command=sys.executable,
          args=[str(fixture_server)],
      )
      manager = McpClientManager({"fixture-demo": config})
      await manager.connect_all()

      tools = manager.list_tools()
      hello_tool_info = next((t for t in tools if t.name == "hello"), None)
      print(f"  [命中前] manager.list_tools() -> found '{hello_tool_info.name}': {hello_tool_info.description}")

      adapter = McpToolAdapter(manager, hello_tool_info)
      mcp_ctx = ToolExecutionContext(cwd=Path.cwd())
      # 动态创建输入模型实例
      InputModel = adapter.input_model
      mcp_result = await adapter.execute(InputModel(name="OpenHarness"), mcp_ctx)
      print(f"  [命中后] adapter.execute() -> {mcp_result.output}")
      assert "OpenHarness" in mcp_result.output, "MCP 工具应返回包含输入名字的问候"
      print("  ✓ MCP 命中后加载验证通过")

      await manager.close()
      import shutil
      shutil.rmtree(tmpdir)
      print("\n进阶 Demo 12 完成!")
  ```

- [ ] **Step 2: 运行验证 Demo 12**

  在 `my_debug_demo.py` 的 `main()` 中临时打开 `await demo_advanced_skill_mcp_hit_api()`，运行：
  ```bash
  uv run python src/openharness/my_debug_demo.py
  ```
  预期输出包含：
  - `✓ Skill 命中后加载验证通过`
  - `✓ MCP 命中后加载验证通过`

- [ ] **Step 3: Commit**

  ```bash
  git add src/openharness/my_debug_demo.py
  git commit -m "feat(demo): add skill and MCP hit-load API layer demo"
  ```

---

### Task 2: Demo 13 — Agent Loop 层 LLM 自主命中

**Files:**
- Modify: `src/openharness/my_debug_demo.py`

- [ ] **Step 1: 实现 `demo_advanced_skill_mcp_hit_agent_loop()` 函数**

  在 `demo_advanced_skill_mcp_hit_api()` 之后插入。

  **Setup：**
  1. 创建临时目录和 test skill（同 Demo 12 Part A）
  2. 启动 fake MCP server（同 Demo 12 Part B）
  3. 创建 `McpClientManager` 并连接
  4. `build_runtime(extra_skill_dirs=[tmpdir])` 获取 bundle
  5. 手动将 MCP 工具注册到 `bundle.tool_registry`（因为 `build_runtime` 不接受 mcp_manager 参数，需要在 build 后手动注册 `ListMcpResourcesTool`、`ReadMcpResourceTool`、所有 `McpToolAdapter`）
  6. `await start_runtime(bundle)`

  **Case A — Skill 命中：**
  - Prompt: `"请使用 test-hit-skill 技能，告诉我它的核心指令是什么"`
  - 遍历事件流，打印 `ToolExecutionStarted` / `ToolExecutionCompleted`
  - 验证：有 `skill` 工具被调用，且最终回答中包含 "step by step"

  **Case B — MCP 命中：**
  - Prompt: `"请使用 MCP 工具 hello，向它打招呼并说你的名字是 OpenHarness"`
  - 遍历事件流，打印 `ToolExecutionStarted` / `ToolExecutionCompleted`
  - 验证：有 `mcp__fixture_demo__hello` 工具被调用

  **Cleanup：**
  - `await close_runtime(bundle)`
  - `await manager.close()`
  - 删除临时目录

  关键代码片段（手动注册 MCP 工具到已有 runtime）：
  ```python
  from openharness.tools.list_mcp_resources_tool import ListMcpResourcesTool
  from openharness.tools.read_mcp_resource_tool import ReadMcpResourceTool
  from openharness.tools.mcp_tool import McpToolAdapter

  # build_runtime 后手动注入 MCP 工具
  bundle.tool_registry.register(ListMcpResourcesTool(manager))
  bundle.tool_registry.register(ReadMcpResourceTool(manager))
  for tool_info in manager.list_tools():
      bundle.tool_registry.register(McpToolAdapter(manager, tool_info))
  ```

- [ ] **Step 2: 运行验证 Demo 13**

  在 `main()` 中临时打开 `await demo_advanced_skill_mcp_hit_agent_loop()`，运行：
  ```bash
  uv run python src/openharness/my_debug_demo.py
  ```
  预期：LLM 先后调用 `skill` 和 `mcp__fixture_demo__hello` 工具，输出包含工具调用过程和结果。

- [ ] **Step 3: 恢复 main() 默认状态**

  两个 demo 默认都应注释掉（遵循文件现有约定），`main()` 保持调用 `await demo_advanced_coordinator()` 或其他原有默认。

- [ ] **Step 4: Commit**

  ```bash
  git add src/openharness/my_debug_demo.py
  git commit -m "feat(demo): add skill and MCP hit-load Agent Loop demo"
  ```

---

### Self-Review

**1. Spec coverage：**
- Demo 12 纯 API 层 → Task 1 ✓
- Demo 13 Agent Loop 层 → Task 2 ✓
- Skill 命中前后对比 → Step 1 Part A 打印命中前/后状态 ✓
- MCP 命中前后对比 → Step 1 Part B 打印命中前/后状态 ✓
- 临时 skill 创建/清理 → 两处均有 `shutil.rmtree` ✓
- MCP server 启停 → `manager.connect_all()` / `manager.close()` ✓

**2. Placeholder scan：** 无 TBD/TODO/"implement later"。

**3. Type consistency：** `SkillToolInput`、`McpStdioServerConfig`、`McpToolAdapter` 的导入路径和用法与现有代码一致。
