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
