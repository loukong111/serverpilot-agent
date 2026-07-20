from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AstSymbol:
    kind: str
    name: str
    file: str = ""
    line: int = 0


@dataclass
class AstCall:
    callee: str
    file: str = ""
    line: int = 0


@dataclass
class AstFileResult:
    source_file: str
    success: bool
    duration_ms: int = 0
    ast_json_path: str = ""
    error: str = ""
    symbols: list[AstSymbol] = field(default_factory=list)
    calls: list[AstCall] = field(default_factory=list)


@dataclass
class AstAnalysisResult:
    project_path: str
    compile_commands_path: str = ""
    clang_binary: str = ""
    clang_found: bool = False
    command_count: int = 0
    analyzed_files: list[AstFileResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def find_compile_commands(project_path: Path, explicit_path: str | None = None) -> Path | None:
    if explicit_path:
        path = Path(explicit_path).resolve()
        return path if path.exists() else None
    candidates = [
        project_path / "compile_commands.json",
        project_path / "build" / "compile_commands.json",
        project_path / "build-debug" / "compile_commands.json",
        project_path / "build-release" / "compile_commands.json",
        project_path / "build-sanitize" / "compile_commands.json",
        project_path / "cmake-build-debug" / "compile_commands.json",
        project_path / "cmake-build-release" / "compile_commands.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_compile_commands(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def source_location(node: dict[str, Any]) -> tuple[str, int]:
    loc = node.get("loc") or {}
    if "spellingLoc" in loc:
        loc = loc["spellingLoc"]
    file_name = loc.get("file", "")
    line = loc.get("line", 0)
    if not file_name:
        begin = ((node.get("range") or {}).get("begin") or {})
        file_name = begin.get("file", "")
        line = begin.get("line", line)
    return file_name, int(line or 0)


def walk_ast(node: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(node, dict):
        result.append(node)
        for child in node.get("inner", []):
            result.extend(walk_ast(child))
    elif isinstance(node, list):
        for item in node:
            result.extend(walk_ast(item))
    return result


def parse_ast_json(source_file: str, ast_json: dict[str, Any], max_items: int = 120) -> tuple[list[AstSymbol], list[AstCall]]:
    symbols: list[AstSymbol] = []
    calls: list[AstCall] = []
    symbol_kinds = {
        "CXXRecordDecl",
        "RecordDecl",
        "ClassTemplateDecl",
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
    }
    for node in walk_ast(ast_json):
        kind = node.get("kind", "")
        if kind in symbol_kinds and node.get("name"):
            file_name, line = source_location(node)
            if not source_file or not file_name or file_name == source_file or str(file_name).endswith(Path(source_file).name):
                if len(symbols) < max_items:
                    symbols.append(AstSymbol(kind=kind, name=node["name"], file=file_name, line=line))
        if kind == "CallExpr":
            callee = ""
            referenced = node.get("referencedDecl") or {}
            if referenced.get("name"):
                callee = referenced["name"]
            if not callee:
                for child in node.get("inner", []):
                    referenced = child.get("referencedDecl") if isinstance(child, dict) else None
                    if referenced and referenced.get("name"):
                        callee = referenced["name"]
                        break
            if callee and len(calls) < max_items:
                file_name, line = source_location(node)
                calls.append(AstCall(callee=callee, file=file_name, line=line))
    return symbols, calls


def ast_command(entry: dict[str, Any], clang_binary: str) -> list[str]:
    original = shlex.split(entry.get("command", ""))
    source_file = entry.get("file", "")
    args: list[str] = [clang_binary]
    skip_next = False
    for index, token in enumerate(original[1:]):
        if skip_next:
            skip_next = False
            continue
        if token in {"-o", "-MF", "-MT", "-MQ"}:
            skip_next = True
            continue
        if token in {"-c", source_file}:
            continue
        if token.endswith(".o") or token.startswith("-MMD") or token.startswith("-MP"):
            continue
        if index == 0 and token.endswith(("g++", "c++", "clang++")):
            continue
        args.append(token)
    args.extend(["-Xclang", "-ast-dump=json", "-fsyntax-only", source_file])
    return args


def run_ast_for_entry(entry: dict[str, Any], clang_binary: str, dump_dir: Path, timeout: int) -> AstFileResult:
    source_file = entry.get("file", "")
    output_path = dump_dir / (Path(source_file).name + ".ast.json")
    command = ast_command(entry, clang_binary)
    start = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=entry.get("directory") or None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    duration_ms = int((time.monotonic() - start) * 1000)
    if completed.returncode != 0:
        return AstFileResult(
            source_file=source_file,
            success=False,
            duration_ms=duration_ms,
            error=completed.stderr[-4000:],
        )
    output_path.write_text(completed.stdout, encoding="utf-8")
    ast_json = json.loads(completed.stdout)
    symbols, calls = parse_ast_json(source_file, ast_json)
    return AstFileResult(
        source_file=source_file,
        success=True,
        duration_ms=duration_ms,
        ast_json_path=str(output_path),
        symbols=symbols,
        calls=calls,
    )


def analyze_ast(
    project_path: Path,
    compile_db: str | None,
    clang_binary: str,
    dump_dir: Path,
    max_files: int,
    timeout: int,
    ast_json_path: str | None = None,
) -> AstAnalysisResult:
    result = AstAnalysisResult(project_path=str(project_path))
    clang_path = shutil.which(clang_binary)
    result.clang_binary = clang_binary
    result.clang_found = bool(clang_path)

    compile_commands = find_compile_commands(project_path, compile_db)
    result.compile_commands_path = str(compile_commands or "")
    commands = load_compile_commands(compile_commands)
    result.command_count = len(commands)

    if ast_json_path:
        ast_path = Path(ast_json_path).resolve()
        ast_json = json.loads(ast_path.read_text(encoding="utf-8"))
        symbols, calls = parse_ast_json("", ast_json)
        result.analyzed_files.append(
            AstFileResult(
                source_file=str(ast_path),
                success=True,
                ast_json_path=str(ast_path),
                symbols=symbols,
                calls=calls,
            )
        )
        result.notes.append("Parsed existing AST JSON file.")
        return result

    if not result.clang_found:
        result.notes.append("clang++ was not found. Install clang or pass --clang-bin to enable AST dumping.")
        return result
    if not compile_commands:
        result.notes.append("compile_commands.json was not found. Configure the project with CMAKE_EXPORT_COMPILE_COMMANDS=ON.")
        return result

    dump_dir.mkdir(parents=True, exist_ok=True)
    for entry in commands[:max_files]:
        result.analyzed_files.append(run_ast_for_entry(entry, clang_path or clang_binary, dump_dir, timeout))
    return result


def ast_result_to_json(result: AstAnalysisResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2)


def generate_ast_report(result: AstAnalysisResult) -> str:
    success_files = [item for item in result.analyzed_files if item.success]
    failed_files = [item for item in result.analyzed_files if not item.success]
    symbol_count = sum(len(item.symbols) for item in success_files)
    call_count = sum(len(item.calls) for item in success_files)
    lines = [
        "# Clang AST 分析报告",
        "",
        f"- 项目路径：`{result.project_path}`",
        f"- compile_commands.json：`{result.compile_commands_path or '未找到'}`",
        f"- Clang：{'已找到' if result.clang_found else '未找到'} `{result.clang_binary}`",
        f"- 编译命令数量：{result.command_count}",
        f"- 成功分析文件：{len(success_files)}",
        f"- 失败文件：{len(failed_files)}",
        f"- AST 符号数量：{symbol_count}",
        f"- AST 调用数量：{call_count}",
        "",
    ]
    if result.notes:
        lines.extend(["## 说明", ""])
        lines.extend(f"- {note}" for note in result.notes)
        lines.append("")
    if success_files:
        lines.extend(["## 符号样本", ""])
        for file_result in success_files[:5]:
            lines.append(f"### {file_result.source_file}")
            lines.append("")
            for symbol in file_result.symbols[:12]:
                lines.append(f"- `{symbol.kind}` `{symbol.name}`：{symbol.file}:{symbol.line}")
            lines.append("")
    if failed_files:
        lines.extend(["## 失败文件", ""])
        for file_result in failed_files:
            lines.append(f"- `{file_result.source_file}`：{file_result.error[:300]}")
        lines.append("")
    return "\n".join(lines)
