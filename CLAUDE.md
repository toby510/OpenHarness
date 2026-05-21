# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenHarness is an open-source Python agent harness — the infrastructure that wraps an LLM to make it a functional agent (tools, skills, memory, permissions, multi-agent coordination). It ships as two packages:

- **`src/openharness`** — Core harness library and `oh` CLI
- **`ohmo`** — Personal AI agent app (`ohmo` CLI) built on OpenHarness, with chat-gateway integrations (Feishu, Slack, Telegram, Discord)

Package name on PyPI: `openharness-ai`. Built with `hatchling`, dependency-managed with `uv`.

## Development Commands

### Setup
```bash
uv sync --extra dev          # Install Python deps incl. dev
```
For React TUI work:
```bash
cd frontend/terminal && npm ci
```

### Run
```bash
uv run oh                    # Interactive TUI
uv run oh -p "..."           # Single prompt, stdout
uv run ohmo                  # ohmo personal agent
```

### Test
```bash
uv run pytest -q                              # All unit + integration tests (114+)
uv run pytest tests/test_api/ -q              # Single module
uv run pytest tests/test_tools/test_bash_tool.py::test_something -q   # Single test

# E2E suites (require real API keys)
python scripts/test_harness_features.py
python scripts/test_real_skills_plugins.py
python scripts/e2e_smoke.py
```

### Lint / Typecheck
```bash
uv run ruff check src tests scripts           # Python linting (line-length 100, py311)
uv run mypy src/openharness                   # Type checking (strict, optional)

cd frontend/terminal && npx tsc --noEmit      # Frontend typecheck
cd autopilot-dashboard && npm run build       # Dashboard build
```

### Build / Package
```bash
uv build                       # Build wheel via hatchling
```

## High-Level Architecture

### Two Packages, Shared Repo

- **`src/openharness/`** — Core harness. Imported as `openharness.*`. Contains the engine, tools, plugins, permissions, MCP client, React TUI backend, etc.
- **`ohmo/`** — Personal agent. Imported as `ohmo.*`. Has its own CLI (`ohmo/cli.py`), workspace logic (`ohmo/workspace.py`), gateway for chat channels, and runtime that delegates to OpenHarness.

Both are declared in `[tool.hatch.build.targets.wheel]` in `pyproject.toml`.

### `src/openharness/` Directory Map (29 个目录，4 层架构)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 4: 应用层 (消费 engine 循环)                                  │
│  ─────────────────────────────                                      │
│  autopilot/  channels/  coordinator/  swarm/  tasks/  bridge/      │
│  sandbox/  personalization/                                        │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3: UI & 交互层                                               │
│  ────────────────                                                   │
│  ui/  commands/  skills/  voice/  keybindings/  themes/  vim/      │
│  output_styles/  state/                                            │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2: 元能力/扩展层                                             │
│  ───────────────────                                                │
│  plugins/  mcp/  memory/  prompts/  services/                      │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1: ★ 核心引擎层 (所有路径必经之地)                             │
│  ─────────────────────────────────────                              │
│  engine/  tools/  permissions/  hooks/  api/  auth/  config/       │
└─────────────────────────────────────────────────────────────────────┘
```

**★ Layer 1 — 核心引擎（必读，理解 harness 的钥匙）**

| 目录 | 职责 | 核心文件 |
|------|------|---------|
| `engine/` | Agent Loop 心脏：`QueryEngine` 持有对话状态，`run_query()` 实现 LLM 对话循环，`_execute_tool_call()` 用 PreHook → 权限 → execute → PostHook 的流水线安全执行每个工具 | `query.py:723 run_query`, `query.py:921 _execute_tool_call`, `query_engine.py:165 submit_message` |
| `tools/` | 所有工具的基类 `BaseTool` + 注册表 `ToolRegistry`。43+ 个工具覆盖文件 I/O、Shell、搜索、Web、MCP、Cron、Agent 等 | `base.py` BaseTool/ToolRegistry, `__init__.py` 工具注册 |
| `permissions/` | 安全决策引擎：敏感路径无条件拒绝 → deny list → allow list → path rules → mode 决策 | `checker.py:PermissionChecker.evaluate()` |
| `hooks/` | 生命周期拦截：PreToolUse/PostToolUse 等 10 个事件点，支持 command/http/prompt 三种 hook 类型，可阻断工具执行 | `executor.py:HookExecutor.execute()` |
| `api/` | LLM 抽象层：Anthropic SDK + OpenAI 兼容客户端，自动重试（3 次，指数退避），provider 检测和 capabilities 查询 | `client.py` AnthropicApiClient, `openai_client.py` |
| `auth/` | 统一认证：API key / browser OAuth / device code 多种凭证流，加密存储 | |
| `config/` | 配置解析：CLI args → 环境变量 → `settings.json` → defaults，Pydantic 建模 | `settings.py` Settings/load_settings, `paths.py` |

**★ Layer 2 — 元能力/扩展层（让 harness 可扩展）**

| 目录 | 职责 |
|------|------|
| `plugins/` | 第三方插件系统：`plugin.json` manifest → 贡献 skills/commands/agents/tools/hooks/mcp |
| `mcp/` | Model Context Protocol 客户端：stdio + HTTP(Streamable) + WebSocket 三种传输 |
| `skills/` | 按需知识加载：从 bundled/user/project/plugin 四源发现 `.md`，frontmatter 解析 |
| `prompts/` | 运行时 prompt 构建：聚合 CLAUDE.md + 环境信息 + context provider |
| `memory/` | 持久化跨会话记忆：CLAUDE.md 加载、项目级 memory 条目存储 |
| `services/` | 后台服务：`compact/`(对话压缩)、`autodream/`(记忆整理)、`cron/`、`lsp/`(代码智能) |

**★ Layer 3 — UI & 交互层**

| 目录 | 职责 |
|------|------|
| `ui/` | TUI 层：Textual REPL + React 前端 + 权限弹窗 + 流式输出渲染 |
| `commands/` | 斜杠命令注册表：`/compact`, `/cost`, `/memory`, `/plan`, `/review`, `/deploy` 等 40+ 命令 |
| `skills/` | 技能系统 (同 Layer 2，也向 UI 暴露命令) |
| `voice/` | 语音输入：speech-to-text 流式识别 |
| `keybindings/` | 键盘快捷键配置 |
| `themes/` `output_styles/` `vim/` `state/` | 外观/样式/状态持久化 |

**★ Layer 4 — 应用层（消费 Agent Loop 的"上层建筑"）**

| 目录 | 职责 |
|------|------|
| `autopilot/` | 自主交付流水线：任务队列 → worktree 隔离 → Agent 执行 → 验证 → repair → git commit/PR/merge |
| `coordinator/` | 多 Agent 编排：`TeamRegistry` + `AgentDefinition` |
| `swarm/` | Agent 生命周期：subprocess 后台 Agent 生成、mailbox IPC、worktree 隔离、permission 同步 |
| `tasks/` | 后台任务管理：local agent / shell task 的 spawn、stop、status、result |
| `channels/` | 外部 IM 接入：Telegram/Discord/Slack/钉钉/飞书/WhatsApp/QQ/Matrix 等 10+ 平台 |
| `bridge/` | 子 CLI 会话桥接 |
| `sandbox/` | Docker 隔离执行 |
| `personalization/` | 自动提取用户偏好 |

### 重点阅读路径

理解 harness 架构只需要按这个顺序读 5 个文件：

| 优先级 | 文件 | 回答什么问题 |
|--------|------|------------|
| ★★★ | `engine/query.py:632 run_query` | Agent 循环怎么转起来的？ |
| ★★★ | `engine/query.py:921 _execute_tool_call` | 一个工具调用怎么被安全执行的？ |
| ★★ | `permissions/checker.py` | 凭什么决定 allow/deny/confirm？ |
| ★★ | `hooks/executor.py` | 插件怎么在工具执行前后插入逻辑？ |
| ★ | `tools/base.py` | 怎么扩展一个新工具？ |

读完这 5 个文件再看上层应用（autopilot、channels、coordinator）才能理解它们为什么这样设计。

### The Agent Loop (`engine/`)

The heart of the system is in `openharness.engine`:

- **`query_engine.py`** — `QueryEngine` owns conversation history and the tool-aware model loop. It wires together the API client, tool registry, permission checker, and hooks.
- **`query.py`** — `run_query()` implements the actual loop: stream LLM response → if `tool_use`, execute each tool (with permission checks + hooks) → append results → loop. Also handles auto-compaction when context window is exceeded.
- **`messages.py`** — `ConversationMessage`, `ToolUseBlock`, `ToolResultBlock`, etc. Normalized message types used across the codebase.
- **`stream_events.py`** — `StreamEvent` hierarchy (`AssistantTextDelta`, `ToolExecutionStarted`, etc.) emitted during the loop for UI consumption.

### Tools System (`tools/`)

Every tool is a class inheriting `BaseTool` (`tools/base.py`):
- Declares `name`, `description`, and a Pydantic `input_model`
- Implements `async def execute(self, arguments, context) -> ToolResult`
- Can declare `is_read_only()` for permission classification
- Exposes `to_api_schema()` for Anthropic Messages API compatibility

`ToolRegistry` (`tools/base.py`) collects all tools. Tools are registered in `tools/__init__.py`. There are 43+ tools covering file I/O, shell, search, web, MCP, tasks, agents, scheduling, notebooks, etc.

### Skills (`skills/`)

Skills are on-demand knowledge loaded from `.md` files. The loader (`skills/`) discovers `SKILL.md` files from:
- Bundled (in-package)
- User: `~/.openharness/skills/`, `~/.claude/skills/`, `~/.agents/skills/`
- Project: `<project>/.openharness/skills/` (up to git root)
- Plugin locations

Format is frontmatter + markdown, compatible with `anthropics/skills`. User-invocable skills can be run as slash commands (e.g. `/deploy staging`).

### Plugins (`plugins/`)

Plugin system compatible with `claude-code` plugins. A plugin is a directory with `.claude-plugin/plugin.json` and optional `commands/`, `hooks/`, `agents/` subdirectories. Managed via `oh plugin` CLI.

### Permissions (`permissions/`)

Multi-level safety:
- Modes: `default` (ask before write/execute), `auto` (allow all), `plan` (block writes)
- Path-level glob rules and denied command lists in `settings.json`
- `PermissionChecker` evaluated before every tool execution
- Interactive `PermissionPrompt` callback for CLI/TUI approval dialogs

### Hooks (`hooks/`)

Lifecycle event system: `PreToolUse` and `PostToolUse` hooks. `HookExecutor` runs registered hooks around each tool execution. Plugins can register hooks via `hooks.json`.

### MCP Client (`mcp/`)

Model Context Protocol client supporting stdio, HTTP, and WebSocket transports. Auto-reconnect on disconnect. JSON Schema types inferred for MCP tool inputs. Configured via `mcp_servers` in `settings.json`.

### Config System (`config/`)

Settings resolution precedence (highest first):
1. CLI arguments
2. Environment variables (`ANTHROPIC_API_KEY`, `OPENHARNESS_MODEL`, etc.)
3. `~/.openharness/settings.json`
4. Defaults

`Settings` class is a Pydantic model. `config/paths.py` defines data dirs.

### UI (`ui/`)

React/Ink TUI backend protocol + frontend. The CLI (`oh` with no args) launches the interactive TUI. `frontend/terminal/` contains the React+Ink frontend code that communicates with the backend via a protocol. `ui/runtime.py` manages the TUI runtime lifecycle.

### ohmo Gateway (`ohmo/gateway/`)

ohmo's chat gateway bridges OpenHarness to messaging platforms:
- **`service.py`** — Gateway lifecycle (start/stop/status)
- **`bridge.py`** — Message bridging between chat channels and OpenHarness runtime
- **`router.py`** — Routes incoming messages to the right handler
- **`group_tool.py`** — ohmo-specific group/channel tools
- **`provider_commands.py`** — Provider configuration commands

ohmo workspace lives at `~/.ohmo/` with `soul.md`, `identity.md`, `user.md`, `BOOTSTRAP.md`, `memory/`, and `gateway.json`.

### API Clients (`api/`)

Abstracted LLM client layer supporting:
- Anthropic-compatible API (Claude, Kimi, GLM, MiniMax)
- OpenAI-compatible API (OpenAI, OpenRouter, DeepSeek, Ollama, etc.)
- Claude subscription (reads `~/.claude/.credentials.json`)
- Codex subscription (reads `~/.codex/auth.json`)
- GitHub Copilot (OAuth device flow)

Profiles are managed via `oh provider` commands. Each profile stores its own auth source and base URL.

### Coordinator / Swarm (`coordinator/`, `swarm/`)

Multi-agent primitives:
- `Agent` tool spawns subagents
- `TeamCreate` / `TeamDelete` for team management
- `TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate` / `TaskStop` / `TaskOutput` for background tasks
- `SendMessage` for inter-agent communication

### Testing Conventions

- `tests/` mirrors the source structure roughly: `test_api/`, `test_tools/`, `test_engine/`, etc.
- `conftest.py` contains shared fixtures.
- `pytest-asyncio` is used with `asyncio_mode = auto`.
- E2E scripts in `scripts/` require real API credentials.
- The large integration tests (`test_hooks_skills_plugins_real.py`, `test_real_large_tasks.py`, `test_merged_prs_on_autoagent.py`, `test_untested_features.py`) test real provider interactions and may be slow.

### Code Style

- Line length: 100
- Target Python: 3.11+
- `from __future__ import annotations` used throughout
- Ruff for linting; mypy in strict mode (optional CI check)
