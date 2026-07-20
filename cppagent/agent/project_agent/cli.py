from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent.project_agent.clang_ast import (
    analyze_ast,
    ast_result_to_json,
    generate_ast_report,
)
from agent.project_agent.diagnostics import (
    generate_diagnostic_report,
    result_to_json,
    run_diagnostics,
)
from agent.project_agent.llm_client import (
    LLMConfigurationError,
    LLMRequestError,
    LLMClient,
    config_from_args,
)
from agent.project_agent.prompts import (
    build_interview_messages,
    build_report_messages,
    messages_to_text,
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_analyzer_path() -> Path:
    root = repo_root()
    candidates = [
        root / "build" / "cpp_analyzer",
        root / "cmake-build-debug" / "cpp_analyzer",
        root / "cmake-build-release" / "cpp_analyzer",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def build_analyzer(root: Path) -> None:
    build_dir = root / "build"
    build_dir.mkdir(exist_ok=True)
    subprocess.run(["cmake", "-S", str(root), "-B", str(build_dir)], check=True)
    subprocess.run(["cmake", "--build", str(build_dir)], check=True)


def run_analyzer(analyzer: Path, project_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(analyzer), "analyze", str(project_path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return json.loads(completed.stdout)


def join_or_dash(values: list[str]) -> str:
    return "、".join(values) if values else "暂未识别"


def checkbox(value: bool) -> str:
    return "有" if value else "无"


def module_summary(modules: list[dict[str, Any]]) -> str:
    if not modules:
        return "- 暂未识别出稳定模块，需要继续增加源码规则或接入 Clang AST。\n"
    lines = []
    for module in modules[:8]:
        name = module.get("name", "unknown")
        confidence = module.get("confidence", 0)
        files = join_or_dash(module.get("files", [])[:4])
        evidence = join_or_dash(module.get("evidence", [])[:6])
        lines.append(f"- `{name}`：置信度 {confidence:.2f}，证据 `{evidence}`，相关文件：{files}")
    return "\n".join(lines) + "\n"


def clang_summary(clang: dict[str, Any]) -> str:
    if not clang:
        return "- 暂无 Clang 增强分析数据。"
    lines = [
        f"- compile_commands.json：{checkbox(bool(clang.get('compile_commands_found')))}",
        f"- 编译命令数量：{clang.get('command_count', 0)}",
        f"- 类/结构体样本数量：{len(clang.get('classes', []))}",
        f"- 函数样本数量：{len(clang.get('functions', []))}",
        f"- 调用线索数量：{len(clang.get('calls', []))}",
        f"- 模块依赖边数量：{len(clang.get('module_dependencies', []))}",
    ]
    classes = clang.get("classes", [])[:8]
    if classes:
        lines.extend(["", "类/结构体样本："])
        lines.extend(
            f"- `{item.get('kind', 'class')} {item.get('name', '')}`：{item.get('file', '')}:{item.get('line', 0)}"
            for item in classes
        )
    functions = clang.get("functions", [])[:8]
    if functions:
        lines.extend(["", "函数样本："])
        lines.extend(
            f"- `{item.get('name', '')}`：{item.get('file', '')}:{item.get('line', 0)}"
            for item in functions
        )
    deps = clang.get("module_dependencies", [])[:10]
    if deps:
        lines.extend(["", "模块依赖图样本："])
        lines.extend(
            f"- `{item.get('from', '')}` -> `{item.get('to', '')}`：{item.get('type', '')}，weight={item.get('weight', 0)}，evidence=`{item.get('evidence', '')}`"
            for item in deps
        )
    return "\n".join(lines)


def build_interview_scripts(data: dict[str, Any]) -> dict[str, str]:
    project_name = data.get("project_name", "UnknownProject")
    modules = data.get("modules", [])
    top_modules = [module.get("name", "") for module in modules[:5]]
    module_words = "、".join([item for item in top_modules if item]) or "核心业务模块"
    entry_points = join_or_dash(data.get("entry_points", []))
    cmake = data.get("cmake", {})
    clang = data.get("clang", {})
    cpp_standard = cmake.get("cpp_standard") or "暂未识别"
    executables = join_or_dash(cmake.get("executables", []))
    clang_sentence = (
        f"另外 analyzer 识别到了 compile_commands.json，并提取了 {len(clang.get('classes', []))} 个类/结构体样本、"
        f"{len(clang.get('functions', []))} 个函数样本和 {len(clang.get('module_dependencies', []))} 条模块依赖边，"
        "这说明项目分析已经不只是目录扫描，而是开始进入代码结构层面。"
        if clang.get("compile_commands_found")
        else "后续如果生成 compile_commands.json，还可以接入 Clang AST，把分析从关键词规则提升到类、函数和调用关系级别。"
    )

    return {
        "30s": (
            f"{project_name} 是一个 C++ 服务端项目，我会把它概括成一个具备工程结构的后端系统，而不是单个算法 demo。"
            f"当前可以从构建系统、目录结构和核心模块三点来讲：项目使用 CMake，C++ 标准是 {cpp_standard}，"
            f"入口文件包括 {entry_points}，核心模块可以围绕 {module_words} 展开。"
            "面试里我会重点强调自己理解了一个服务端项目从入口、模块拆分到测试验证的完整链路。"
        ),
        "1min": (
            f"{project_name} 的分析可以先从工程入口讲起。它使用 CMake 管理构建，可执行目标包括 {executables}，"
            f"源码结构里有 src、include、tests、docs 等目录，说明它已经具备比较完整的工程组织方式。"
            f"从模块上看，analyzer 识别到 {module_words}，我会把这些模块串成一条主线：入口文件负责启动和配置，"
            "网络或服务层接收请求，协议或命令层把请求转成内部操作，再由存储、持久化、集群、指标等模块支撑核心能力。"
            "这个讲法的重点不是说项目功能多，而是说明我能从构建、模块边界、测试和可维护性角度理解一个 C++ 服务端工程。"
        ),
        "2min": (
            f"我会用两分钟把 {project_name} 讲成一个完整的 C++ 服务端工程。第一部分讲工程结构：项目使用 CMake 构建，"
            f"C++ 标准是 {cpp_standard}，入口文件包括 {entry_points}，并且有 src、include、tests、docs 这些目录，"
            "这说明项目不是临时 demo，而是按可维护工程来组织。第二部分讲架构主线：根据 analyzer 的结果，"
            f"核心模块可以围绕 {module_words} 来展开。我的讲法会是，请求或任务先从入口进入系统，随后经过服务/网络层，"
            "再进入协议解析或命令分发层，最终落到存储、持久化、集群或指标模块。这样讲能体现我理解的是模块协作，而不是孤立函数。"
            f"第三部分讲工程亮点：CMake target、测试入口、目录分层和模块依赖都可以作为项目成熟度的证据。{clang_sentence}"
            "最后我会主动讲不足和改进方向，例如继续补充边界测试、压测结果、错误恢复、长期运行日志，以及用真正 Clang AST 提升分析精度。"
            "这样回答既能展示 C++ 服务端能力，也能展示工程诊断和持续改进意识。"
        ),
    }


def generate_report(data: dict[str, Any]) -> str:
    project_name = data.get("project_name", "UnknownProject")
    directories = data.get("directories", {})
    files = data.get("files", {})
    cmake = data.get("cmake", {})
    modules = data.get("modules", [])
    strengths = data.get("strengths", [])
    risks = data.get("risks", [])
    clang = data.get("clang", {})

    lines = [
        f"# {project_name} 项目分析报告",
        "",
        "## 项目总览",
        "",
        f"- 项目路径：`{data.get('root', '')}`",
        f"- README：{checkbox(bool(data.get('has_readme')))}",
        f"- CMake：{checkbox(bool(data.get('has_cmake')))}",
        f"- 目录结构：src={checkbox(directories.get('src', False))}，include={checkbox(directories.get('include', False))}，tests={checkbox(directories.get('tests', False))}，docs={checkbox(directories.get('docs', False))}",
        f"- 源码统计：源文件 {files.get('source_count', 0)} 个，头文件 {files.get('header_count', 0)} 个，测试相关文件 {files.get('test_count', 0)} 个",
        "",
        "## 构建系统",
        "",
        f"- C++ 标准：`{cmake.get('cpp_standard') or '暂未识别'}`",
        f"- 可执行目标：{join_or_dash(cmake.get('executables', []))}",
        f"- 库目标：{join_or_dash(cmake.get('libraries', []))}",
        f"- 测试目标：{join_or_dash(cmake.get('tests', []))}",
        f"- 依赖包：{join_or_dash(cmake.get('packages', []))}",
        "",
        "## 核心模块",
        "",
        module_summary(modules).rstrip(),
        "",
        "## Clang 增强分析",
        "",
        clang_summary(clang),
        "",
        "## 技术亮点",
        "",
    ]

    if strengths:
        lines.extend(f"- {item}" for item in strengths)
    else:
        lines.append("- 暂未生成亮点，需要补充规则或人工复核。")

    lines.extend(["", "## 潜在问题", ""])
    if risks:
        lines.extend(f"- [{risk.get('type', 'risk')}] {risk.get('message', '')}" for risk in risks)
    else:
        lines.append("- 暂未识别明显风险，但仍建议人工复核错误处理、边界测试和并发安全。")

    interview_scripts = build_interview_scripts(data)
    lines.extend(
        [
            "",
            "## 面试讲法",
            "",
            "### 30 秒版本",
            "",
            interview_scripts["30s"],
            "",
            "### 1 分钟版本",
            "",
            interview_scripts["1min"],
            "",
            "### 2 分钟版本",
            "",
            interview_scripts["2min"],
            "",
            "## 推荐追问",
            "",
            "- 这个项目的主流程从入口文件到核心模块是怎么走的？",
            "- 哪个模块最能体现你的 C++ 能力？",
            "- 如果要提升稳定性，你会先补测试、日志、监控还是错误处理？",
            "- 如果接入 Clang AST，你希望比当前规则分析多得到哪些事实？",
            "",
        ]
    )
    return "\n".join(lines)


def generate_offline_interview_answer(data: dict[str, Any], question: str) -> str:
    project_name = data.get("project_name", "这个项目")
    modules = [module.get("name", "") for module in data.get("modules", [])[:5]]
    module_words = "、".join([item for item in modules if item]) or "核心模块"
    strengths = data.get("strengths", [])
    risks = data.get("risks", [])
    first_strength = strengths[0] if strengths else "项目具备可分析的 C++ 工程结构。"
    first_risk = risks[0].get("message", "仍需要人工复核边界测试、错误处理和并发安全。") if risks else "仍需要人工复核边界测试、错误处理和并发安全。"

    return "\n".join(
        [
            f"# 面试问答：{project_name}",
            "",
            f"**问题：** {question}",
            "",
            f"我的回答会围绕 `{project_name}` 的工程结构和模块拆分展开。根据 analyzer 的结果，这个项目可以重点讲 {module_words}。{first_strength} 同时我也会主动说明不足：{first_risk}",
            "",
            "## 可以展开的技术点",
            "",
            f"- 模块协作：围绕 {module_words} 说明主流程如何从入口进入核心模块。",
            "- 工程能力：结合 CMake、src/include/tests/docs 说明项目不是单文件 demo，而是完整工程。",
            "- 改进意识：主动提到测试覆盖、错误处理、并发边界或可观测性，体现对服务端稳定性的理解。",
            "",
            "## 继续追问时可以补充",
            "",
            "如果继续追问，我会先承认当前 analyzer 是基于结构和关键词的静态分析，再说明下一步会接入 Clang AST，提取类、函数和调用关系，让项目讲解从“模块识别”升级到“代码级调用链分析”。",
            "",
        ]
    )


def maybe_generate_with_llm(args: argparse.Namespace, messages: list[dict[str, str]]) -> str:
    if args.save_prompt:
        prompt_path = Path(args.save_prompt).resolve()
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(messages_to_text(messages), encoding="utf-8")

    client = LLMClient(config_from_args(args))
    return client.chat(messages)


def command_analyze(args: argparse.Namespace) -> int:
    root = repo_root()
    project_path = Path(args.project_path).resolve()
    analyzer = Path(args.analyzer).resolve() if args.analyzer else default_analyzer_path()

    if args.build or not analyzer.exists():
        build_analyzer(root)
        analyzer = default_analyzer_path()

    data = run_analyzer(analyzer, project_path)
    output = Path(args.output).resolve() if args.output else root / "reports" / f"{data.get('project_name', 'analysis')}_analysis.md"
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.llm:
        try:
            report = maybe_generate_with_llm(args, build_report_messages(data))
        except (LLMConfigurationError, LLMRequestError) as exc:
            print(f"LLM report failed: {exc}", file=sys.stderr)
            return 5
    else:
        report = generate_report(data)
    output.write_text(report, encoding="utf-8")

    json_output = Path(args.json_output).resolve() if args.json_output else output.with_suffix(".json")
    json_output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Markdown report: {output}")
    print(f"Analysis JSON: {json_output}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
    root = repo_root()
    project_path = Path(args.project_path).resolve()
    analyzer = Path(args.analyzer).resolve() if args.analyzer else default_analyzer_path()

    if args.build or not analyzer.exists():
        build_analyzer(root)
        analyzer = default_analyzer_path()

    data = run_analyzer(analyzer, project_path)
    if args.llm:
        try:
            answer = maybe_generate_with_llm(args, build_interview_messages(data, args.question))
        except (LLMConfigurationError, LLMRequestError) as exc:
            print(f"LLM answer failed: {exc}", file=sys.stderr)
            return 5
    else:
        answer = generate_offline_interview_answer(data, args.question)

    if args.output:
        output = Path(args.output).resolve()
    else:
        output = root / "reports" / f"{data.get('project_name', 'analysis')}_interview_answer.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(answer, encoding="utf-8")

    print(answer)
    print(f"\nInterview answer: {output}")
    return 0


def command_diagnose(args: argparse.Namespace) -> int:
    root = repo_root()
    project_path = Path(args.project_path).resolve()
    build_dir = (
        Path(args.build_dir).resolve()
        if args.build_dir
        else root / "reports" / "diagnostics" / "builds" / project_path.name
    )
    report_path = (
        Path(args.output).resolve()
        if args.output
        else root / "reports" / "diagnostics" / f"{project_path.name}_diagnostic.md"
    )
    json_path = (
        Path(args.json_output).resolve()
        if args.json_output
        else report_path.with_suffix(".json")
    )

    result = run_diagnostics(
        project_path=project_path,
        build_dir=build_dir,
        cmake_args=args.cmake_arg,
        build_args=args.build_arg,
        ctest_args=args.ctest_arg,
        skip_configure=args.skip_configure,
        skip_build=args.skip_build,
        skip_test=args.skip_test,
        start_command=args.start_command,
        startup_seconds=args.startup_seconds,
        benchmark_command=args.benchmark_command,
        stats_url=args.stats_url,
        timeout_seconds=args.timeout,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(generate_diagnostic_report(result), encoding="utf-8")
    json_path.write_text(result_to_json(result), encoding="utf-8")

    failed = [step for step in result.steps if not step.success and not step.skipped]
    print(f"Diagnostic report: {report_path}")
    print(f"Diagnostic JSON: {json_path}")
    print(f"Failed steps: {len(failed)}")
    return 1 if failed and args.fail_on_error else 0


def command_ast(args: argparse.Namespace) -> int:
    root = repo_root()
    project_path = Path(args.project_path).resolve()
    report_path = (
        Path(args.output).resolve()
        if args.output
        else root / "reports" / "ast" / f"{project_path.name}_ast.md"
    )
    json_path = (
        Path(args.json_output).resolve()
        if args.json_output
        else report_path.with_suffix(".json")
    )
    dump_dir = (
        Path(args.dump_dir).resolve()
        if args.dump_dir
        else root / "reports" / "ast" / "dumps" / project_path.name
    )

    result = analyze_ast(
        project_path=project_path,
        compile_db=args.compile_db,
        clang_binary=args.clang_bin,
        dump_dir=dump_dir,
        max_files=args.max_files,
        timeout=args.timeout,
        ast_json_path=args.ast_json,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(generate_ast_report(result), encoding="utf-8")
    json_path.write_text(ast_result_to_json(result), encoding="utf-8")

    print(f"AST report: {report_path}")
    print(f"AST JSON: {json_path}")
    if not result.clang_found and not args.ast_json:
        print("clang++ not found; wrote diagnostic report without AST dump.")
        return 1 if args.fail_on_error else 0
    failed = [item for item in result.analyzed_files if not item.success]
    return 1 if failed and args.fail_on_error else 0


def add_llm_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--llm", action="store_true", help="Use an OpenAI-compatible LLM instead of offline templates")
    parser.add_argument("--model", help="LLM model name. Can also use PROJECTAGENTCPP_MODEL or OPENAI_MODEL")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL. Defaults to OPENAI_BASE_URL or https://api.openai.com/v1")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable that stores the API key")
    parser.add_argument("--save-prompt", help="Write the rendered prompt to a file before calling the LLM")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C++ project analysis agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a C++ project and generate a report")
    analyze.add_argument("project_path", help="Path to the C++ project")
    analyze.add_argument("--analyzer", help="Path to cpp_analyzer executable")
    analyze.add_argument("--output", help="Markdown report path")
    analyze.add_argument("--json-output", help="JSON result path")
    analyze.add_argument("--build", action="store_true", help="Configure and build the C++ analyzer first")
    add_llm_options(analyze)
    analyze.set_defaults(func=command_analyze)

    ask = subparsers.add_parser("ask", help="Answer an interview question about a C++ project")
    ask.add_argument("project_path", help="Path to the C++ project")
    ask.add_argument("--question", required=True, help="Interview question to answer")
    ask.add_argument("--analyzer", help="Path to cpp_analyzer executable")
    ask.add_argument("--output", help="Markdown answer path")
    ask.add_argument("--build", action="store_true", help="Configure and build the C++ analyzer first")
    add_llm_options(ask)
    ask.set_defaults(func=command_ask)

    diagnose = subparsers.add_parser("diagnose", help="Build, test, and optionally run a C++ service project")
    diagnose.add_argument("project_path", help="Path to the C++ project")
    diagnose.add_argument("--build-dir", help="Out-of-source CMake build directory")
    diagnose.add_argument("--output", help="Markdown diagnostic report path")
    diagnose.add_argument("--json-output", help="JSON diagnostic result path")
    diagnose.add_argument("--cmake-arg", action="append", default=[], help="Extra argument passed to CMake configure. Repeatable")
    diagnose.add_argument("--build-arg", action="append", default=[], help="Extra argument passed to cmake --build. Repeatable")
    diagnose.add_argument("--ctest-arg", action="append", default=[], help="Extra argument passed to ctest. Repeatable")
    diagnose.add_argument("--skip-configure", action="store_true", help="Skip CMake configure")
    diagnose.add_argument("--skip-build", action="store_true", help="Skip CMake build")
    diagnose.add_argument("--skip-test", action="store_true", help="Skip ctest")
    diagnose.add_argument("--start-command", help="Command used to start the service from the project root")
    diagnose.add_argument("--startup-seconds", type=float, default=1.0, help="Seconds to wait after starting the service")
    diagnose.add_argument("--benchmark-command", help="Benchmark command to run from the project root")
    diagnose.add_argument("--stats-url", help="HTTP stats endpoint to fetch")
    diagnose.add_argument("--timeout", type=int, default=120, help="Timeout in seconds for each command")
    diagnose.add_argument("--fail-on-error", action="store_true", help="Return non-zero if any diagnostic step fails")
    diagnose.set_defaults(func=command_diagnose)

    ast = subparsers.add_parser("ast", help="Run optional Clang AST analysis using compile_commands.json")
    ast.add_argument("project_path", help="Path to the C++ project")
    ast.add_argument("--compile-db", help="Explicit compile_commands.json path")
    ast.add_argument("--clang-bin", default="clang++", help="Clang++ binary used for AST dumping")
    ast.add_argument("--max-files", type=int, default=3, help="Maximum compile commands to AST-dump")
    ast.add_argument("--timeout", type=int, default=60, help="Timeout in seconds for each AST dump")
    ast.add_argument("--dump-dir", help="Directory for raw AST JSON dumps")
    ast.add_argument("--ast-json", help="Parse an existing clang -ast-dump=json file instead of invoking clang")
    ast.add_argument("--output", help="Markdown AST report path")
    ast.add_argument("--json-output", help="JSON AST result path")
    ast.add_argument("--fail-on-error", action="store_true", help="Return non-zero if clang is missing or any AST file fails")
    ast.set_defaults(func=command_ast)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
