"""Higher-level system prompt assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from openharness.config.paths import (
    get_project_active_repo_context_path,
    get_project_issue_file,
    get_project_pr_comments_file,
)
from openharness.config.settings import Settings
from openharness.coordinator.coordinator_mode import get_coordinator_system_prompt, is_coordinator_mode
from openharness.memory import load_memory_prompt
from openharness.memory.relevance import format_relevant_memories, select_relevant_memories
from openharness.memory.usage import mark_memory_used
from openharness.personalization.rules import load_local_rules
from openharness.prompts.claudemd import load_claude_md_prompt
from openharness.prompts.system_prompt import build_system_prompt
from openharness.skills.loader import load_skill_registry


def _build_skills_section(
    cwd: str | Path,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Build a system prompt section listing available skills."""
    registry = load_skill_registry(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    skills = [skill for skill in registry.list_skills() if not skill.disable_model_invocation]
    if not skills:
        return None
    lines = [
        "# Available Skills",
        "",
        "The following skills are available via the `skill` tool. "
        "When a user's request matches a skill, invoke it with `skill(name=\"<skill_name>\")` "
        "to load detailed instructions before proceeding. "
        "User-invocable skills can also be run directly by the user as `/<skill-name>`.",
        "",
    ]
    for skill in skills:
        command_name = skill.command_name or skill.name
        display = f" ({skill.display_name})" if skill.display_name else ""
        lines.append(f"- **{command_name}**{display}: {skill.description}")
    return "\n".join(lines)


def _build_delegation_section() -> str:
    """Build a concise section describing delegation and worker usage."""
    return "\n".join(
        [
            "# Delegation And Subagents",
            "",
            "OpenHarness can delegate background work with the `agent` tool.",
            "Use it when the user explicitly asks for a subagent, background worker, or parallel investigation, "
            "or when the task clearly benefits from splitting off a focused worker.",
            "",
            "Default pattern:",
            '- Spawn with `agent(description=..., prompt=..., subagent_type=\"worker\")`.',
            "- Inspect running or recorded workers with `/agents`.",
            "- Inspect one worker in detail with `/agents show TASK_ID`.",
            "- Send follow-up instructions with `send_message(task_id=..., message=...)`.",
            "- Read worker output with `task_output(task_id=...)`.",
            "",
            "Prefer a normal direct answer for simple tasks. Use subagents only when they materially help.",
        ]
    )


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    include_project_memory: bool = True,
) -> str:
    """Build the runtime system prompt with project instructions and memory."""
    if is_coordinator_mode():
        sections = [get_coordinator_system_prompt()]
    else:
        sections = [build_system_prompt(custom_prompt=settings.system_prompt, cwd=str(cwd))]

    if not is_coordinator_mode() and settings.system_prompt is None:
        sections[0] = build_system_prompt(cwd=str(cwd))

    if settings.fast_mode:
        sections.append(
            "# Session Mode\nFast mode is enabled. Prefer concise replies, minimal tool use, and quicker progress over exhaustive exploration."
        )


    """
    20:34Claude responded: 这是给 AI 的推理强度配置指令，告诉模型在回答时该花多少"力气"思考。这是给 AI 的推理强度配置指令，告诉模型在回答时该花多少"力气"思考。
    Effort: medium（努力程度：中等）
    控制思考深度。有三档：
    
    low — 快速回答，浅层推理
    medium — 适中深度，平衡速度与质量
    high — 深度推理，更慢但更严谨
    
    Passes: 1（推理轮次：1次）
    控制内部迭代次数。1 表示想一遍就给答案，不反复自我检查和修正。2+ 则会多轮推敲。
    Adjust depth and iteration count to match these settings
    要求模型根据以上配置自我调节——既不过度分析浪费资源，也不敷衍了事。
    """
    sections.append(
        "# Reasoning Settings\n"
        f"- Effort: {settings.effort}\n"
        f"- Passes: {settings.passes}\n"
        "Adjust depth and iteration count to match these settings while still completing the task."
    )
    # todo @Toby注释: [装配-步骤7.1]加载项目skill模块
    skills_section = _build_skills_section(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    if skills_section and not is_coordinator_mode():
        sections.append(skills_section)

    if not is_coordinator_mode():
        sections.append(_build_delegation_section())

    # todo @Toby注释: [装配-步骤7.2]加载claude.md所有内容
    claude_md = load_claude_md_prompt(cwd)
    if claude_md:
        sections.append(claude_md)

    # todo @Toby注释: [装配-步骤7.3]加载rules.md所有内容
    local_rules = load_local_rules()
    if local_rules:
        sections.append(f"# Local Environment Rules\n\n{local_rules}")

    for title, path in (
        ("Issue Context", get_project_issue_file(cwd)),
        ("Pull Request Comments", get_project_pr_comments_file(cwd)),
        ("Active Repo Context", get_project_active_repo_context_path(cwd)),
    ):
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"# {title}\n\n```md\n{content[:12000]}\n```")

    # todo @Toby注释: [装配-步骤7.4]加载memory.md所有内容，以及跟提问相关的Relevant Memories
    if include_project_memory and settings.memory.enabled:
        memory_section = load_memory_prompt(
            cwd,
            max_entrypoint_lines=settings.memory.max_entrypoint_lines,
            max_entrypoint_bytes=settings.memory.max_entrypoint_bytes,
        )
        if memory_section:
            sections.append(memory_section)

        if latest_user_prompt:
            relevant = select_relevant_memories(
                latest_user_prompt,
                cwd,
                max_results=settings.memory.max_files,
            )
            if relevant:
                try:
                    headers = [item.header for item in relevant]
                    mark_memory_used(cwd, headers, memory_dir=headers[0].path.parent)
                except OSError:
                    pass
                sections.append(format_relevant_memories(relevant))

    return "\n\n".join(section for section in sections if section.strip())
