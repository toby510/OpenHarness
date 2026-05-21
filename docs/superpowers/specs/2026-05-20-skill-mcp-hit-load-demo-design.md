# Skill & MCP 命中后加载 Demo 设计

## 背景

OpenHarness 的 skill 和 MCP 工具都采用"懒加载"策略：
- **Skill**：`build_runtime_system_prompt()` 阶段只把 skill 的 name/description 注入系统提示（轻量索引）。当 Agent 调用 `skill(name="...")` 工具时，`SkillTool.execute()` 才从 `SkillRegistry` 中取出完整 content 返回（命中后加载）。
- **MCP**：`McpClientManager.connect_all()` 阶段只获取工具的 name/description/schema（轻量索引）。当 Agent 调用某个 MCP 工具时，`McpToolAdapter.execute()` 才通过 `session.call_tool()` 实际转发到 MCP server（命中后加载）。

`my_debug_demo.py` 缺少直接观测这两种"命中后加载"机制的 demo。

## 设计

### Demo 12: 纯 API 层 — 命中加载机制验证

函数名：`demo_advanced_skill_mcp_hit_api()`

**Part A — Skill 命中后加载**
1. 在临时目录创建 `test-hit-skill/SKILL.md`，包含 frontmatter + markdown 内容
2. 调用 `load_skill_registry(extra_skill_dirs=[tmpdir])` → 验证 registry 发现 skill（命中前：只有摘要）
3. 实例化 `SkillTool`，构造 `ToolExecutionContext`
4. 调用 `skill_tool.execute({"name": "test-hit-skill"}, context)` → 返回完整 content（命中后：完整加载）
5. 打印对比：命中前 vs 命中后的数据差异

**Part B — MCP 命中后加载**
1. 启动 `tests/fixtures/fake_mcp_server.py`
2. 创建 `McpClientManager` 并 `connect_all()`
3. 验证 `manager.list_tools()` 已索引工具（命中前：只有元数据）
4. 用 `McpToolAdapter` 包装 `hello` 工具
5. 调用 `adapter.execute({"name": "OpenHarness"}, context)` → 实际转发到 server（命中后：完整执行）
6. 打印对比：命中前 vs 命中后的数据差异

### Demo 13: Agent Loop 层 — LLM 自主命中

函数名：`demo_advanced_skill_mcp_hit_agent_loop()`

**Setup**
1. 在临时目录创建 test skill
2. 启动 fake MCP server，创建 `McpClientManager`
3. `build_runtime(extra_skill_dirs=[tmpdir])` 并注入 MCP manager 到 tool registry

**Case A — Skill 命中**
- Prompt: "请使用 test-hit-skill 技能，告诉我它的核心指令是什么"
- 预期 LLM 调用 `skill(name="test-hit-skill")` → 返回完整 content

**Case B — MCP 命中**
- Prompt: "请使用 MCP 工具 hello，向它打招呼并说你的名字是 OpenHarness"
- 预期 LLM 调用 `mcp__fixture_demo__hello(name="OpenHarness")` → 返回 fake server 结果

**输出**：逐事件打印 `ToolExecutionStarted/Completed` + `AssistantTextDelta`

## 实现范围

- 仅修改 `src/openharness/my_debug_demo.py`，新增两个 async demo 函数
- 复用已有模式：Demo 8 的临时 skill 创建、Demo 9 的 MCP fake server
- 不修改任何核心库代码
- Demo 13 需要 LLM API key，默认走 deepseek-chat

## 测试方式

- Demo 12：直接 `await demo_advanced_skill_mcp_hit_api()`，无需 API key
- Demo 13：`await demo_advanced_skill_mcp_hit_agent_loop()`，需配置 API key
