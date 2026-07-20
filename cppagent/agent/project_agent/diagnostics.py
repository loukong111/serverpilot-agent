from __future__ import annotations

import json
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class DiagnosticStep:
    name: str
    command: list[str] = field(default_factory=list)
    success: bool = False
    skipped: bool = False
    exit_code: int | None = None
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    observation: str = ""


@dataclass
class DiagnosticResult:
    project_path: str
    build_dir: str
    steps: list[DiagnosticStep] = field(default_factory=list)
    stats_body: str = ""
    suggestions: list[str] = field(default_factory=list)


def command_from_string(command: str) -> list[str]:
    return shlex.split(command)


def trim_output(value: str, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n... output truncated ..."


def run_command_step(
    name: str,
    command: list[str],
    cwd: Path | None = None,
    timeout_seconds: int = 120,
) -> DiagnosticStep:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return DiagnosticStep(
            name=name,
            command=command,
            success=completed.returncode == 0,
            exit_code=completed.returncode,
            duration_ms=duration_ms,
            stdout=trim_output(completed.stdout),
            stderr=trim_output(completed.stderr),
            observation="command completed" if completed.returncode == 0 else "command failed",
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return DiagnosticStep(
            name=name,
            command=command,
            success=False,
            exit_code=None,
            duration_ms=duration_ms,
            stdout=trim_output(exc.stdout or ""),
            stderr=trim_output(exc.stderr or ""),
            observation=f"command timed out after {timeout_seconds}s",
        )


def skipped_step(name: str, reason: str) -> DiagnosticStep:
    return DiagnosticStep(name=name, skipped=True, success=True, observation=reason)


def fetch_stats(url: str, timeout_seconds: int = 5) -> DiagnosticStep:
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
        return DiagnosticStep(
            name="fetch_stats",
            command=["GET", url],
            success=True,
            duration_ms=int((time.monotonic() - start) * 1000),
            stdout=trim_output(body),
            observation=f"fetched {len(body)} bytes from stats endpoint",
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        return DiagnosticStep(
            name="fetch_stats",
            command=["GET", url],
            success=False,
            duration_ms=int((time.monotonic() - start) * 1000),
            stderr=str(exc),
            observation="failed to fetch stats endpoint",
        )


def run_service_workflow(
    start_command: str,
    project_path: Path,
    startup_seconds: float,
    benchmark_command: str | None,
    stats_url: str | None,
    timeout_seconds: int,
) -> list[DiagnosticStep]:
    steps: list[DiagnosticStep] = []
    command = command_from_string(start_command)
    start = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=str(project_path),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(startup_seconds)
    running = process.poll() is None
    steps.append(
        DiagnosticStep(
            name="start_service",
            command=command,
            success=running,
            exit_code=process.returncode,
            duration_ms=int((time.monotonic() - start) * 1000),
            observation="service is still running" if running else "service exited during startup",
        )
    )

    try:
        if running and stats_url:
            steps.append(fetch_stats(stats_url, timeout_seconds=min(timeout_seconds, 10)))
        elif stats_url:
            steps.append(skipped_step("fetch_stats", "service was not running"))

        if running and benchmark_command:
            steps.append(
                run_command_step(
                    "benchmark",
                    command_from_string(benchmark_command),
                    cwd=project_path,
                    timeout_seconds=timeout_seconds,
                )
            )
        elif benchmark_command:
            steps.append(skipped_step("benchmark", "service was not running"))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
        else:
            stdout, stderr = process.communicate(timeout=5)
        steps.append(
            DiagnosticStep(
                name="stop_service",
                command=["terminate", str(process.pid)],
                success=True,
                exit_code=process.returncode,
                stdout=trim_output(stdout or ""),
                stderr=trim_output(stderr or ""),
                observation="service process stopped",
            )
        )

    return steps


def build_suggestions(result: DiagnosticResult) -> list[str]:
    suggestions: list[str] = []
    failed = [step for step in result.steps if not step.success and not step.skipped]
    if not failed:
        suggestions.append("构建、测试和可选运行步骤没有发现失败，建议继续补充压测指标和长期运行日志。")
    for step in failed:
        if step.name == "configure":
            suggestions.append("CMake 配置失败，优先检查编译器、依赖包、CMake option 和平台限制。")
        elif step.name == "build":
            suggestions.append("构建失败，建议从第一个编译错误入手，确认 include 路径、链接库和 C++ 标准。")
        elif step.name == "test":
            suggestions.append("测试失败，建议查看 ctest 输出，区分单元测试失败、集成测试环境缺失和端口冲突。")
        elif step.name == "start_service":
            suggestions.append("服务启动失败，建议检查配置文件、端口占用、运行目录和启动参数。")
        elif step.name == "fetch_stats":
            suggestions.append("stats 拉取失败，建议确认服务是否暴露监控端口以及 URL 路径是否正确。")
        elif step.name == "benchmark":
            suggestions.append("压测命令失败，建议检查服务是否就绪、客户端参数和测试超时时间。")
    if not any(step.name == "benchmark" and step.success for step in result.steps):
        suggestions.append("当前未获得成功的压测结果，后续可以增加固定 benchmark 命令并记录 QPS/延迟。")
    if not any(step.name == "fetch_stats" and step.success for step in result.steps):
        suggestions.append("当前未获得 stats 输出，后续可以把可观测性数据纳入诊断报告。")
    return suggestions


def result_to_json(result: DiagnosticResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2)


def generate_diagnostic_report(result: DiagnosticResult) -> str:
    lines = [
        "# C++ 服务端项目诊断报告",
        "",
        f"- 项目路径：`{result.project_path}`",
        f"- 构建目录：`{result.build_dir}`",
        "",
        "## 执行步骤",
        "",
    ]
    for step in result.steps:
        status = "SKIPPED" if step.skipped else ("OK" if step.success else "FAILED")
        command = " ".join(shlex.quote(part) for part in step.command) if step.command else "-"
        lines.extend(
            [
                f"### {step.name}",
                "",
                f"- 状态：`{status}`",
                f"- 命令：`{command}`",
                f"- 退出码：`{step.exit_code if step.exit_code is not None else '-'}`",
                f"- 耗时：{step.duration_ms} ms",
                f"- 观察：{step.observation}",
                "",
            ]
        )
        if step.stdout.strip():
            lines.extend(["stdout 摘要：", "", "```text", step.stdout.strip(), "```", ""])
        if step.stderr.strip():
            lines.extend(["stderr 摘要：", "", "```text", step.stderr.strip(), "```", ""])

    lines.extend(["## 诊断建议", ""])
    lines.extend(f"- {suggestion}" for suggestion in result.suggestions)
    lines.append("")
    return "\n".join(lines)


def run_diagnostics(
    project_path: Path,
    build_dir: Path,
    cmake_args: list[str],
    build_args: list[str],
    ctest_args: list[str],
    skip_configure: bool,
    skip_build: bool,
    skip_test: bool,
    start_command: str | None,
    startup_seconds: float,
    benchmark_command: str | None,
    stats_url: str | None,
    timeout_seconds: int,
) -> DiagnosticResult:
    result = DiagnosticResult(project_path=str(project_path), build_dir=str(build_dir))
    build_dir.mkdir(parents=True, exist_ok=True)

    if skip_configure:
        result.steps.append(skipped_step("configure", "skipped by user"))
    else:
        configure_command = ["cmake", "-S", str(project_path), "-B", str(build_dir), *cmake_args]
        result.steps.append(run_command_step("configure", configure_command, timeout_seconds=timeout_seconds))

    if skip_build:
        result.steps.append(skipped_step("build", "skipped by user"))
    else:
        build_command = ["cmake", "--build", str(build_dir), *build_args]
        result.steps.append(run_command_step("build", build_command, timeout_seconds=timeout_seconds))

    if skip_test:
        result.steps.append(skipped_step("test", "skipped by user"))
    else:
        test_command = ["ctest", "--test-dir", str(build_dir), "--output-on-failure", *ctest_args]
        result.steps.append(run_command_step("test", test_command, timeout_seconds=timeout_seconds))

    if start_command:
        result.steps.extend(
            run_service_workflow(
                start_command=start_command,
                project_path=project_path,
                startup_seconds=startup_seconds,
                benchmark_command=benchmark_command,
                stats_url=stats_url,
                timeout_seconds=timeout_seconds,
            )
        )
    else:
        if stats_url:
            result.steps.append(fetch_stats(stats_url, timeout_seconds=min(timeout_seconds, 10)))
        if benchmark_command:
            result.steps.append(
                run_command_step(
                    "benchmark",
                    command_from_string(benchmark_command),
                    cwd=project_path,
                    timeout_seconds=timeout_seconds,
                )
            )

    for step in result.steps:
        if step.name == "fetch_stats" and step.success:
            result.stats_body = step.stdout
            break
    result.suggestions = build_suggestions(result)
    return result
