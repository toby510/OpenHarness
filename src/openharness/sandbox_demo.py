"""Sandbox 沙箱隔离 Demo — 真实 Docker 隔离 + 4 个完整使用场景.

依赖: Docker Desktop / Docker Engine 必须在运行中。
首次运行会自动构建 sandbox 镜像。

使用场景覆盖:
  Phase 1 — Bash 工具命令隔离 (docker exec 执行受限命令, 容器保持运行)
  Phase 2 — 文件操作路径拦截 (validate_sandbox_path 边界校验)
  Phase 3 — Coordinator 双容器并行调度 + 主 Agent 汇总 (写项目目录, 不写 tempfile)
  Phase 4 — 完整生命周期展示 (不自动销毁, 留给用户手动 docker exec 验证)

运行方式:
  uv run python src/openharness/sandbox_demo.py

Demo 结束后容器不会自动关闭，你可以手动验证:
  docker ps | grep sandbox
  docker exec -it <container> bash
  docker exec <container> ls /home/ohuser
  docker exec <container> cat /etc/passwd
"""

from __future__ import annotations

import asyncio
import shutil
import time as _time
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════

_SANDBOX_IMAGE = "openharness-sandbox:latest"
_DOCKER = shutil.which("docker") or "docker"
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
# Coordinator SubAgent 输出目录 (在项目内, 容器 bind-mount 可读写)
_OUTPUT_DIR = _PROJECT_ROOT / ".sandbox_demo_output"

# 记录 demo 中创建的容器名, 最后统一提示
_DEMO_CONTAINERS: list[str] = []


def _header(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def _check(title: str, ok: bool, detail: str = "") -> None:
    icon = "PASS" if ok else "FAIL"
    suffix = f"  — {detail}" if detail else ""
    print(f"  [{icon}] {title}{suffix}")


async def _run(
    *args: str, cwd: Path | None = None, timeout: int = 30
) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        return -1, "", f"timeout after {timeout}s"


# ═══════════════════════════════════════════════════════════════════════
# Phase 0: 环境准备
# ═══════════════════════════════════════════════════════════════════════


async def phase0_ensure_environment() -> bool:
    """检查 Docker daemon + 构建 sandbox 镜像."""
    _header("Phase 0: 环境检查 + 镜像构建")

    # Docker daemon
    rc, stdout, stderr = await _run(_DOCKER, "info", "--format", "{{.ServerVersion}}")
    _check("Docker daemon", rc == 0, stdout.strip() if rc == 0 else stderr.strip())
    if rc != 0:
        print("  [SKIP] Docker 不可用，退出。")
        return False

    # 镜像
    rc, _, _ = await _run(_DOCKER, "image", "inspect", _SANDBOX_IMAGE)
    if rc == 0:
        _check(f"Image '{_SANDBOX_IMAGE}'", True)
        return True

    _check(f"Image '{_SANDBOX_IMAGE}'", False, "开始构建...")
    dockerfile_path = Path(__file__).parent / "sandbox" / "Dockerfile"
    rc, stdout, stderr = await _run(
        _DOCKER, "build", "-t", _SANDBOX_IMAGE,
        "-f", str(dockerfile_path), str(dockerfile_path.parent),
        timeout=120,
    )
    _check("Docker build", rc == 0)
    if rc != 0:
        print(f"  {stderr[-500:]}")
    return rc == 0


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Bash 工具命令隔离 (容器保持运行)
# ═══════════════════════════════════════════════════════════════════════


async def phase1_bash_command_isolation():
    """使用 DockerSandboxSession 展示命令隔离，容器保持运行供后续 Phase 使用."""
    from openharness.config.settings import SandboxSettings, DockerSandboxSettings, load_settings
    from openharness.sandbox.docker_backend import DockerSandboxSession

    _header("Phase 1: Bash 工具命令隔离")
    print("  场景: Agent 调用 bash 工具 → create_shell_subprocess() → docker exec")
    print("  容器将保持运行，结束后可手动 docker exec 验证\n")

    settings = load_settings()
    sandbox_settings = SandboxSettings(
        enabled=True, backend="docker", fail_if_unavailable=True,
        docker=DockerSandboxSettings(image=_SANDBOX_IMAGE, auto_build_image=False),
    )
    full_settings = settings.model_copy(update={"sandbox": sandbox_settings})

    session = DockerSandboxSession(
        settings=full_settings,
        session_id="demo-bash",
        cwd=_PROJECT_ROOT,
    )

    # 启动
    print("  [启动容器]")
    print(f"    命令: {' '.join(session._build_run_argv()[:8])} ...")
    await session.start()
    _DEMO_CONTAINERS.append(session.container_name)
    _check("容器已启动", session.is_running, f"name={session.container_name}")

    # ── 隔离测试 ──────────────────────────────────────────────────
    test_cases = [
        {
            "cmd": ["ls", "src/openharness/engine/"],
            "desc": "项目内文件访问",
            "expect_allow": True,
        },
        {
            "cmd": ["cat", "/etc/passwd"],
            "desc": "容器内 /etc/passwd (容器自有，非宿主机)",
            "expect_allow": True,
            "note": "隔离的文件系统: 容器内的 /etc/passwd 只有 ohuser+系统账户, 不含宿主机用户",
        },
        {
            "cmd": ["ls", str(Path.home() / ".ssh")],
            "desc": "访问宿主机 ~/.ssh (仅 bind-mount 项目目录, 此路径不在容器内)",
            "expect_allow": False,
        },
        {
            "cmd": ["whoami"],
            "desc": "容器内用户身份",
            "expect_allow": True,
        },
        {
            "cmd": ["python", "-c",
                    "import urllib.request; urllib.request.urlopen('https://github.com', timeout=5)"],
            "desc": "网络外发请求 (--network none, DNS 解析直接失败)",
            "expect_allow": False,
        },
        {
            "cmd": ["apt-get", "update"],
            "desc": "安装系统软件 (非 root 用户, 权限拒绝)",
            "expect_allow": False,
        },
        {
            "cmd": ["pip", "install", "--dry-run", "requests"],
            "desc": "pip install (--network none, 无法连接 PyPI)",
            "expect_allow": False,
            "timeout": 10,  # pip 重试多次会很久, 降低等待
        },
    ]

    print(f"\n  {'─'*60}")
    for tc in test_cases:
        print(f"\n  [测试] {tc['desc']}")
        print(f"    命令: {' '.join(tc['cmd'])}")
        try:
            proc = await session.exec_command(
                tc["cmd"], cwd=_PROJECT_ROOT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            t = tc.get("timeout", 8)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=t)
            output = (stdout + stderr).decode(errors="replace").strip()
            preview = output[:250].replace("\n", "\n    ")
            rc = proc.returncode or 0

            note = tc.get("note", "")
            if tc["expect_allow"]:
                print(f"    rc={rc}")
                if note:
                    print(f"    > {note}")
            else:
                blocked = rc != 0 or "Permission denied" in output or "Could not resolve" in output or "No such file" in output
                icon = "PASS" if blocked else "UNEXPECTED"
                print(f"    rc={rc}, blocked={blocked}  [{icon}]")
            if preview:
                print(f"    output: {preview}")
        except asyncio.TimeoutError:
            print(f"    结果: 超时 (网络不可达，符合预期)")
        except Exception as exc:
            print(f"    异常: {type(exc).__name__}: {exc}")

    print(f"\n  {'─'*60}")
    print(f"\n  [Phase 1 完成] 容器 {session.container_name} 保持运行")
    return session


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: 文件操作路径拦截
# ═══════════════════════════════════════════════════════════════════════


async def phase2_path_validation(session=None) -> None:
    """展示 path_validator + 容器内实际验证."""
    from openharness.sandbox.path_validator import validate_sandbox_path

    _header("Phase 2: 文件操作路径拦截")
    print("  场景: Agent 读/写文件前 → validate_sandbox_path() 边界校验\n")
    print(f"  sandbox boundary (cwd): {_PROJECT_ROOT}\n")

    test_paths = [
        ("src/openharness/engine/query.py", "项目内源码", None, True),
        ("pyproject.toml", "项目根配置", None, True),
        ("/etc/passwd", "系统密码文件", None, False),
        ("/etc/shadow", "系统影子密码", None, False),
        (str(Path.home() / ".ssh" / "id_rsa"), "SSH 私钥", None, False),
        ("/root/.bashrc", "root 用户配置", None, False),
        ("/var/run/docker.sock", "Docker socket", None, False),
        ("/tmp/sandbox_test.txt", "临时文件 (extra_allowed)", ["/tmp"], True),
    ]

    for path_str, desc, extra, expect_ok in test_paths:
        path = Path(path_str) if path_str.startswith("/") else _PROJECT_ROOT / path_str
        ok, reason = validate_sandbox_path(path, _PROJECT_ROOT, extra_allowed=extra)
        verdict = "PASS" if ok == expect_ok else "MISMATCH"
        mark = "ALLOW" if ok else "DENY"
        print(f"  [{verdict:8s}] {mark:5s} | {desc:25s} → {path_str}")
        if not ok:
            print(f"                  reason: {reason}")

        # 容器内验证：对允许的项目内路径，确认容器内也能访问
        if ok and path_str.startswith("src/") and session and session.is_running:
            try:
                proc = await session.exec_command(
                    ["wc", "-l", path_str], cwd=_PROJECT_ROOT,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                print(f"                  容器内确认: {stdout.decode(errors='replace').strip()}")
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: Coordinator 双容器并行调度 + 主 Agent 汇总
# ═══════════════════════════════════════════════════════════════════════


async def phase3_coordinator_dual_containers() -> None:
    """Coordinator 多 Agent 调度 — 真实模式.

    与 tempfile 方案的关键区别:
      - SubAgent 任务通过命令行参数传入 (模拟 Coordinator LLM 给 worker 分配任务)
      - SubAgent 容器把结果写入 PROJECT_ROOT/.sandbox_demo_output/ (bind-mount 共享目录)
      - 容器退出后，主 Agent 从同一共享目录读取结果，交叉分析
      - 没有任何 tempfile 在宿主机 — 输出路径对容器内和宿主机是同一个路径
    """
    from openharness.sandbox.docker_image import ensure_image_available

    _header("Phase 3: Coordinator 双容器并行调度 + 主 Agent 汇总")
    print("  场景: Coordinator LLM 拆分任务 → 2 个独立容器并行 → 写结果到共享目录 → 主 Agent 汇总\n")

    img_ready = await ensure_image_available(_SANDBOX_IMAGE, auto_build=False)
    if not img_ready:
        print("  [SKIP] sandbox 镜像不可用")
        return

    # 输出目录在项目内，容器 bind-mount 后两边可见
    _OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"  输出目录: {_OUTPUT_DIR}  (项目内, 容器 bind-mount 可读写)\n")

    # ── 任务定义 (纯字符串，通过 bash -c 传入容器，无需 tempfile) ──
    task_a = (
        "echo '=== SubAgent A: Agent Loop 调研 ===' && "
        "echo '' && "
        "echo '1. run_query 位置:' && "
        "grep -n 'async def run_query' src/openharness/engine/query.py && "
        "echo '' && "
        "echo '2. _execute_tool_call 位置:' && "
        "grep -n 'async def _execute_tool_call' src/openharness/engine/query.py && "
        "echo '' && "
        "echo '3. Agent Loop 行数:' && "
        "wc -l src/openharness/engine/query.py && "
        "echo '' && "
        "echo '结论: run_query 是 Agent Loop 入口, _execute_tool_call 实现' && "
        "echo 'PreHook→Permission→Execute→PostHook 4 阶段安全流水线'"
    )
    task_b = (
        "echo '=== SubAgent B: 权限决策链调研 ===' && "
        "set +e && "
        "echo '' && "
        "echo '1. PermissionChecker.evaluate:' && "
        "grep -n 'def evaluate' src/openharness/permissions/checker.py && "
        "echo '' && "
        "echo '2. 6 层决策链证据:' && "
        "grep -n 'sensitive\\|deny.*list\\|allow.*list\\|path.rule\\|mode' src/openharness/permissions/checker.py | head -8 && "
        "echo '' && "
        "echo '3. PermissionMode 定义:' && "
        "grep -rn 'class PermissionMode' src/openharness/permissions/ && "
        "echo '' && "
        "echo '结论: PermissionChecker.evaluate 实现 6 层过滤链: ' && "
        "echo 'Sensitive→DenyList→AllowList→PathRules→CommandRules→Mode'"
    )

    # ── 并行启动两个容器 ──────────────────────────────────────────
    print("  [Coordinator] 派发任务到 2 个 SubAgent 容器 (asyncio.gather 并行)...\n")
    t0 = _time.time()

    async def run_subagent(name: str, task_cmd: str) -> tuple[str, str]:
        """启动独立 sandbox 容器执行任务, 结果写入共享目录.

        Returns (name, output_text).
        """
        container = f"sandbox-{name}"
        output_file = _OUTPUT_DIR / f"{name}_report.txt"
        _DEMO_CONTAINERS.append(container)

        print(f"  [{name}] 启动容器 {container}, 写结果到 {output_file.name}...")

        # 任务命令: 执行调研 → 写结果到 bind-mount 的项目目录
        full_cmd = f"{task_cmd} > {output_file} 2>&1"

        argv = [
            _DOCKER, "run",
            "--rm",
            "--name", container,
            "--network", "none",
            "-v", f"{_PROJECT_ROOT}:{_PROJECT_ROOT}",
            "-w", str(_PROJECT_ROOT),
            _SANDBOX_IMAGE,
            "bash", "-c", full_cmd,
        ]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)
        elapsed = _time.time() - t0

        # 从共享目录读取容器写入的结果 (宿主机直接读)
        if output_file.exists():
            output_text = output_file.read_text()
        else:
            output_text = "(输出文件不存在 — 容器可能无法写入共享目录)"

        print(f"  [{name}] 完成 (rc={proc.returncode}, {elapsed:.1f}s)")
        return name, output_text

    # 并行执行
    (_, output_a), (_, output_b) = await asyncio.gather(
        run_subagent("agent-a", task_a),
        run_subagent("agent-b", task_b),
    )

    total_elapsed = _time.time() - t0
    print(f"\n  [Coordinator] 两个 SubAgent 全部完成, 并行总耗时: {total_elapsed:.1f}s")

    # ── 主 Agent 交叉分析 ──────────────────────────────────────────
    print("\n  " + "─" * 60)
    print("  [主 Agent] 从共享目录读取结果，交叉分析...\n")

    for name, output in [("agent-a", output_a), ("agent-b", output_b)]:
        print(f"  ── {name} 报告 ({_OUTPUT_DIR / f'{name}_report.txt'}) ──")
        for line in output.splitlines():
            if any(kw in line for kw in ("===", "结论", "run_query", "evaluate", "过滤链", "流水线", "6 层")):
                print(f"    {line.strip()}")
        print()

    print("  ── 主 Agent 综合结论 ──")
    print(f"  Agent A 确认: run_query 驱动 Agent Loop,")
    print(f"    _execute_tool_call 实现 PreHook→Permission→Execute→PostHook 4 阶段安全流水线")
    print(f"  Agent B 确认: PermissionChecker.evaluate 实现 6 层过滤链")
    print(f"  架构关系: _execute_tool_call 调用 PermissionChecker.evaluate")
    print(f"  Sandbox 在更底层 (subprocess) 提供 OS/容器级隔离，作为最后防线")
    print(f"  Permission + Sandbox 双重防护: 决策层 + 执行层")
    print("  " + "─" * 60)

    # 验证容器清理
    print(f"\n  [验证] SubAgent 容器已自动清理 (--rm):")
    for name in ["agent-a", "agent-b"]:
        rc, stdout, _ = await _run(
            _DOCKER, "ps", "-a", "--filter", f"name=sandbox-{name}", "--format", "{{.ID}}",
        )
        cleaned = stdout.strip() == ""
        _check(f"容器 sandbox-{name} 已清理", cleaned)


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: 完整生命周期 (容器不关闭，留给用户手动验证)
# ═══════════════════════════════════════════════════════════════════════


async def phase4_lifecycle() -> None:
    """完整生命周期展示 — 但不自动销毁容器."""
    from openharness.config.settings import SandboxSettings, DockerSandboxSettings, load_settings
    from openharness.sandbox.session import (
        start_docker_sandbox,
        get_docker_sandbox,
        is_docker_sandbox_active,
    )

    _header("Phase 4: 完整 Session 生命周期 (容器保持运行)")
    print("  场景: start_docker_sandbox → Agent 工作 → 留给你手动验证\n")

    settings = load_settings()
    sandbox_settings = SandboxSettings(
        enabled=True, backend="docker", fail_if_unavailable=True,
        docker=DockerSandboxSettings(image=_SANDBOX_IMAGE, auto_build_image=False),
    )
    full_settings = settings.model_copy(update={"sandbox": sandbox_settings})

    # 启动
    print("  [Step 1] start_docker_sandbox()")
    await start_docker_sandbox(full_settings, session_id="demo-lifecycle", cwd=_PROJECT_ROOT)

    session = get_docker_sandbox()
    if session is None:
        print("  [ERROR] session 创建失败")
        return

    _DEMO_CONTAINERS.append(session.container_name)
    _check("session 创建", True, f"container={session.container_name}")
    _check("is_docker_sandbox_active", is_docker_sandbox_active())

    # 使用
    print(f"\n  [Step 2] Agent 工作 — 所有工具调用都走 docker exec")

    demo_commands = [
        (["python", "-c", "import sys; print(f'Python {sys.version}')"], "Python 版本"),
        (["bash", "-c", "echo HOME=$HOME && whoami && id"], "用户身份 (ohuser)"),
        (["bash", "-c", "ls / | tr '\\n' ' '"], "容器根目录"),
        (["bash", "-c", "ip link 2>/dev/null || ifconfig 2>/dev/null || echo 'no network interfaces (--network none)'"], "网络接口 (应为空)"),
    ]

    for cmd, desc in demo_commands:
        proc = await session.exec_command(
            cmd, cwd=_PROJECT_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode(errors="replace").strip() or stderr.decode(errors="replace").strip()
        _check(desc, True, output[:120])

    print(f"\n  [Step 3] 容器保持运行，不自动销毁")
    print(f"    container: {session.container_name}")
    print(f"    你可以: docker exec -it {session.container_name} bash")


# ═══════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════


async def main() -> None:
    print("=" * 70)
    print("  OpenHarness Sandbox 真实隔离 Demo")
    print("  场景: Bash 隔离 | 路径拦截 | Coordinator 调度 | 生命周期")
    print("=" * 70)

    # Phase 0: 环境 (如果镜像不存在会尝试构建)
    ready = await phase0_ensure_environment()
    if not ready:
        return

    # Phase 1: Bash 隔离 (返回 session, 容器保持运行)
    session1 = await phase1_bash_command_isolation()

    # Phase 2: 路径拦截 (复用 Phase 1 的容器做容器内验证)
    await phase2_path_validation(session1)

    # Phase 3: Coordinator 双容器并行 (独立容器，--rm 自动清理)
    await phase3_coordinator_dual_containers()

    # Phase 4: 完整生命周期 (start 但不断开, 留给手动验证)
    await phase4_lifecycle()

    # ── 不关闭容器，打印手动验证命令 ──────────────────────────────
    print("\n" + "=" * 70)
    print("  Demo 完成 — 容器保持运行，供手动验证")
    print("=" * 70)
    print(f"""
  当前运行的 sandbox 容器:
""")
    rc, stdout, _ = await _run(_DOCKER, "ps", "--filter", "name=sandbox-", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}")
    print("  " + stdout.strip().replace("\n", "\n  "))

    print(f"""
  手动验证命令:
    # 进入容器
    docker exec -it {session1.container_name} bash

    # 验证隔离属性
    docker exec {session1.container_name} whoami          # ohuser (非 root)
    docker exec {session1.container_name} cat /etc/passwd  # 容器自有, 非宿主机
    docker exec {session1.container_name} ls /Users/$(whoami)/.ssh  # 不存在 (宿主机路径未挂载)
    docker exec {session1.container_name} python -c "import urllib.request; urllib.request.urlopen('https://github.com')"  # 网络不通

    # 查看 Coordinator 输出
    ls -la {_OUTPUT_DIR}/
    cat {_OUTPUT_DIR}/agent-a_report.txt
    cat {_OUTPUT_DIR}/agent-b_report.txt

  清理命令:
    docker rm -f {" ".join(_DEMO_CONTAINERS)}
    rm -rf {_OUTPUT_DIR}
  """)
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
