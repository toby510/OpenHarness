"""Project-level repo autopilot state, intake, and execution helpers."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha1
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from openharness.autopilot.types import (
    RepoAutopilotRegistry,
    RepoJournalEntry,
    RepoRunResult,
    RepoTaskCard,
    RepoTaskSource,
    RepoTaskStatus,
    RepoVerificationStep,
)
from openharness.config.paths import (
    get_project_active_repo_context_path,
    get_project_autopilot_policy_path,
    get_project_autopilot_registry_path,
    get_project_autopilot_runs_dir,
    get_project_release_policy_path,
    get_project_repo_journal_path,
    get_project_verification_policy_path,
)
from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete, ErrorEvent
from openharness.swarm.worktree import WorktreeManager
from openharness.utils.fs import atomic_write_text

_SOURCE_BASE_SCORES: dict[RepoTaskSource, int] = {
    "ohmo_request": 100,
    "manual_idea": 80,
    "github_issue": 75,
    "github_pr": 85,
    "claude_code_candidate": 45,
}
_BUG_HINTS = ("bug", "fix", "failure", "broken", "regression", "crash", "error", "issue")
_URGENT_HINTS = ("urgent", "p0", "p1", "high", "critical", "blocker")

_DEFAULT_AUTOPILOT_POLICY = {
    "intake": {
        "mode": "unified_queue",
        "max_visible_candidates": 12,
        "dedupe_strategy": "source_ref_then_fingerprint",
    },
    "decision": {
        "default_human_gate": True,
        "prefer_small_safe_steps": True,
    },
    "execution": {
        "default_model": "",
        "max_turns": 12,
        "permission_mode": "full_auto",
        "host_mode": "self_hosted",
        "use_worktree": True,
        "base_branch": "main",
        "max_attempts": 3,
    },
    "github": {
        "issue_comment_style": "bilingual",
        "pr_branch_prefix": "autopilot/",
        "ci_poll_interval_seconds": 20,
        "ci_timeout_seconds": 1800,
        "no_checks_grace_seconds": 60,
        "checks_settle_seconds": 20,
        "auto_merge": {
            "mode": "label_gated",
            "required_label": "autopilot:merge",
        },
    },
    "repair": {
        "max_rounds": 2,
        "retry_on": ["local_verification_failed", "remote_ci_failed"],
        "stop_on": ["agent_runtime_error", "git_error", "permission_error", "merge_conflict"],
    },
}
_DEFAULT_VERIFICATION_POLICY = {
    "gates": [
        "fast_gate",
        "repo_gate",
        "harness_gate",
    ],
    "commands": [
        "uv run pytest -q",
        "uv run ruff check src tests scripts",
        {
            "command": (
                "cd frontend/terminal && "
                "([ -x ./node_modules/.bin/tsc ] || npm ci --no-audit --no-fund) && "
                "./node_modules/.bin/tsc --noEmit"
            ),
            "shell": True,
        },
    ],
    "require_tests_before_merge": True,
}
_DEFAULT_RELEASE_POLICY = {
    "merge_requires_human": True,
    "release_requires_human": True,
    "auto_revert_on_failed_verification": False,
}


def _shorten(text: str, *, limit: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    return str(value)


_SHELL_METACHARS = frozenset(";&|`$<>\n\r")


@dataclass(frozen=True)
class _VerificationCommand:
    """Parsed verification-policy entry.

    When ``shell`` is false, ``argv`` is executed with ``shell=False``.
    When ``shell`` is true, ``raw`` is handed to the shell (explicit opt-in).
    ``error`` signals a policy entry that must not be executed; callers emit
    an error step so the verification gate fails loudly.
    """

    raw: str
    argv: tuple[str, ...]
    shell: bool
    error: str | None = None


def _parse_verification_entry(entry: object) -> _VerificationCommand:
    if isinstance(entry, dict):
        raw = str(entry.get("command", "")).strip()
        if not raw:
            return _VerificationCommand(raw=str(entry), argv=(), shell=False, error="empty command")
        if bool(entry.get("shell", False)):
            return _VerificationCommand(raw=raw, argv=(), shell=True)
        # fall through and validate as an argv-form command
    elif isinstance(entry, str):
        raw = entry.strip()
        if not raw:
            return _VerificationCommand(raw=entry, argv=(), shell=False, error="empty command")
    else:
        return _VerificationCommand(
            raw=str(entry),
            argv=(),
            shell=False,
            error="entry must be a string or a mapping with a 'command' key",
        )

    if any(ch in _SHELL_METACHARS for ch in raw):
        return _VerificationCommand(
            raw=raw,
            argv=(),
            shell=False,
            error=(
                "command contains shell metacharacters; use the mapping form "
                "{command: '...', shell: true} in verification_policy.yaml to opt in"
            ),
        )
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        return _VerificationCommand(
            raw=raw,
            argv=(),
            shell=False,
            error=f"could not tokenize command: {exc}",
        )
    if not argv:
        return _VerificationCommand(raw=raw, argv=(), shell=False, error="empty command")
    return _VerificationCommand(raw=raw, argv=tuple(argv), shell=False)


def _looks_available(command: str, cwd: Path) -> bool:
    lowered = command.lower()
    if lowered.startswith("uv "):
        return (cwd / "pyproject.toml").exists()
    if "ruff check" in lowered:
        return (cwd / "pyproject.toml").exists()
    if "pytest" in lowered:
        return (cwd / "tests").exists()
    if "tsc" in lowered or "frontend/terminal" in lowered:
        return (cwd / "frontend" / "terminal" / "package.json").exists()
    return True


def _source_ref_number(source_ref: str, prefix: str) -> int | None:
    normalized = source_ref.strip()
    if not normalized.startswith(f"{prefix}:"):
        return None
    try:
        return int(normalized.split(":", 1)[1])
    except ValueError:
        return None


def _bilingual_lines(zh: str, en: str) -> str:
    return f"{zh}\n{en}".strip()


class RepoAutopilotStore:
    """Persist and query project-level autopilot state."""

    def __init__(self, cwd: str | Path) -> None:
        self._cwd = Path(cwd).resolve()
        self._registry_path = get_project_autopilot_registry_path(self._cwd)
        self._journal_path = get_project_repo_journal_path(self._cwd)
        self._context_path = get_project_active_repo_context_path(self._cwd)
        self._runs_dir = get_project_autopilot_runs_dir(self._cwd)
        self._ensure_layout()

    @property
    def registry_path(self) -> Path:
        return self._registry_path

    @property
    def journal_path(self) -> Path:
        return self._journal_path

    @property
    def context_path(self) -> Path:
        return self._context_path

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def list_cards(self, *, status: RepoTaskStatus | None = None) -> list[RepoTaskCard]:
        cards = self._load_registry().cards
        if status is not None:
            cards = [card for card in cards if card.status == status]
        return sorted(cards, key=lambda card: (-card.score, -card.updated_at, card.title.lower()))

    def get_card(self, card_id: str) -> RepoTaskCard | None:
        for card in self._load_registry().cards:
            if card.id == card_id:
                return card
        return None

    def enqueue_card(
        self,
        *,
        source_kind: RepoTaskSource,
        title: str,
        body: str = "",
        source_ref: str = "",
        labels: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[RepoTaskCard, bool]:
        registry = self._load_registry()
        now = time.time()
        normalized_title = title.strip()
        normalized_body = body.strip()
        normalized_ref = source_ref.strip()
        fingerprint = self._build_fingerprint(
            source_kind=source_kind,
            source_ref=normalized_ref,
            title=normalized_title,
            body=normalized_body,
        )
        existing = next((card for card in registry.cards if card.fingerprint == fingerprint), None)
        merged_labels = self._normalize_labels(labels)
        merged_metadata = dict(metadata or {})
        if existing is not None:
            if normalized_title:
                existing.title = normalized_title
            if normalized_body:
                existing.body = normalized_body
            if normalized_ref:
                existing.source_ref = normalized_ref
            existing.labels = self._merge_labels(existing.labels, merged_labels)
            existing.metadata.update(merged_metadata)
            existing.updated_at = now
            existing.score, existing.score_reasons = self._score_card(existing)
            self._save_registry(registry)
            self.append_journal(
                kind="intake_refresh",
                summary=f"Refreshed intake card {existing.id}: {existing.title}",
                task_id=existing.id,
                metadata={"source_kind": existing.source_kind, "source_ref": existing.source_ref},
            )
            self.rebuild_active_context()
            return existing, False

        card = RepoTaskCard(
            id=f"ap-{uuid4().hex[:8]}",
            fingerprint=fingerprint,
            title=normalized_title or "Untitled intake item",
            body=normalized_body,
            source_kind=source_kind,
            source_ref=normalized_ref,
            labels=merged_labels,
            metadata=merged_metadata,
            created_at=now,
            updated_at=now,
        )
        card.score, card.score_reasons = self._score_card(card)
        registry.cards.append(card)
        self._save_registry(registry)
        self.append_journal(
            kind="intake_added",
            summary=f"Queued {card.source_kind}: {card.title}",
            task_id=card.id,
            metadata={"source_ref": card.source_ref, "score": card.score},
        )
        self.rebuild_active_context()
        return card, True

    def pick_next_card(self) -> RepoTaskCard | None:
        queued = [card for card in self._load_registry().cards if card.status == "queued"]
        if not queued:
            return None
        return sorted(queued, key=lambda card: (-card.score, -card.updated_at, card.title.lower()))[0]

    def update_status(
        self,
        card_id: str,
        *,
        status: RepoTaskStatus,
        note: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> RepoTaskCard:
        registry = self._load_registry()
        card = next((item for item in registry.cards if item.id == card_id), None)
        if card is None:
            raise ValueError(f"No autopilot card found with ID: {card_id}")
        card.status = status
        card.updated_at = time.time()
        if note:
            card.metadata["last_note"] = note.strip()
        if metadata_updates:
            card.metadata.update(metadata_updates)
        card.score, card.score_reasons = self._score_card(card)
        self._save_registry(registry)
        summary = f"{status}: {card.title}"
        if note:
            summary = f"{summary} ({_shorten(note, limit=80)})"
        self.append_journal(kind=f"status_{status}", summary=summary, task_id=card.id)
        self.rebuild_active_context()
        return card

    def load_journal(self, *, limit: int = 12) -> list[RepoJournalEntry]:
        if not self._journal_path.exists():
            return []
        entries: list[RepoJournalEntry] = []
        for line in self._journal_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(RepoJournalEntry.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue
        return entries[-limit:]

    def append_journal(
        self,
        *,
        kind: str,
        summary: str,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RepoJournalEntry:
        entry = RepoJournalEntry(
            timestamp=time.time(),
            kind=kind,
            summary=summary.strip(),
            task_id=task_id,
            metadata=metadata or {},
        )
        with self._journal_path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
        return entry

    def load_active_context(self) -> str:
        if not self._context_path.exists():
            return ""
        return self._context_path.read_text(encoding="utf-8", errors="replace").strip()

    def rebuild_active_context(self) -> str:
        cards = self._load_registry().cards
        running = [card for card in cards if card.status in {"preparing", "running", "verifying", "waiting_ci", "repairing"}]
        accepted = [card for card in cards if card.status in {"accepted", "pr_open"}]
        queued = [card for card in cards if card.status == "queued"]
        completed = [card for card in cards if card.status in {"completed", "merged"}]
        failed = [card for card in cards if card.status in {"failed", "rejected"}]
        focus = None
        for group in (running, accepted, queued):
            if group:
                focus = sorted(
                    group,
                    key=lambda card: (-card.score, -card.updated_at, card.title.lower()),
                )[0]
                break

        lines = [
            "# Active Repo Context",
            "",
            f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
            "",
            "## Current Task Focus",
        ]
        if focus is None:
            lines.append("- No active repo task focus yet.")
        else:
            lines.append(
                f"- [{focus.status}] {focus.title} ({focus.source_kind}, score={focus.score})"
            )
            if focus.body:
                lines.append(f"- Detail: {_shorten(focus.body, limit=220)}")

        lines.extend(["", "## In Progress"])
        for card in sorted(running + accepted, key=lambda item: (-item.score, -item.updated_at))[:6]:
            lines.append(f"- [{card.status}] {card.id} {card.title} ({card.source_kind})")
        if not running and not accepted:
            lines.append("- None.")

        lines.extend(["", "## Next Up"])
        for card in sorted(queued, key=lambda item: (-item.score, -item.updated_at))[:8]:
            lines.append(f"- [{card.score}] {card.id} {card.title} ({card.source_kind})")
        if not queued:
            lines.append("- No queued items.")

        lines.extend(["", "## Recently Completed"])
        for card in sorted(completed, key=lambda item: item.updated_at, reverse=True)[:5]:
            lines.append(f"- {card.id} {card.title}")
        if not completed:
            lines.append("- None yet.")

        lines.extend(["", "## Recent Failures"])
        for card in sorted(failed, key=lambda item: item.updated_at, reverse=True)[:5]:
            lines.append(f"- [{card.status}] {card.id} {card.title}")
        if not failed:
            lines.append("- None.")

        lines.extend(["", "## Recent Repo Journal"])
        journal = self.load_journal(limit=8)
        if journal:
            for entry in journal:
                lines.append(
                    f"- {time.strftime('%m-%d %H:%M', time.gmtime(entry.timestamp))} "
                    f"{entry.kind}: {entry.summary}"
                )
        else:
            lines.append("- Journal is empty.")

        lines.extend(
            [
                "",
                "## Policies",
                f"- Autopilot: {get_project_autopilot_policy_path(self._cwd)}",
                f"- Verification: {get_project_verification_policy_path(self._cwd)}",
                f"- Release: {get_project_release_policy_path(self._cwd)}",
            ]
        )
        content = "\n".join(lines).strip() + "\n"
        atomic_write_text(self._context_path, content)
        self.export_dashboard()
        return content

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for card in self._load_registry().cards:
            counts[card.status] = counts.get(card.status, 0) + 1
        return counts

    def load_policies(self) -> dict[str, Any]:
        return {
            "autopilot": self._read_yaml(get_project_autopilot_policy_path(self._cwd), _DEFAULT_AUTOPILOT_POLICY),
            "verification": self._read_yaml(
                get_project_verification_policy_path(self._cwd),
                _DEFAULT_VERIFICATION_POLICY,
            ),
            "release": self._read_yaml(get_project_release_policy_path(self._cwd), _DEFAULT_RELEASE_POLICY),
        }

    def scan_github_issues(self, *, limit: int = 10) -> list[RepoTaskCard]:
        raw = self._run_gh_json(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,body,labels,updatedAt,url",
            ]
        )
        cards: list[RepoTaskCard] = []
        for item in raw:
            number = item.get("number")
            if number is None:
                continue
            labels = [str(label.get("name", "")).strip() for label in item.get("labels", [])]
            card, _ = self.enqueue_card(
                source_kind="github_issue",
                source_ref=f"issue:{number}",
                title=f"GitHub issue #{number}: {_safe_text(item.get('title'))}",
                body=_safe_text(item.get("body")),
                labels=[label for label in labels if label],
                metadata={
                    "url": _safe_text(item.get("url")),
                    "updated_at_remote": _safe_text(item.get("updatedAt")),
                },
            )
            cards.append(card)
        return cards

    def scan_github_prs(self, *, limit: int = 10) -> list[RepoTaskCard]:
        raw = self._run_gh_json(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,body,isDraft,reviewDecision,mergeStateStatus,updatedAt,url,labels,headRefName,baseRefName",
            ]
        )
        cards: list[RepoTaskCard] = []
        for item in raw:
            number = item.get("number")
            if number is None:
                continue
            labels = [str(label.get("name", "")).strip() for label in item.get("labels", [])]
            card, _ = self.enqueue_card(
                source_kind="github_pr",
                source_ref=f"pr:{number}",
                title=f"GitHub PR #{number}: {_safe_text(item.get('title'))}",
                body=_safe_text(item.get("body")),
                labels=[label for label in labels if label],
                metadata={
                    "url": _safe_text(item.get("url")),
                    "updated_at_remote": _safe_text(item.get("updatedAt")),
                    "is_draft": bool(item.get("isDraft")),
                    "review_decision": _safe_text(item.get("reviewDecision")),
                    "merge_state_status": _safe_text(item.get("mergeStateStatus")),
                    "head_ref_name": _safe_text(item.get("headRefName")),
                    "base_ref_name": _safe_text(item.get("baseRefName")),
                },
            )
            cards.append(card)
        return cards

    def scan_claude_code_candidates(
        self,
        *,
        limit: int = 10,
        root: str | Path | None = None,
    ) -> list[RepoTaskCard]:
        candidate_root = Path(root or Path.home() / "claude-code").expanduser().resolve()
        if not candidate_root.exists():
            raise ValueError(f"claude-code root not found: {candidate_root}")
        discovered: list[tuple[str, Path]] = []
        for dirname, label in (("commands", "command"), ("agents", "agent")):
            base = candidate_root / dirname
            if not base.exists():
                continue
            for path in sorted(base.iterdir(), key=lambda item: item.name.lower()):
                if path.name.startswith("."):
                    continue
                discovered.append((label, path))
        cards: list[RepoTaskCard] = []
        for label, path in discovered[:limit]:
            name = path.stem if path.is_file() else path.name
            card, _ = self.enqueue_card(
                source_kind="claude_code_candidate",
                source_ref=f"{label}:{path}",
                title=f"Evaluate claude-code {label}: {name}",
                body=(
                    f"Borrow candidate from {path}. "
                    "Review whether this should be aligned, adapted, or ignored for OpenHarness."
                ),
                metadata={"path": str(path)},
            )
            cards.append(card)
        return cards

    def scan_all_sources(self, *, issue_limit: int = 10, pr_limit: int = 10) -> dict[str, int]:
        counts = {"github_issue": 0, "github_pr": 0, "claude_code_candidate": 0}
        try:
            counts["github_issue"] = len(self.scan_github_issues(limit=issue_limit))
        except Exception as exc:
            self.append_journal(kind="scan_warning", summary=f"GitHub issue scan failed: {exc}")
        try:
            counts["github_pr"] = len(self.scan_github_prs(limit=pr_limit))
        except Exception as exc:
            self.append_journal(kind="scan_warning", summary=f"GitHub PR scan failed: {exc}")
        try:
            counts["claude_code_candidate"] = len(self.scan_claude_code_candidates(limit=8))
        except Exception as exc:
            self.append_journal(kind="scan_warning", summary=f"claude-code scan failed: {exc}")
        self.append_journal(kind="scan_all", summary=f"Scanned sources: {counts}")
        self.rebuild_active_context()
        return counts

    async def run_next(
        self,
        *,
        model: str | None = None,
        max_turns: int | None = None,
        permission_mode: str | None = None,
    ) -> RepoRunResult:
        card = self.pick_next_card()
        if card is None:
            raise ValueError("No queued autopilot cards.")
        return await self.run_card(
            card.id,
            model=model,
            max_turns=max_turns,
            permission_mode=permission_mode,
        )

    async def run_card(
        self,
        card_id: str,
        *,
        model: str | None = None,
        max_turns: int | None = None,
        permission_mode: str | None = None,
    ) -> RepoRunResult:
        card = self.get_card(card_id)
        if card is None:
            raise ValueError(f"No autopilot card found with ID: {card_id}")
        # todo @Toby注释: 状态机互斥保护：一个 card 同时只能在一个状态流转，
        # 防止用户重复触发或并发执行导致 worktree/git 状态混乱
        if card.status in {"preparing", "running", "verifying", "waiting_ci", "repairing"}:
            raise ValueError(f"Autopilot card {card.id} is already active.")

        # todo @Toby注释: [策略加载] 从项目根目录读取两个 YAML 配置文件：
        #   - .openharness/autopilot_policy.yaml: 控制 Agent 行为（max_turns, permission_mode）
        #   - .openharness/verification_policy.yaml: 控制验证命令（pytest, ruff, mypy）
        # 策略与代码分离，不同项目可配置不同的验证标准
        policies = self.load_policies()
        execution = dict(policies.get("autopilot", {}).get("execution", {}))
        effective_model = model or _safe_text(execution.get("default_model")) or None
        effective_max_turns = max_turns if max_turns is not None else int(execution.get("max_turns", 12))
        effective_permission_mode = permission_mode or _safe_text(
            execution.get("permission_mode", "full_auto")
        )
        max_attempts = self._max_attempts(policies)
        base_branch = self._base_branch(policies)
        head_branch = self._head_branch(card, policies)
        issue_number = self._issue_number_for_card(card)
        linked_pr_number = self._linked_pr_number(card)
        use_worktree = bool(execution.get("use_worktree", True)) and self._is_git_repo(self._cwd)

        # todo @Toby注释: [GitHub PR 关联] 如果 card 来自 github_pr 且已关联现有 PR，
        # 不走完整执行流，直接进入 CI 监控模式（复用已有 PR 的代码）
        if card.source_kind == "github_pr" and linked_pr_number is not None and not card.metadata.get("autopilot_managed"):
            return await self._process_existing_pr_card(card, linked_pr_number, policies)

        # todo @Toby注释: [环境隔离] 创建 git worktree，在隔离分支上执行所有代码修改。
        # 好处：
        #   1. 失败时不污染主分支（worktree 可直接删除）
        #   2. 多任务并行时互不干扰（每个 card 一个 worktree）
        #   3. 天然支持 git diff 检查变更量
        worktree_manager = WorktreeManager()
        worktree_info = None
        working_cwd = self._cwd
        if use_worktree:
            worktree_info = await worktree_manager.create_worktree(
                self._cwd,
                self._worktree_slug(card),
                branch=head_branch,
            )
            working_cwd = worktree_info.path
        existing_attempts = int(card.metadata.get("attempt_count", 0) or 0)
        self.update_status(
            card.id,
            status="preparing",
            note="preparing isolated worktree" if use_worktree else "preparing local execution",
            metadata_updates={
                "run_started_at": time.time(),
                "execution_model": effective_model or "",
                "max_attempts": max_attempts,
                "worktree_slug": self._worktree_slug(card),
                "worktree_path": str(working_cwd),
                "head_branch": head_branch,
                "base_branch": base_branch,
                "linked_issue_numbers": [issue_number] if issue_number is not None else [],
                "linked_pr_number": linked_pr_number,
            },
        )

        if issue_number is not None and existing_attempts == 0:
            self._comment_on_issue(issue_number, self._comment_started(card, existing_attempts + 1))

        current_run_report = self._runs_dir / f"{card.id}-run.md"
        current_verification_report = self._runs_dir / f"{card.id}-verification.md"
        prior_summary = _safe_text(card.metadata.get("assistant_summary_preview"))
        prior_failure_stage = _safe_text(card.metadata.get("last_failure_stage"))
        prior_failure_summary = _safe_text(card.metadata.get("last_failure_summary"))

        # todo @Toby注释: [核心循环] 执行-验证-修复闭环。最多 max_attempts 轮（默认3轮）。
        # 设计思想：不是"一次对话就交付"，而是"产出代码 → 跑验证 → 不通过就修复"的迭代。
        # 每一轮都是完整的 Agent 调用 + 验证，不是对话轮次的追加。
        for attempt_count in range(existing_attempts + 1, max_attempts + 1):
            attempt_run_report = self._runs_dir / f"{card.id}-attempt-{attempt_count:02d}-run.md"
            attempt_verification_report = self._runs_dir / f"{card.id}-attempt-{attempt_count:02d}-verification.md"
            is_first_attempt = attempt_count == 1 and existing_attempts == 0
            if use_worktree:
                try:
                    # todo @Toby注释: [worktree 同步] 第一轮用 reset（从 base 全新开始），
                    # 后续轮次保留当前分支状态（Agent 可能已有部分修改，在此基础上修复）
                    self._sync_worktree_to_base(
                        working_cwd,
                        base_branch=base_branch,
                        head_branch=head_branch,
                        reset=is_first_attempt,
                    )
                except Exception as exc:
                    summary = f"Failed to prepare worktree branch: {exc}"
                    self.update_status(
                        card.id,
                        status="failed",
                        note=summary,
                        metadata_updates={"last_failure_stage": "git_prepare_failed", "last_failure_summary": summary},
                    )
                    self.append_journal(kind="run_failed", summary=summary, task_id=card.id)
                    return RepoRunResult(
                        card_id=card.id,
                        status="failed",
                        run_report_path=str(current_run_report),
                        verification_report_path=str(current_verification_report),
                        attempt_count=attempt_count,
                        worktree_path=str(working_cwd),
                    )

            # todo @Toby注释: [状态切换] 第1轮是 running，第2轮起是 repairing。
            # 状态差异影响 prompt 组装（_prepare_repair_prompt 会附加失败上下文）
            self.update_status(
                card.id,
                status="repairing" if attempt_count > 1 else "running",
                note="repairing failed run" if attempt_count > 1 else "autopilot execution started",
                metadata_updates={"attempt_count": attempt_count},
            )
            # todo @Toby注释: [Prompt 组装] 核心：如何把"任务需求 + 历史失败信息"告诉 Agent。
            # 第1轮：正常任务描述 + autopilot policy + verification policy
            # 第2轮+：额外附加 "上一轮 pytest 失败，2 tests failed，请修复"
            # 这是验证结果反馈给 Agent 的关键通道
            prompt = self._prepare_repair_prompt(
                card,
                policies,
                attempt_count=attempt_count,
                prior_summary=prior_summary,
                failure_stage=prior_failure_stage,
                failure_summary=prior_failure_summary,
            )
            try:
                # todo @Toby注释: [Agent 执行] _run_agent_prompt 是 Engine 层的入口。
                # 内部走：build_runtime → start_runtime → submit_message → run_query (LLM 循环)。
                # permission_mode="full_auto" 表示工具调用无需人工确认（因为已在隔离 worktree 中）。
                # 返回值是 Agent 的文本摘要（如 "I fixed auth.py by..."），不是结构化数据。
                assistant_summary = await self._run_agent_prompt(
                    prompt,
                    model=effective_model,
                    max_turns=effective_max_turns,
                    permission_mode=effective_permission_mode,
                    cwd=working_cwd,
                )
            except Exception as exc:
                # todo @Toby注释: [Agent 异常] Agent 执行过程中抛异常（如模型 API 失败、工具执行崩溃）。
                # 写入失败报告，标记 card 为 failed，不再重试（这是基础设施问题，不是代码质量问题）。
                failure_text = self._render_run_report(
                    card,
                    agent_summary=f"Autopilot execution failed: {exc}",
                    verification_steps=[],
                    verification_status="not_started",
                )
                for path in (attempt_run_report, current_run_report):
                    atomic_write_text(path, failure_text)
                summary = f"agent execution failed: {exc}"
                self.update_status(
                    card.id,
                    status="failed",
                    note=summary,
                    metadata_updates={
                        "execution_error": str(exc),
                        "last_failure_stage": "agent_runtime_error",
                        "last_failure_summary": summary,
                    },
                )
                self.append_journal(
                    kind="run_failed",
                    summary=f"{card.title}: agent execution failed",
                    task_id=card.id,
                    metadata={"error": str(exc), "attempt_count": attempt_count},
                )
                if issue_number is not None:
                    self._comment_on_issue(issue_number, self._comment_terminal_failure(summary))
                return RepoRunResult(
                    card_id=card.id,
                    status="failed",
                    assistant_summary=failure_text.strip(),
                    run_report_path=str(current_run_report),
                    verification_report_path=str(current_verification_report),
                    verification_steps=[],
                    attempt_count=attempt_count,
                    worktree_path=str(working_cwd),
                )

            # todo @Toby注释: [运行报告] 将 Agent 的文本回复写入 Markdown 报告。
            # 这是给人看的审计日志，不是给机器解析的。报告路径后续会附加到 PR 描述中。
            pending_report = self._render_run_report(
                card,
                agent_summary=assistant_summary,
                verification_steps=[],
                verification_status="pending",
            )
            for path in (attempt_run_report, current_run_report):
                atomic_write_text(path, pending_report)
            self.append_journal(
                kind="run_finished",
                summary=f"Agent run finished for {card.title}",
                task_id=card.id,
                metadata={"run_report_path": str(attempt_run_report), "attempt_count": attempt_count},
            )

            # todo @Toby注释: [状态切换: verifying] 从 running 切换到 verifying，
            # 明确区分"Agent 正在写代码"和"正在检查代码质量"两个阶段。
            # 这个状态会显示在 /autopilot status 中，让用户知道当前在跑验证。
            self.update_status(
                card.id,
                status="verifying",
                note="running verification gates",
                metadata_updates={"assistant_summary_preview": _shorten(assistant_summary, limit=300)},
            )
            # todo @Toby注释: ★ [验证执行] 核心验证逻辑。读取 verification_policy.yaml 中的 commands 列表，
            # 在 worktree 中逐个执行。返回值是 RepoVerificationStep 列表，每个 step 包含：
            #   - command: 执行的命令字符串
            #   - returncode: 进程返回码（0=成功，非0=失败）
            #   - status: "success" | "failed" | "error"
            #   - stdout/stderr: 输出内容（截断到4000字符，防止过大）
            # 验证的是 Agent 产出的**代码文件**，不是 Agent 的文本回复。
            verification_steps = self._run_verification_steps(policies, cwd=working_cwd)
            # todo @Toby注释: [验证报告] 将验证结果写入 Markdown，格式类似 CI 报告，
            # 每条命令一行，显示 ✓/✗ 和输出摘要。
            verification_text = self._render_verification_report(card, verification_steps)
            for path in (attempt_verification_report, current_verification_report):
                atomic_write_text(path, verification_text)

            # todo @Toby注释: [失败判定] 过滤出 status 为 failed 或 error 的步骤。
            # 只要有一条命令失败，整轮验证就视为失败。
            failing = [step for step in verification_steps if step.status in {"failed", "error"}]
            # todo @Toby注释: [完整报告渲染] 将 Agent 摘要 + 验证结果合并为最终报告，
            # verification_status="failed" 或 "passed" 会显示在报告头部。
            final_local_report = self._render_run_report(
                card,
                agent_summary=assistant_summary,
                verification_steps=verification_steps,
                verification_status="failed" if failing else "passed",
            )
            for path in (attempt_run_report, current_run_report):
                atomic_write_text(path, final_local_report)
            prior_summary = assistant_summary  # 保存给下一轮作为上下文

            if failing:
                # todo @Toby注释: [失败摘要] 取前3条失败命令，格式: "pytest -q rc=1; mypy rc=2"
                # 这个摘要会被写入 metadata，也会出现在 _prepare_repair_prompt 的修复上下文中。
                summary = "; ".join(f"{step.command} rc={step.returncode}" for step in failing[:3])
                metadata_updates = {
                    "verification_failed": True,
                    "verification_steps": [step.model_dump(mode="json") for step in verification_steps],
                    "last_failure_stage": "local_verification_failed",
                    "last_failure_summary": summary,
                }
                # todo @Toby注释: [修复决策] 关键分支：
                #   - 还有重试次数 → 状态切为 repairing，记录失败原因，continue 回到 for 循环顶部
                #   - 次数用完 → 状态切为 failed，返回失败结果
                # continue 时，下一轮 _prepare_repair_prompt 会把 failure_summary 塞进 prompt
                if attempt_count < max_attempts:
                    self.update_status(
                        card.id,
                        status="repairing",
                        note="local verification failed; retrying",
                        metadata_updates=metadata_updates,
                    )
                    self.append_journal(
                        kind="verification_failed",
                        summary=f"{card.title}: local verification failed, retrying",
                        task_id=card.id,
                        metadata={"attempt_count": attempt_count},
                    )
                    if issue_number is not None:
                        self._comment_on_issue(issue_number, self._comment_local_failed(attempt_count, summary))
                    # todo @Toby注释: [保存失败上下文] 这两个变量会被下一轮 _prepare_repair_prompt 读取，
                    # 生成包含失败信息的修复 prompt。这是"验证驱动修复"的核心闭环。
                    prior_failure_stage = "local_verification_failed"
                    prior_failure_summary = summary
                    continue

                self.update_status(
                    card.id,
                    status="failed",
                    note=f"{len(failing)} verification gate(s) failed",
                    metadata_updates=metadata_updates,
                )
                self.append_journal(
                    kind="verification_failed",
                    summary=f"{card.title}: {len(failing)} verification gate(s) failed",
                    task_id=card.id,
                )
                if issue_number is not None:
                    self._comment_on_issue(issue_number, self._comment_terminal_failure(summary))
                return RepoRunResult(
                    card_id=card.id,
                    status="failed",
                    assistant_summary=assistant_summary,
                    run_report_path=str(current_run_report),
                    verification_report_path=str(current_verification_report),
                    verification_steps=verification_steps,
                    attempt_count=attempt_count,
                    worktree_path=str(working_cwd),
                )

            # todo @Toby注释: [git 检查] 如果不是 git 仓库（如纯本地项目），验证通过后直接 completed，
            # 跳过 GitHub 自动化流程。human_gate_pending=True 提示需要人工手动合并。
            if not self._is_git_repo(working_cwd):
                self.update_status(
                    card.id,
                    status="completed",
                    note="local verification passed; repository is not a git repo so GitHub automation was skipped",
                    metadata_updates={
                        "verification_failed": False,
                        "verification_steps": [step.model_dump(mode="json") for step in verification_steps],
                        "human_gate_pending": True,
                    },
                )
                return RepoRunResult(
                    card_id=card.id,
                    status="completed",
                    assistant_summary=assistant_summary,
                    run_report_path=str(current_run_report),
                    verification_report_path=str(current_verification_report),
                    verification_steps=verification_steps,
                    attempt_count=attempt_count,
                    worktree_path=str(working_cwd),
                )

            # todo @Toby注释: [代码提交] 将 worktree 中所有变更自动提交。
            # commit message 格式: "autopilot(task-001): fix login bug"
            commit_created = self._git_commit_all(
                working_cwd,
                f"autopilot({card.id}): {card.title}",
            )
            # todo @Toby注释: [变更检测] 两种情况视为"有进度"：
            #   1. 本次 commit 有文件变更（commit_created=True）
            #   2. 分支本身已有历史变更（可能是上一轮留下的）
            # 如果 Agent 什么都没改（连空 commit 都没有），视为无效执行，需要重试。
            branch_has_progress = commit_created or self._git_branch_has_progress(
                working_cwd,
                base_branch=base_branch,
            )
            if not branch_has_progress:
                # todo @Toby注释: [第3层验证：变更验证] 检查 Agent 是否实际修改了代码文件。
                # 只说话不改代码 → no_changes → 同样进入 repairing 重试。
                # 这是"验证结果是符合预期的"的补充：有 lint 通过还不够，必须有实际变更。
                no_changes_summary = "Agent produced no code changes to commit."
                if attempt_count < max_attempts:
                    # todo @Toby注释: 没有代码变更也走 repairing 重试，但 failure_stage="no_changes"，
                    # prompt 会提示 Agent "请确保实际修改代码文件"
                    self.update_status(
                        card.id,
                        status="repairing",
                        note="agent produced no changes; retrying",
                        metadata_updates={
                            "last_failure_stage": "no_changes",
                            "last_failure_summary": no_changes_summary,
                        },
                    )
                    prior_failure_stage = "no_changes"
                    prior_failure_summary = no_changes_summary
                    continue
                self.update_status(
                    card.id,
                    status="failed",
                    note=no_changes_summary,
                    metadata_updates={
                        "last_failure_stage": "no_changes",
                        "last_failure_summary": no_changes_summary,
                    },
                )
                return RepoRunResult(
                    card_id=card.id,
                    status="failed",
                    assistant_summary=assistant_summary,
                    run_report_path=str(current_run_report),
                    verification_report_path=str(current_verification_report),
                    verification_steps=verification_steps,
                    attempt_count=attempt_count,
                    worktree_path=str(working_cwd),
                )
            if not commit_created:
                self.append_journal(
                    kind="existing_progress_detected",
                    summary=f"{card.title}: reusing existing local branch progress",
                    task_id=card.id,
                    metadata={"attempt_count": attempt_count, "head_branch": head_branch},
                )

            # todo @Toby注释: [推送与 PR] push 分支到远程，创建或更新 PR。
            # PR 描述会自动附加 run_report 和 verification_report 的链接。
            try:
                self._git_push_branch(working_cwd, head_branch)
                pr_info = self._upsert_pull_request(
                    card,
                    head_branch=head_branch,
                    base_branch=base_branch,
                    run_report_path=current_run_report,
                    verification_report_path=current_verification_report,
                )
            except Exception as exc:
                summary = f"Failed to push branch or upsert PR: {exc}"
                self.update_status(
                    card.id,
                    status="failed",
                    note=summary,
                    metadata_updates={"last_failure_stage": "github_pr_open_failed", "last_failure_summary": summary},
                )
                if issue_number is not None:
                    self._comment_on_issue(issue_number, self._comment_terminal_failure(summary))
                return RepoRunResult(
                    card_id=card.id,
                    status="failed",
                    assistant_summary=assistant_summary,
                    run_report_path=str(current_run_report),
                    verification_report_path=str(current_verification_report),
                    verification_steps=verification_steps,
                    attempt_count=attempt_count,
                    worktree_path=str(worktree_info.path),
                )

            linked_pr_number = int(pr_info.get("number"))
            pr_url = _safe_text(pr_info.get("url"))
            # todo @Toby注释: [远程 CI 等待] 状态切为 waiting_ci，记录 PR 信息到 metadata。
            # 这是第4层验证：本地验证通过不代表远程 CI 通过（环境差异、依赖版本等）。
            self.update_status(
                card.id,
                status="waiting_ci",
                note=f"waiting for remote CI on PR #{linked_pr_number}",
                metadata_updates={
                    "linked_pr_number": linked_pr_number,
                    "linked_pr_url": pr_url,
                    "linked_issue_numbers": [issue_number] if issue_number is not None else [],
                    "autopilot_managed": True,
                    "verification_failed": False,
                    "verification_steps": [step.model_dump(mode="json") for step in verification_steps],
                },
            )
            self._comment_on_pr(linked_pr_number, self._comment_pr_opened(linked_pr_number, pr_url))

            # todo @Toby注释: [CI 轮询] 轮询间隔 20s（ci_poll_interval_seconds），最长 30分钟（ci_timeout_seconds）。
            # 返回 ci_state ∈ {"passed", "failed", "pending", "no_checks"}
            ci_state, ci_summary, pr_snapshot, checks = await self._wait_for_pr_ci(linked_pr_number, policies)
            self.update_status(
                card.id,
                status="waiting_ci" if ci_state == "pending" else "waiting_ci",
                note=f"remote CI status: {ci_state}",
                metadata_updates={
                    "last_ci_conclusion": ci_state,
                    "last_ci_summary": ci_summary,
                    "last_ci_checks": checks,
                    "linked_pr_number": linked_pr_number,
                    "linked_pr_url": _safe_text(pr_snapshot.get("url")) or pr_url,
                },
            )
            if ci_state == "failed":
                # todo @Toby注释: [CI 失败也重试] 远程 CI 失败（如测试在 Linux CI 上挂了但在本地通过了），
                # 同样进入 repairing 循环。failure_stage="remote_ci_failed" 会让 Agent 知道是 CI 环境问题。
                if attempt_count < max_attempts:
                    self.update_status(
                        card.id,
                        status="repairing",
                        note="remote CI failed; retrying",
                        metadata_updates={
                            "last_failure_stage": "remote_ci_failed",
                            "last_failure_summary": ci_summary,
                        },
                    )
                    self.append_journal(
                        kind="ci_failed_retry",
                        summary=f"{card.title}: remote CI failed, retrying",
                        task_id=card.id,
                        metadata={"pr_number": linked_pr_number, "attempt_count": attempt_count},
                    )
                    self._comment_on_pr(linked_pr_number, self._comment_ci_failed(attempt_count, ci_summary))
                    prior_failure_stage = "remote_ci_failed"
                    prior_failure_summary = ci_summary
                    continue

                self.update_status(
                    card.id,
                    status="failed",
                    note=f"remote CI failed: {ci_summary}",
                    metadata_updates={
                        "last_failure_stage": "remote_ci_failed",
                        "last_failure_summary": ci_summary,
                    },
                )
                self._comment_on_pr(linked_pr_number, self._comment_terminal_failure(ci_summary))
                if issue_number is not None:
                    self._comment_on_issue(issue_number, self._comment_terminal_failure(ci_summary))
                return RepoRunResult(
                    card_id=card.id,
                    status="failed",
                    assistant_summary=assistant_summary,
                    run_report_path=str(current_run_report),
                    verification_report_path=str(current_verification_report),
                    verification_steps=verification_steps,
                    attempt_count=attempt_count,
                    worktree_path=str(working_cwd),
                    pr_number=linked_pr_number,
                    pr_url=pr_url,
                )

            # todo @Toby注释: [合并决策] CI 通过后检查是否满足 auto-merge 条件：
            #   - PR 必须有 "autopilot:merge" label（label_gated 模式）
            #   - 或配置为无条件自动合并（mode=always）
            # 满足则自动 merge，不满足则标记 completed 等待人工审批（human gate）
            if self._automerge_eligible(pr_snapshot, policies):
                self._merge_pull_request(linked_pr_number)
                self.update_status(
                    card.id,
                    status="merged",
                    note=f"PR #{linked_pr_number} merged automatically",
                    metadata_updates={"human_gate_pending": False},
                )
                self.append_journal(
                    kind="merged",
                    summary=f"{card.title}: PR #{linked_pr_number} merged",
                    task_id=card.id,
                    metadata={"pr_number": linked_pr_number},
                )
                self._comment_on_pr(linked_pr_number, self._comment_merged(linked_pr_number))
                if issue_number is not None:
                    self._comment_on_issue(issue_number, self._comment_merged(linked_pr_number))
                if use_worktree:
                    await worktree_manager.remove_worktree(self._worktree_slug(card))
                return RepoRunResult(
                    card_id=card.id,
                    status="merged",
                    assistant_summary=assistant_summary,
                    run_report_path=str(current_run_report),
                    verification_report_path=str(current_verification_report),
                    verification_steps=verification_steps,
                    attempt_count=attempt_count,
                    worktree_path=str(working_cwd),
                    pr_number=linked_pr_number,
                    pr_url=pr_url,
                )

            self.update_status(
                card.id,
                status="completed",
                note=f"PR #{linked_pr_number} is green; human gate pending",
                metadata_updates={
                    "human_gate_pending": True,
                    "linked_pr_number": linked_pr_number,
                    "linked_pr_url": pr_url,
                },
            )
            self.append_journal(
                kind="human_gate_pending",
                summary=f"{card.title}: PR #{linked_pr_number} is ready for human gate",
                task_id=card.id,
                metadata={"pr_number": linked_pr_number},
            )
            self._comment_on_pr(linked_pr_number, self._comment_human_gate(linked_pr_number))
            if issue_number is not None:
                self._comment_on_issue(issue_number, self._comment_human_gate(linked_pr_number))
            if use_worktree:
                await worktree_manager.remove_worktree(self._worktree_slug(card))
            return RepoRunResult(
                card_id=card.id,
                status="completed",
                assistant_summary=assistant_summary,
                run_report_path=str(current_run_report),
                verification_report_path=str(current_verification_report),
                verification_steps=verification_steps,
                attempt_count=attempt_count,
                worktree_path=str(working_cwd),
                pr_number=linked_pr_number,
                pr_url=pr_url,
            )

        exhausted = "repair rounds exhausted"
        self.update_status(
            card.id,
            status="failed",
            note=exhausted,
            metadata_updates={"last_failure_stage": "repair_exhausted", "last_failure_summary": exhausted},
        )
        return RepoRunResult(
            card_id=card.id,
            status="failed",
            run_report_path=str(current_run_report),
            verification_report_path=str(current_verification_report),
            attempt_count=max_attempts,
            worktree_path=str(working_cwd),
        )

    async def tick(
        self,
        *,
        model: str | None = None,
        max_turns: int | None = None,
        permission_mode: str | None = None,
        issue_limit: int = 10,
        pr_limit: int = 10,
    ) -> RepoRunResult | None:
        self.scan_all_sources(issue_limit=issue_limit, pr_limit=pr_limit)
        if any(card.status in {"preparing", "running", "verifying", "waiting_ci", "repairing"} for card in self.list_cards()):
            self.append_journal(kind="tick_skip", summary="Skipped run-next because another card is active")
            return None
        if self.pick_next_card() is None:
            self.append_journal(kind="tick_idle", summary="Tick completed with no queued work")
            return None
        return await self.run_next(
            model=model,
            max_turns=max_turns,
            permission_mode=permission_mode,
        )

    def install_default_cron(self) -> list[str]:
        from openharness.services.cron import upsert_cron_job

        jobs = [
            {
                "name": "autopilot.scan",
                "schedule": "*/30 * * * *",
                "command": f"oh autopilot scan all --cwd {self._cwd}",
                "cwd": str(self._cwd),
            },
            {
                "name": "autopilot.tick",
                "schedule": "0 */2 * * *",
                "command": f"oh autopilot tick --cwd {self._cwd}",
                "cwd": str(self._cwd),
            },
        ]
        for job in jobs:
            upsert_cron_job(job)
        return [job["name"] for job in jobs]

    def export_dashboard(self, output_dir: str | Path | None = None) -> Path:
        target_dir = Path(output_dir) if output_dir is not None else self._cwd / "docs" / "autopilot"
        target_dir = target_dir.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        snapshot = self._build_dashboard_snapshot()
        atomic_write_text(
            target_dir / "snapshot.json",
            json.dumps(snapshot, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        )
        atomic_write_text(target_dir / "index.html", self._render_dashboard_html(snapshot))
        atomic_write_text(target_dir / ".nojekyll", "")
        return target_dir

    def _max_attempts(self, policies: dict[str, Any]) -> int:
        execution = dict(policies.get("autopilot", {}).get("execution", {}))
        repair = dict(policies.get("autopilot", {}).get("repair", {}))
        execution_attempts = int(execution.get("max_attempts", 3) or 3)
        repair_rounds = int(repair.get("max_rounds", 2) or 2)
        return max(execution_attempts, repair_rounds + 1, 1)

    def _base_branch(self, policies: dict[str, Any]) -> str:
        execution = dict(policies.get("autopilot", {}).get("execution", {}))
        return _safe_text(execution.get("base_branch")) or "main"

    def _head_branch(self, card: RepoTaskCard, policies: dict[str, Any]) -> str:
        github_policy = dict(policies.get("autopilot", {}).get("github", {}))
        prefix = _safe_text(github_policy.get("pr_branch_prefix")) or "autopilot/"
        return f"{prefix}{card.id}"

    def _worktree_slug(self, card: RepoTaskCard) -> str:
        return f"autopilot/{card.id}"

    def _run_command(
        self,
        command: str | list[str],
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
        shell: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            command,
            cwd=cwd or self._cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            shell=shell,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""},
        )
        if check and completed.returncode != 0:
            output = (completed.stderr or completed.stdout).strip() or f"Command failed: {command}"
            raise RuntimeError(output)
        return completed

    def _run_git(self, args: list[str], *, cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
        return self._run_command(["git", *args], cwd=cwd, check=check)

    def _run_gh(self, args: list[str], *, cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
        return self._run_command(["gh", *args], cwd=cwd, check=check)

    def _gh_json(self, args: list[str], *, cwd: Path | None = None) -> Any:
        completed = self._run_gh(args, cwd=cwd, check=True)
        raw = (completed.stdout or "").strip()
        if not raw:
            return None
        return json.loads(raw)

    def _git_has_changes(self, cwd: Path) -> bool:
        completed = self._run_git(["status", "--porcelain"], cwd=cwd, check=True)
        return bool((completed.stdout or "").strip())

    def _is_git_repo(self, cwd: Path) -> bool:
        completed = self._run_git(["rev-parse", "--git-dir"], cwd=cwd)
        return completed.returncode == 0

    def _git_commit_all(self, cwd: Path, message: str) -> bool:
        if not self._git_has_changes(cwd):
            return False
        self._run_git(["add", "-A"], cwd=cwd, check=True)
        self._run_git(["commit", "-m", message], cwd=cwd, check=True)
        return True

    def _git_push_branch(self, cwd: Path, branch: str) -> None:
        self._run_git(["push", "-u", "origin", branch], cwd=cwd, check=True)

    def _git_branch_has_progress(self, cwd: Path, *, base_branch: str) -> bool:
        completed = self._run_git(
            ["rev-list", "--count", f"origin/{base_branch}..HEAD"],
            cwd=cwd,
        )
        if completed.returncode != 0:
            return False
        try:
            return int((completed.stdout or "0").strip() or "0") > 0
        except ValueError:
            return False

    def _sync_worktree_to_base(self, cwd: Path, *, base_branch: str, head_branch: str, reset: bool) -> None:
        self._run_git(["fetch", "origin", base_branch], cwd=cwd, check=True)
        if reset:
            self._run_git(["checkout", "-B", head_branch, f"origin/{base_branch}"], cwd=cwd, check=True)
            return
        self._run_git(["checkout", head_branch], cwd=cwd, check=True)

    def _issue_number_for_card(self, card: RepoTaskCard) -> int | None:
        linked = card.metadata.get("linked_issue_numbers")
        if isinstance(linked, list) and linked:
            try:
                return int(linked[0])
            except (TypeError, ValueError):
                pass
        return _source_ref_number(card.source_ref, "issue")

    def _linked_pr_number(self, card: RepoTaskCard) -> int | None:
        linked = card.metadata.get("linked_pr_number")
        if linked is not None:
            try:
                return int(linked)
            except (TypeError, ValueError):
                return None
        return _source_ref_number(card.source_ref, "pr")

    def _current_repo_full_name(self) -> str:
        info = self._gh_json(["repo", "view", "--json", "nameWithOwner"], cwd=self._cwd) or {}
        repo = _safe_text(info.get("nameWithOwner"))
        if not repo:
            raise RuntimeError("Unable to resolve GitHub repository name with `gh repo view`.")
        return repo

    def _find_open_pr_for_branch(self, head_branch: str) -> dict[str, Any] | None:
        data = self._gh_json(
            [
                "pr",
                "list",
                "--state",
                "open",
                "--head",
                head_branch,
                "--json",
                "number,url,isDraft,labels,headRefName,baseRefName,mergeStateStatus,reviewDecision",
            ],
            cwd=self._cwd,
        )
        if isinstance(data, list) and data:
            return data[0]
        return None

    def _best_effort_add_labels(self, pr_number: int, labels: list[str]) -> None:
        normalized = [label for label in labels if label]
        if not normalized:
            return
        try:
            self._run_gh(["pr", "edit", str(pr_number), *sum([["--add-label", label] for label in normalized], [])], cwd=self._cwd)
        except Exception:
            self.append_journal(
                kind="github_warning",
                summary=f"Failed to add labels to PR #{pr_number}; continuing",
                metadata={"labels": normalized},
            )

    def _build_pr_body(
        self,
        card: RepoTaskCard,
        *,
        run_report_path: Path,
        verification_report_path: Path,
    ) -> str:
        issue_number = self._issue_number_for_card(card)
        body = [
            "## Autopilot Summary",
            "",
            f"- Task ID: `{card.id}`",
            f"- Source: `{card.source_kind}`",
            f"- Source ref: `{card.source_ref or '-'}`",
            "",
            "## Reports",
            "",
            f"- Run report: `{run_report_path}`",
            f"- Verification report: `{verification_report_path}`",
            "",
            "## Notes",
            "",
            "- Agent self-reported summary is not the source of truth.",
            "- Service-level local verification and remote CI status should be checked before merge.",
        ]
        if issue_number is not None:
            body.extend(["", f"Closes #{issue_number}"])
        return "\n".join(body).strip() + "\n"

    def _upsert_pull_request(
        self,
        card: RepoTaskCard,
        *,
        head_branch: str,
        base_branch: str,
        run_report_path: Path,
        verification_report_path: Path,
    ) -> dict[str, Any]:
        existing = self._find_open_pr_for_branch(head_branch)
        if existing is not None:
            self._best_effort_add_labels(existing.get("number"), ["autopilot"])
            return existing

        title = f"Autopilot: {card.title}"
        body = self._build_pr_body(
            card,
            run_report_path=run_report_path,
            verification_report_path=verification_report_path,
        )
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".md") as handle:
            handle.write(body)
            body_path = Path(handle.name)
        try:
            self._run_gh(
                [
                    "pr",
                    "create",
                    "--title",
                    title,
                    "--body-file",
                    str(body_path),
                    "--base",
                    base_branch,
                    "--head",
                    head_branch,
                ],
                cwd=self._cwd,
                check=True,
            )
        finally:
            body_path.unlink(missing_ok=True)

        created = self._find_open_pr_for_branch(head_branch)
        if created is None:
            raise RuntimeError(f"PR creation succeeded but PR for branch {head_branch} was not discoverable.")
        self._best_effort_add_labels(created.get("number"), ["autopilot"])
        return created

    def _comment_on_issue(self, issue_number: int, comment: str) -> None:
        try:
            self._run_gh(["issue", "comment", str(issue_number), "--body", comment], cwd=self._cwd, check=True)
        except Exception as exc:
            self.append_journal(
                kind="github_warning",
                summary=f"Failed to comment on issue #{issue_number}: {exc}",
                metadata={"issue": issue_number},
            )

    def _comment_on_pr(self, pr_number: int, comment: str) -> None:
        try:
            self._run_gh(["pr", "comment", str(pr_number), "--body", comment], cwd=self._cwd, check=True)
        except Exception as exc:
            self.append_journal(
                kind="github_warning",
                summary=f"Failed to comment on PR #{pr_number}: {exc}",
                metadata={"pr": pr_number},
            )

    def _comment_started(self, card: RepoTaskCard, attempt_count: int) -> str:
        return _bilingual_lines(
            f"OpenHarness autopilot 已开始处理 `{card.id}`，当前第 {attempt_count} 轮执行。",
            f"OpenHarness autopilot started processing `{card.id}`. Attempt {attempt_count} is now running.",
        )

    def _comment_pr_opened(self, pr_number: int, pr_url: str) -> str:
        return _bilingual_lines(
            f"已创建或更新 PR #{pr_number}: {pr_url}",
            f"Created or updated PR #{pr_number}: {pr_url}",
        )

    def _comment_ci_failed(self, attempt_count: int, summary: str) -> str:
        return _bilingual_lines(
            f"远端 CI 失败，准备进入第 {attempt_count + 1} 轮自动修复。摘要：{summary}",
            f"Remote CI failed. Preparing repair round {attempt_count + 1}. Summary: {summary}",
        )

    def _comment_local_failed(self, attempt_count: int, summary: str) -> str:
        return _bilingual_lines(
            f"本地 verification 失败，准备进入第 {attempt_count + 1} 轮自动修复。摘要：{summary}",
            f"Local verification failed. Preparing repair round {attempt_count + 1}. Summary: {summary}",
        )

    def _comment_merged(self, pr_number: int) -> str:
        return _bilingual_lines(
            f"PR #{pr_number} 已自动合并，任务闭环完成。",
            f"PR #{pr_number} was auto-merged. The autopilot loop has completed.",
        )

    def _comment_human_gate(self, pr_number: int) -> str:
        return _bilingual_lines(
            f"PR #{pr_number} 的本地验证和远端 CI 都已通过，但仍需人工 gate 或 merge label。",
            f"PR #{pr_number} passed local verification and remote CI, but still requires a human gate or merge label.",
        )

    def _comment_terminal_failure(self, summary: str) -> str:
        return _bilingual_lines(
            f"自动化流程已停止。失败原因：{summary}",
            f"The automated loop has stopped. Failure reason: {summary}",
        )

    def _pr_status_snapshot(self, pr_number: int) -> dict[str, Any]:
        payload = self._gh_json(
            [
                "pr",
                "view",
                str(pr_number),
                "--json",
                "number,url,isDraft,labels,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup",
            ],
            cwd=self._cwd,
        ) or {}
        payload["labels"] = [
            _safe_text(label.get("name"))
            for label in payload.get("labels", [])
            if isinstance(label, dict) and _safe_text(label.get("name"))
        ]
        return payload

    def _ci_rollup(self, pr_snapshot: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
        checks = pr_snapshot.get("statusCheckRollup") or []
        normalized: list[dict[str, Any]] = []
        if not isinstance(checks, list):
            checks = []
        for item in checks:
            if not isinstance(item, dict):
                continue
            name = _safe_text(item.get("name") or item.get("context") or item.get("__typename") or "check")
            status = _safe_text(item.get("status")).upper()
            conclusion = _safe_text(item.get("conclusion")).upper()
            details_url = _safe_text(item.get("detailsUrl") or item.get("targetUrl"))
            normalized.append(
                {
                    "name": name,
                    "status": status,
                    "conclusion": conclusion,
                    "details_url": details_url,
                }
            )
        if not normalized:
            return "pending", "Remote CI checks have not appeared yet.", normalized
        if any(item["status"] in {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING"} or (not item["conclusion"] and item["status"] != "COMPLETED") for item in normalized):
            return "pending", "Remote CI is still running.", normalized
        failing = [
            item for item in normalized
            if item["conclusion"] and item["conclusion"] not in {"SUCCESS", "SKIPPED", "NEUTRAL"}
        ]
        if failing:
            summary = "; ".join(f"{item['name']}={item['conclusion']}" for item in failing[:4])
            return "failed", summary, normalized
        return "success", "All reported remote checks passed.", normalized

    async def _wait_for_pr_ci(self, pr_number: int, policies: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
        github_policy = dict(policies.get("autopilot", {}).get("github", {}))
        timeout_seconds = int(github_policy.get("ci_timeout_seconds", 1800) or 1800)
        poll_interval = int(github_policy.get("ci_poll_interval_seconds", 20) or 20)
        no_checks_grace_seconds = int(github_policy.get("no_checks_grace_seconds", 60) or 60)
        checks_settle_seconds = int(github_policy.get("checks_settle_seconds", 20) or 20)
        deadline = time.time() + max(timeout_seconds, 30)
        no_checks_deadline = time.time() + max(no_checks_grace_seconds, poll_interval, 5)
        checks_seen_at: float | None = None
        while True:
            snapshot = self._pr_status_snapshot(pr_number)
            state, summary, checks = self._ci_rollup(snapshot)
            now = time.time()
            if checks and checks_seen_at is None:
                checks_seen_at = now
            if not checks and time.time() >= no_checks_deadline:
                return "success", "No remote checks were reported after the grace period.", snapshot, checks
            if state == "success" and checks and checks_seen_at is not None and now < checks_seen_at + max(checks_settle_seconds, 0):
                await asyncio.sleep(max(poll_interval, 5))
                continue
            if state in {"success", "failed"}:
                return state, summary, snapshot, checks
            if now >= deadline:
                return "failed", "Remote CI timed out.", snapshot, checks
            await asyncio.sleep(max(poll_interval, 5))

    def _automerge_eligible(self, pr_snapshot: dict[str, Any], policies: dict[str, Any]) -> bool:
        github_policy = dict(policies.get("autopilot", {}).get("github", {}))
        auto_merge = dict(github_policy.get("auto_merge", {}))
        mode = _safe_text(auto_merge.get("mode")) or "label_gated"
        required_label = _safe_text(auto_merge.get("required_label")) or "autopilot:merge"
        labels = {str(label).lower() for label in pr_snapshot.get("labels", [])}
        if bool(pr_snapshot.get("isDraft")):
            return False
        if mode == "pr_only":
            return False
        if mode == "fully_auto":
            return True
        return required_label.lower() in labels

    def _merge_pull_request(self, pr_number: int) -> None:
        self._run_gh(
            ["pr", "merge", str(pr_number), "--squash"],
            cwd=self._cwd,
            check=True,
        )

    def _prepare_repair_prompt(
        self,
        card: RepoTaskCard,
        policies: dict[str, Any],
        *,
        attempt_count: int,
        prior_summary: str | None,
        failure_stage: str | None,
        failure_summary: str | None,
    ) -> str:
        prompt = self._build_execution_prompt(card, policies)
        if attempt_count <= 1 or not failure_stage:
            return prompt
        extras = [
            "",
            "Repair context:",
            f"- Attempt: {attempt_count}",
            f"- Previous failure stage: {failure_stage}",
            f"- Previous failure summary: {failure_summary or '(none)'}",
        ]
        if prior_summary:
            extras.append(f"- Previous agent summary: {_shorten(prior_summary, limit=600)}")
        extras.extend(
            [
                "",
                "Repair instructions:",
                "- Make the smallest patch that fixes the reported failure.",
                "- Do not restart the task from scratch if the existing branch already contains valid progress.",
                "- Re-run the relevant verification commands after the fix.",
            ]
        )
        return prompt + "\n" + "\n".join(extras).strip() + "\n"

    async def _process_existing_pr_card(
        self,
        card: RepoTaskCard,
        pr_number: int,
        policies: dict[str, Any],
    ) -> RepoRunResult:
        current_run_report = self._runs_dir / f"{card.id}-run.md"
        current_verification_report = self._runs_dir / f"{card.id}-verification.md"
        self.update_status(
            card.id,
            status="waiting_ci",
            note=f"monitoring existing PR #{pr_number}",
            metadata_updates={"linked_pr_number": pr_number},
        )
        ci_state, ci_summary, pr_snapshot, _checks = await self._wait_for_pr_ci(pr_number, policies)
        pr_url = _safe_text(pr_snapshot.get("url"))
        if ci_state == "failed":
            self.update_status(
                card.id,
                status="failed",
                note=f"existing PR CI failed: {ci_summary}",
                metadata_updates={
                    "linked_pr_number": pr_number,
                    "linked_pr_url": pr_url,
                    "last_failure_stage": "remote_ci_failed",
                    "last_failure_summary": ci_summary,
                },
            )
            self._comment_on_pr(pr_number, self._comment_terminal_failure(ci_summary))
            return RepoRunResult(
                card_id=card.id,
                status="failed",
                run_report_path=str(current_run_report),
                verification_report_path=str(current_verification_report),
                pr_number=pr_number,
                pr_url=pr_url,
            )
        if self._automerge_eligible(pr_snapshot, policies):
            self._merge_pull_request(pr_number)
            self.update_status(
                card.id,
                status="merged",
                note=f"existing PR #{pr_number} merged automatically",
                metadata_updates={"linked_pr_number": pr_number, "linked_pr_url": pr_url},
            )
            self._comment_on_pr(pr_number, self._comment_merged(pr_number))
            return RepoRunResult(
                card_id=card.id,
                status="merged",
                run_report_path=str(current_run_report),
                verification_report_path=str(current_verification_report),
                pr_number=pr_number,
                pr_url=pr_url,
            )
        self.update_status(
            card.id,
            status="completed",
            note=f"existing PR #{pr_number} is green; human gate pending",
            metadata_updates={
                "linked_pr_number": pr_number,
                "linked_pr_url": pr_url,
                "human_gate_pending": True,
            },
        )
        self._comment_on_pr(pr_number, self._comment_human_gate(pr_number))
        return RepoRunResult(
            card_id=card.id,
            status="completed",
            run_report_path=str(current_run_report),
            verification_report_path=str(current_verification_report),
            pr_number=pr_number,
            pr_url=pr_url,
        )

    def _build_dashboard_snapshot(self) -> dict[str, Any]:
        registry = self._load_registry()
        cards = sorted(
            registry.cards,
            key=lambda card: (
                self._status_sort_key(card.status),
                -card.score,
                -card.updated_at,
                card.title.lower(),
            ),
        )
        status_order = [
            "queued",
            "accepted",
            "preparing",
            "running",
            "verifying",
            "pr_open",
            "waiting_ci",
            "repairing",
            "completed",
            "merged",
            "failed",
            "rejected",
            "superseded",
        ]
        columns = {status: [] for status in status_order}
        counts = {status: 0 for status in status_order}
        for card in cards:
            counts[card.status] = counts.get(card.status, 0) + 1
            columns.setdefault(card.status, []).append(self._serialize_card(card))

        focus = None
        for status in ("repairing", "waiting_ci", "running", "verifying", "preparing", "accepted", "queued"):
            bucket = columns.get(status) or []
            if bucket:
                focus = bucket[0]
                break

        return {
            "generated_at": time.time(),
            "repo_name": self._cwd.name,
            "repo_path": str(self._cwd),
            "focus": focus,
            "counts": counts,
            "status_order": status_order,
            "columns": columns,
            "cards": [self._serialize_card(card) for card in cards],
            "journal": [
                {
                    "timestamp": entry.timestamp,
                    "kind": entry.kind,
                    "summary": entry.summary,
                    "task_id": entry.task_id,
                    "metadata": entry.metadata,
                }
                for entry in self.load_journal(limit=30)
            ],
            "policies": {
                "autopilot": str(get_project_autopilot_policy_path(self._cwd)),
                "verification": str(get_project_verification_policy_path(self._cwd)),
                "release": str(get_project_release_policy_path(self._cwd)),
            },
            "active_context": self.load_active_context(),
        }

    def _serialize_card(self, card: RepoTaskCard) -> dict[str, Any]:
        verification_steps = []
        for step in card.metadata.get("verification_steps", []) or []:
            if isinstance(step, dict):
                verification_steps.append(
                    {
                        "command": _safe_text(step.get("command")),
                        "status": _safe_text(step.get("status")),
                        "returncode": step.get("returncode"),
                    }
                )
        return {
            "id": card.id,
            "title": card.title,
            "body": card.body,
            "status": card.status,
            "source_kind": card.source_kind,
            "source_ref": card.source_ref,
            "score": card.score,
            "score_reasons": list(card.score_reasons),
            "labels": list(card.labels),
            "created_at": card.created_at,
            "updated_at": card.updated_at,
            "metadata": {
                "last_note": _safe_text(card.metadata.get("last_note")),
                "url": _safe_text(card.metadata.get("url")),
                "execution_model": _safe_text(card.metadata.get("execution_model")),
                "assistant_summary_preview": _safe_text(card.metadata.get("assistant_summary_preview")),
                "human_gate_pending": bool(card.metadata.get("human_gate_pending")),
                "verification_failed": bool(card.metadata.get("verification_failed")),
                "attempt_count": int(card.metadata.get("attempt_count", 0) or 0),
                "max_attempts": int(card.metadata.get("max_attempts", 0) or 0),
                "linked_pr_number": card.metadata.get("linked_pr_number"),
                "linked_pr_url": _safe_text(card.metadata.get("linked_pr_url")),
                "last_ci_conclusion": _safe_text(card.metadata.get("last_ci_conclusion")),
                "last_ci_summary": _safe_text(card.metadata.get("last_ci_summary")),
                "last_failure_stage": _safe_text(card.metadata.get("last_failure_stage")),
                "last_failure_summary": _safe_text(card.metadata.get("last_failure_summary")),
                "verification_steps": verification_steps,
            },
        }

    def _status_sort_key(self, status: str) -> int:
        order = {
            "repairing": 0,
            "waiting_ci": 1,
            "running": 2,
            "verifying": 3,
            "preparing": 4,
            "accepted": 5,
            "pr_open": 6,
            "queued": 7,
            "completed": 8,
            "merged": 9,
            "failed": 10,
            "rejected": 11,
            "superseded": 12,
        }
        return order.get(status, 99)

    def _render_dashboard_html(self, snapshot: dict[str, Any]) -> str:
        """Return a minimal fallback HTML page.

        The primary dashboard is now a React + Vite app built from
        ``autopilot-dashboard/``.  This fallback is only written when
        no pre-built ``index.html`` already exists in the output
        directory, so local ``snapshot.json`` generation still works
        without a Node.js toolchain.
        """
        repo_name = escape(_safe_text(snapshot.get("repo_name")) or "OpenHarness")
        generated = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC",
            time.gmtime(float(snapshot.get("generated_at") or time.time())),
        )
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{repo_name} Autopilot Kanban</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --bg: #0a0a0a; --bg-elevated: #1a1a1a; --ink: #fff;
      --accent: #00d4aa; --muted: #666; --line: #222;
      --mono: "JetBrains Mono", ui-monospace, monospace;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--ink); font-family: var(--mono); font-size: 13px; }}
    .shell {{ max-width: 960px; margin: 80px auto; padding: 0 20px; text-align: center; }}
    h1 {{ font-size: 32px; letter-spacing: 2px; margin-bottom: 16px; }}
    h1 span {{ color: var(--accent); }}
    .sub {{ color: var(--muted); font-size: 12px; line-height: 1.8; margin-bottom: 32px; }}
    .info {{ background: var(--bg-elevated); border: 1px solid var(--line); border-radius: 6px; padding: 24px; text-align: left; }}
    .info p {{ color: #888; font-size: 12px; line-height: 1.7; margin-bottom: 12px; }}
    .info code {{ color: var(--accent); }}
    .ts {{ color: var(--muted); font-size: 10px; letter-spacing: 1px; margin-top: 20px; }}
  </style>
</head>
<body>
  <div class="shell">
    <h1>{repo_name} <span>AUTOPILOT</span></h1>
    <p class="sub">
      This is a fallback page. The full React dashboard is built via CI
      from <code>autopilot-dashboard/</code>.
    </p>
    <div class="info">
      <p>To view the full dashboard locally, build the React app:</p>
      <p><code>cd autopilot-dashboard &amp;&amp; npm install &amp;&amp; npm run build</code></p>
      <p>Then open <code>docs/autopilot/index.html</code> in a browser.</p>
      <p>Snapshot data: <code>snapshot.json</code> (generated {escape(generated)})</p>
    </div>
    <div class="ts">Generated at {escape(generated)}</div>
  </div>
</body>
</html>
"""

    def _ensure_layout(self) -> None:
        for path, payload in (
            (get_project_autopilot_policy_path(self._cwd), _DEFAULT_AUTOPILOT_POLICY),
            (get_project_verification_policy_path(self._cwd), _DEFAULT_VERIFICATION_POLICY),
            (get_project_release_policy_path(self._cwd), _DEFAULT_RELEASE_POLICY),
        ):
            if not path.exists():
                atomic_write_text(path, yaml.safe_dump(payload, sort_keys=False))
        if not self._registry_path.exists():
            self._save_registry(RepoAutopilotRegistry(updated_at=time.time(), cards=[]))
        if not self._context_path.exists():
            self.rebuild_active_context()

    def _load_registry(self) -> RepoAutopilotRegistry:
        if not self._registry_path.exists():
            return RepoAutopilotRegistry(updated_at=time.time(), cards=[])
        try:
            payload = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return RepoAutopilotRegistry(updated_at=time.time(), cards=[])
        return RepoAutopilotRegistry.model_validate(payload)

    def _save_registry(self, registry: RepoAutopilotRegistry) -> None:
        registry.updated_at = time.time()
        atomic_write_text(
            self._registry_path,
            json.dumps(
                registry.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
            + "\n",
        )

    def _build_fingerprint(
        self,
        *,
        source_kind: RepoTaskSource,
        source_ref: str,
        title: str,
        body: str,
    ) -> str:
        basis = source_ref.strip() or f"{title.strip()}\n{body.strip()}"
        digest = sha1(basis.encode("utf-8")).hexdigest()[:16]
        return f"{source_kind}:{digest}"

    def _score_card(self, card: RepoTaskCard) -> tuple[int, list[str]]:
        score = _SOURCE_BASE_SCORES.get(card.source_kind, 50)
        reasons = [f"source={card.source_kind}"]
        text = f"{card.title}\n{card.body}".lower()
        labels = {label.lower() for label in card.labels}
        if card.source_kind == "github_issue":
            if labels.intersection({"bug", "regression", "failure"}):
                score += 25
                reasons.append("bug-labelled issue")
            if any(hint in text for hint in _BUG_HINTS):
                score += 15
                reasons.append("issue looks like a bug/regression")
        if card.source_kind == "github_pr":
            if bool(card.metadata.get("is_draft")):
                score -= 30
                reasons.append("draft pr")
            if str(card.metadata.get("merge_state_status", "")).upper() == "CLEAN":
                score += 20
                reasons.append("clean merge state")
            if str(card.metadata.get("review_decision", "")).upper() == "APPROVED":
                score += 20
                reasons.append("approved review state")
        if card.source_kind in {"ohmo_request", "manual_idea"}:
            score += 10
            reasons.append("direct user-driven input")
        if any(hint in text for hint in _URGENT_HINTS) or labels.intersection(
            {"urgent", "p0", "p1", "high", "critical", "blocker"}
        ):
            score += 20
            reasons.append("urgent signals")
        age_days = max(0.0, (time.time() - card.updated_at) / 86400.0)
        freshness_bonus = max(0, 10 - int(age_days))
        if freshness_bonus:
            score += freshness_bonus
            reasons.append("recently updated")
        return score, reasons

    def _normalize_labels(self, labels: list[str] | None) -> list[str]:
        if not labels:
            return []
        return sorted({label.strip() for label in labels if label and label.strip()})

    def _merge_labels(self, existing: list[str], incoming: list[str]) -> list[str]:
        return sorted({*existing, *incoming})

    def _run_gh_json(self, command: list[str]) -> list[dict[str, Any]]:
        try:
            completed = subprocess.run(
                command,
                cwd=self._cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ValueError("gh CLI is not installed.") from exc
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout).strip() or "gh command failed"
            raise ValueError(error)
        raw = (completed.stdout or "").strip()
        if not raw:
            return []
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("Expected gh JSON array output.")
        return [item for item in payload if isinstance(item, dict)]

    def _read_yaml(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return dict(default)
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return dict(default)
        if not isinstance(payload, dict):
            return dict(default)
        return payload

    # todo @Toby注释: [Execution Prompt 组装] 这是告诉 Agent"要做什么"的核心 prompt。
    # 结构：
    #   1. Goal → 最小化修改、自己跑验证、不 merge、保持可审查状态
    #   2. 任务卡片 → id/source/title/body（含用户描述的问题细节）
    #   3. Autopilot policy → max_turns、permission_mode 等执行约束
    #   4. Verification policy → 验证命令列表，Agent 能看到"完成后会被什么标准检查"
    #   5. Expected output → 变更说明、验证结果、剩余风险
    # 注意：prompt 要求 Agent "Run verification commands yourself"（建议性），
    # 但 run_card 在 Agent 停止后会**强制**再跑一遍 _run_verification_steps（强制执行）。
    # 测试用例不是 Agent 自动写的——pytest 执行的是项目中已有的测试。
    def _build_execution_prompt(self, card: RepoTaskCard, policies: dict[str, Any]) -> str:
        autopilot_policy = yaml.safe_dump(policies["autopilot"], sort_keys=False).strip()
        verification_policy = yaml.safe_dump(policies["verification"], sort_keys=False).strip()
        release_policy = yaml.safe_dump(policies["release"], sort_keys=False).strip()
        return (
            "You are executing one repo-autopilot task for the current repository.\n\n"
            "Goal:\n"
            "- Make the smallest coherent implementation that resolves the task.\n"
            "- Run the relevant verification commands yourself before stopping.\n"
            "- Do not merge, release, or perform irreversible external actions.\n"
            "- Leave the repository in a reviewable state and summarize what changed.\n\n"
            f"Task ID: {card.id}\n"
            f"Source: {card.source_kind}\n"
            f"Source ref: {card.source_ref or '-'}\n"
            f"Title: {card.title}\n"
            f"Body:\n{card.body or '(none)'}\n\n"
            "Autopilot policy:\n"
            f"{autopilot_policy}\n\n"
            "Verification policy:\n"
            f"{verification_policy}\n\n"
            "Release policy:\n"
            f"{release_policy}\n\n"
            "Expected output:\n"
            "1. What you changed.\n"
            "2. What you verified.\n"
            "3. Any remaining risk or human follow-up.\n"
        )

    async def _run_agent_prompt(
        self,
        prompt: str,
        *,
        model: str | None,
        max_turns: int,
        permission_mode: str,
        cwd: Path | None = None,
    ) -> str:
        from openharness.ui.runtime import build_runtime, close_runtime, start_runtime

        async def _allow(_tool_name: str, _reason: str) -> bool:
            return True

        async def _ask(_question: str) -> str:
            return ""

        bundle = await build_runtime(
            cwd=str(cwd or self._cwd),
            model=model,
            max_turns=max_turns,
            permission_prompt=_allow,
            ask_user_prompt=_ask,
            permission_mode=permission_mode,
        )
        await start_runtime(bundle)
        collected: list[str] = []
        try:
            async for event in bundle.engine.submit_message(prompt):
                if isinstance(event, AssistantTextDelta):
                    collected.append(event.text)
                elif isinstance(event, AssistantTurnComplete):
                    text = event.message.text.strip()
                    if text and not "".join(collected).strip():
                        collected.append(text)
                elif isinstance(event, ErrorEvent):
                    raise RuntimeError(event.message)
        finally:
            await close_runtime(bundle)
        return "".join(collected).strip()

    def _verification_commands(self, policies: dict[str, Any]) -> list[_VerificationCommand]:
        configured = policies.get("verification", {}).get("commands", [])
        parsed = [_parse_verification_entry(entry) for entry in configured]
        selected: list[_VerificationCommand] = []
        for cmd in parsed:
            if cmd.error is not None:
                selected.append(cmd)
                continue
            if _looks_available(cmd.raw, self._cwd):
                selected.append(cmd)
        return selected

    # todo @Toby注释: [验证执行引擎] 把 verification_policy.yaml 中的 commands 逐个执行。
    # 每个命令在 worktree 中通过 subprocess.run() 执行，返回 RepoVerificationStep:
    #   success: returncode=0 → 验证通过
    #   failed:  returncode≠0 → 验证失败（会触发 repair 循环）
    #   error:   文件找不到/超时/异常 → 配置问题，同样算失败
    # 输出的 stdout/stderr 截断到4000字符，前端展示不会炸。
    def _run_verification_steps(self, policies: dict[str, Any], *, cwd: Path | None = None) -> list[RepoVerificationStep]:
        steps: list[RepoVerificationStep] = []
        # todo @Toby注释: [命令解析] 支持两种格式：
        #   字符串: "uv run pytest -q" → 自动推断 shell=True/False
        #   dict:   {"command": "...", "shell": true} → 精确控制
        # _verification_commands() 还会检查命令的可执行性（_looks_available），不可执行的跳过
        for cmd in self._verification_commands(policies):
            if cmd.error is not None:
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=-1,
                        status="error",
                        stderr=f"verification policy error: {cmd.error}",
                    )
                )
                continue
            target: str | list[str] = cmd.raw if cmd.shell else list(cmd.argv)
            try:
                # todo @Toby注释: [子进程执行] subprocess.run 在 worktree 中执行验证命令。
                # shell=True: 直接传字符串（支持管道/重定向等 shell 语法）
                # shell=False: 传 argv 列表（安全，防 shell 注入）
                # timeout=1800（30分钟），capture_output=True 捕获 stdout/stderr
                # check=False: 不抛异常，根据 returncode 自行判断成功/失败
                completed = subprocess.run(
                    target,
                    cwd=cwd or self._cwd,
                    shell=cmd.shell,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=1800,
                )
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=completed.returncode,
                        status="success" if completed.returncode == 0 else "failed",
                        stdout=(completed.stdout or "")[-4000:],
                        stderr=(completed.stderr or "")[-4000:],
                    )
                )
            except FileNotFoundError as exc:
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=-1,
                        status="error",
                        stderr=f"executable not found: {exc}",
                    )
                )
            except subprocess.TimeoutExpired as exc:
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=-1,
                        status="error",
                        stdout=_safe_text(getattr(exc, "stdout", ""))[-4000:],
                        stderr=f"Timed out after {exc.timeout}s",
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=-1,
                        status="error",
                        stderr=str(exc),
                    )
                )
        return steps

    def _render_verification_report(
        self,
        card: RepoTaskCard,
        steps: list[RepoVerificationStep],
    ) -> str:
        lines = [
            f"# Verification Report: {card.id}",
            "",
            f"Title: {card.title}",
            "",
        ]
        if not steps:
            lines.append("No verification commands were applicable.")
            return "\n".join(lines).strip() + "\n"
        for step in steps:
            lines.extend(
                [
                    f"## {step.status.upper()} :: {step.command}",
                    "",
                    f"Return code: {step.returncode}",
                    "",
                ]
            )
            if step.stdout:
                lines.extend(["### stdout", "```text", step.stdout, "```", ""])
            if step.stderr:
                lines.extend(["### stderr", "```text", step.stderr, "```", ""])
        return "\n".join(lines).strip() + "\n"

    def _render_run_report(
        self,
        card: RepoTaskCard,
        *,
        agent_summary: str,
        verification_steps: list[RepoVerificationStep],
        verification_status: str,
    ) -> str:
        lines = [
            f"# Autopilot Run Report: {card.id}",
            "",
            f"Title: {card.title}",
            f"Source: {card.source_kind}",
            f"Source ref: {card.source_ref or '-'}",
            "",
            "## Agent Self-Reported Summary",
            "",
            agent_summary.strip() or "(empty agent summary)",
            "",
            "## Service-Level Ground Truth",
            "",
            (
                "The section above is the model's own summary. "
                "Treat it as untrusted until the service-level verification results below finish."
            ),
            "",
        ]

        if verification_status == "not_started":
            lines.extend(
                [
                    "- Verification status: not started.",
                    "- The agent run itself failed before service-level verification could begin.",
                ]
            )
        elif verification_status == "pending":
            lines.extend(
                [
                    "- Verification status: pending.",
                    "- Service-level verification has not finished yet.",
                ]
            )
        else:
            overall = "passed" if verification_status == "passed" else "failed"
            lines.append(f"- Verification status: {overall}.")
            if verification_steps:
                for step in verification_steps:
                    lines.append(
                        f"- [{step.status}] `{step.command}` (rc={step.returncode})"
                    )
            else:
                lines.append("- No verification commands were applicable.")

        return "\n".join(lines).strip() + "\n"
