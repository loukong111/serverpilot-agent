from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "node_modules",
    "out",
    "reports",
    "vcpkg_installed",
    "_deps",
}
IGNORED_DIRECTORY_PREFIXES = ("build-", "cmake-build-")
TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cmake",
    ".conf",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".in",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}
SPECIAL_TEXT_FILES = {"CMakeLists.txt", "Makefile", "Dockerfile"}
MAX_FILE_BYTES = 256 * 1024
MAX_CONTEXT_BYTES = 80 * 1024
MAX_PATCH_BYTES = 200 * 1024
MAX_PATCH_FILES = 12
MAX_EDIT_LINES = 200
_MUTATION_LOCK = threading.Lock()


class CodingAgentError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_text_file(path: Path) -> bool:
    return path.name in SPECIAL_TEXT_FILES or path.suffix.lower() in TEXT_SUFFIXES


def is_ignored_directory(name: str) -> bool:
    return name in IGNORED_DIRECTORIES or name.startswith(IGNORED_DIRECTORY_PREFIXES)


def _iter_project_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if not is_ignored_directory(name))
        for name in sorted(names):
            path = Path(current) / name
            if not _is_text_file(path):
                continue
            try:
                if path.stat().st_size <= MAX_FILE_BYTES:
                    files.append(path)
            except OSError:
                continue
    return files


def _search_tokens(query: str) -> list[str]:
    ascii_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_:-]{1,}", query)
    domain_terms = {
        "配置": ("config", "configuration"),
        "解析": ("parse", "parser"),
        "端口": ("port",),
        "范围": ("range", "minimum", "maximum"),
        "校验": ("validate", "validation", "check"),
        "命令": ("command",),
        "网络": ("network", "socket"),
        "并发": ("concurrency", "thread"),
        "日志": ("log", "logger"),
        "缓存": ("cache",),
        "持久化": ("persistence",),
        "集群": ("cluster",),
        "指标": ("metric", "stats"),
        "超时": ("timeout",),
        "重试": ("retry",),
        "错误": ("error",),
        "内存": ("memory",),
        "单元测试": ("test", "tests"),
    }
    generic_words = {
        "项目",
        "代码",
        "功能",
        "增加",
        "添加",
        "修改",
        "实现",
        "修复",
        "一个",
        "进行",
        "补充",
        "小型",
        "对应",
        "单元测试",
        "测试",
        "帮我",
        "请",
        "为",
        "并",
    }
    splitter = re.compile("|".join(re.escape(word) for word in sorted(generic_words, key=len, reverse=True)))
    chinese_tokens: list[str] = []
    for sequence in re.findall(r"[\u4e00-\u9fff]+", query):
        chinese_tokens.extend(part for part in splitter.split(sequence) if 2 <= len(part) <= 12)
    translated_tokens = [
        token
        for term, translations in domain_terms.items()
        if term in query
        for token in (term, *translations)
    ]
    tokens = ascii_tokens + chinese_tokens + translated_tokens
    ignored = {word.lower() for word in generic_words}
    unique: list[str] = []
    for token in tokens:
        normalized = token.lower()
        if normalized in ignored or normalized in unique:
            continue
        unique.append(normalized)
    return unique[:32]


def search_code(project_path: Path, query: str, max_results: int = 40) -> list[dict[str, Any]]:
    root = project_path.resolve()
    tokens = _search_tokens(query)
    if not tokens:
        return []

    matches: list[dict[str, Any]] = []
    for path in _iter_project_files(root):
        relative = path.relative_to(root).as_posix()
        path_tokens = [token for token in tokens if token in relative.lower()]
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        file_had_line_match = False
        for line_number, line in enumerate(lines, 1):
            lowered = line.lower()
            line_tokens = [token for token in tokens if token in lowered]
            if not line_tokens:
                continue
            file_had_line_match = True
            matched = list(dict.fromkeys(line_tokens + path_tokens))
            matches.append(
                {
                    "path": relative,
                    "line": line_number,
                    "text": line.strip()[:300],
                    "score": len(line_tokens) + min(2, len(path_tokens)),
                    "matched": matched,
                }
            )
        if path_tokens and not file_had_line_match:
            matches.append(
                {
                    "path": relative,
                    "line": 1,
                    "text": lines[0].strip()[:300] if lines else "",
                    "score": len(path_tokens),
                    "matched": path_tokens,
                }
            )

    file_tokens: dict[str, set[str]] = {}
    for item in matches:
        file_tokens.setdefault(item["path"], set()).update(item["matched"])
    for item in matches:
        item["file_score"] = len(file_tokens[item["path"]])

    def match_priority(item: dict[str, Any]) -> tuple[int, int, int, int, str, int]:
        path = Path(item["path"])
        source_rank, _relative = _context_path_priority(path, Path("."))
        generated_or_auxiliary_rank = 1 if source_rank >= 4 else 0
        return (
            generated_or_auxiliary_rank,
            -item["file_score"],
            source_rank,
            -item["score"],
            item["path"],
            item["line"],
        )

    matches.sort(key=match_priority)
    selected: list[dict[str, Any]] = []
    per_file: dict[str, int] = {}
    selected_lines: dict[str, list[int]] = {}
    for item in matches:
        if per_file.get(item["path"], 0) >= 5:
            continue
        if any(abs(item["line"] - line) < 10 for line in selected_lines.get(item["path"], [])):
            continue
        selected.append(item)
        per_file[item["path"]] = per_file.get(item["path"], 0) + 1
        selected_lines.setdefault(item["path"], []).append(item["line"])
        if len(selected) >= max_results:
            break
    return selected


def _context_path_priority(path: Path, root: Path) -> tuple[int, str]:
    relative = path.relative_to(root).as_posix()
    parts = path.relative_to(root).parts
    suffix = path.suffix.lower()
    if relative == "main.cpp":
        return 0, relative
    if parts and parts[0] in {"src", "include"} and suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}:
        return 1, relative
    if parts and parts[0] in {"test", "tests"}:
        return 2, relative
    if path.name == "CMakeLists.txt" or suffix == ".cmake":
        return 3, relative
    if suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}:
        return 4, relative
    if suffix in {".conf", ".ini", ".json", ".yaml", ".yml"}:
        return 5, relative
    if path.name == "README.md" or suffix == ".md":
        return 7, relative
    return 6, relative


def _truncate_utf8(content: str, max_bytes: int) -> str:
    return content.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _relevant_file_content(content: str, line_numbers: list[int], max_bytes: int) -> str:
    if len(content.encode("utf-8")) <= max_bytes:
        return content
    if not line_numbers:
        return _truncate_utf8(content, max_bytes)

    lines = content.splitlines(keepends=True)
    ranges: list[tuple[int, int]] = []
    for line_number in line_numbers[:10]:
        start = max(0, line_number - 11)
        end = min(len(lines), line_number + 10)
        ranges.append((start, end))
    ranges.sort()
    merged_ranges: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged_ranges and start <= merged_ranges[-1][1]:
            merged_ranges[-1] = (merged_ranges[-1][0], max(merged_ranges[-1][1], end))
        else:
            merged_ranges.append((start, end))

    pieces: list[str] = []
    used_bytes = 0
    for start, end in merged_ranges:
        marker = f"<<< EXCERPT lines {start + 1}-{end} >>>\n"
        segment = marker + "".join(lines[start:end])
        remaining = max_bytes - used_bytes
        if remaining <= len(marker.encode("utf-8")):
            break
        if len(segment.encode("utf-8")) > remaining:
            segment = marker + _truncate_utf8("".join(lines[start:end]), remaining - len(marker.encode("utf-8")))
        pieces.append(segment)
        used_bytes += len(segment.encode("utf-8"))
        if used_bytes >= max_bytes:
            break
    if not pieces:
        return _truncate_utf8(content, max_bytes)
    return _truncate_utf8("\n".join(pieces), max_bytes)


def collect_code_context(
    project_path: Path,
    task: str,
    max_files: int = 12,
    max_context_bytes: int = MAX_CONTEXT_BYTES,
) -> dict[str, Any]:
    root = project_path.resolve()
    matches = search_code(root, task)
    ranked_paths: list[str] = []
    matched_lines: dict[str, list[int]] = {}

    for match in matches:
        matched_lines.setdefault(match["path"], []).append(int(match["line"]))
        if match["path"] not in ranked_paths:
            ranked_paths.append(match["path"])
    if len(ranked_paths) < max_files:
        for path in sorted(_iter_project_files(root), key=lambda item: _context_path_priority(item, root)):
            relative = path.relative_to(root).as_posix()
            if relative not in ranked_paths:
                ranked_paths.append(relative)
            if len(ranked_paths) >= max_files:
                break

    context_files: list[dict[str, str]] = []
    used_bytes = 0
    selected_paths = ranked_paths[:max_files]
    for index, relative in enumerate(selected_paths):
        path = _safe_target(root, relative)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        remaining = max(0, max_context_bytes - used_bytes)
        if remaining < 1024:
            break
        remaining_files = len(selected_paths) - index
        file_budget = max(1024, remaining // max(1, remaining_files))
        content = _relevant_file_content(
            content,
            matched_lines.get(relative, []),
            min(file_budget, remaining),
        )
        encoded_size = len(content.encode("utf-8"))
        context_files.append({"path": relative, "content": content})
        used_bytes += encoded_size
        if used_bytes >= max_context_bytes:
            break

    return {
        "matches": matches,
        "files": context_files,
        "context_bytes": used_bytes,
    }


def build_coding_messages(
    task: str,
    analysis: dict[str, Any],
    context: dict[str, Any],
    *,
    use_edits: bool = False,
) -> list[dict[str, str]]:
    file_blocks = []
    for item in context.get("files", []):
        content = item["content"]
        file_header = f"===== FILE: {item['path']} ====="
        if use_edits:
            line_count = len(content.splitlines())
            content = "\n".join(
                f"{line_number}|{line}"
                for line_number, line in enumerate(content.splitlines(), 1)
            )
            file_header = (
                f"===== FILE: {item['path']} "
                f"(总行数 {line_count}，末尾追加行号 {line_count + 1}) ====="
            )
        file_blocks.append(f"{file_header}\n{content}")
    compact_modules = []
    for module in analysis.get("modules", [])[:8]:
        if not isinstance(module, dict):
            continue
        compact_modules.append(
            {
                "name": module.get("name"),
                "confidence": module.get("confidence"),
                "files": module.get("files", [])[:8],
                "evidence": module.get("evidence", [])[:3],
            }
        )
    compact_analysis = {
        key: analysis.get(key)
        for key in ("project_name", "directories", "files", "cmake", "entry_points")
        if key in analysis
    }
    compact_analysis["modules"] = compact_modules
    compact_analysis["strengths"] = analysis.get("strengths", [])[:5]
    compact_analysis["risks"] = analysis.get("risks", [])[:5]
    project_facts = json.dumps(compact_analysis, ensure_ascii=False, indent=2)
    source_context = "\n\n".join(file_blocks)
    if use_edits:
        output_instructions = (
            "返回格式：\n"
            "{\n"
            '  "summary": "修改摘要",\n'
            '  "plan": ["步骤 1", "步骤 2"],\n'
            '  "risks": ["风险或注意点"],\n'
            '  "tests": ["建议执行的测试"],\n'
            '  "edits": [{"path": "相对路径", "start_line": 1, "end_line": 1, "replacement": "替换后的完整文本"}]\n'
            "}\n"
            "start_line 和 end_line 是源码上下文中显示的行号，表示需要替换的闭区间。"
            "如需在文件末尾追加，两个行号都填写最后一行加 1；此时不会替换已有行。"
            "行号始终以原始源码为准，不要按修改后的内容重新编号，也不要填写更大的虚拟行号。"
            "选择能够完整表达修改的最小语法范围，replacement 不要包含行号，并保留正确缩进。"
            "不要返回 patch，不要修改 build、.git、reports 等生成目录。"
        )
    else:
        output_instructions = (
            "返回格式：\n"
            "{\n"
            '  "summary": "修改摘要",\n'
            '  "plan": ["步骤 1", "步骤 2"],\n'
            '  "risks": ["风险或注意点"],\n'
            '  "tests": ["建议执行的测试"],\n'
            '  "patch": "标准 unified diff"\n'
            "}\n"
            "patch 必须使用 git diff 格式，以 diff --git a/... b/... 开头，路径必须相对项目根目录。"
            "修改已有文件时必须提供准确上下文；不要修改 build、.git、reports 等生成目录。"
        )
    return [
        {
            "role": "system",
            "content": (
                "你是面向 C++ 服务端项目的 Coding Agent。只能依据给出的项目事实和源码上下文提出修改。"
                "源码上下文中的 EXCERPT 标记只是截断提示，并非源文件内容，禁止把它写入补丁。"
                "保持修改范围小，遵循现有代码风格，并优先补充测试。输出必须是单个 JSON 对象，不要使用 Markdown code fence。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"任务：\n{task}\n\n"
                f"项目事实：\n{project_facts}\n\n"
                f"源码上下文：\n{source_context}\n\n"
                f"{output_instructions}"
            ),
        },
    ]


def diagnostic_failure_summary(diagnostic: dict[str, Any], max_chars: int = 16000) -> str:
    sections: list[str] = []
    for step in diagnostic.get("steps", []):
        if step.get("success") or step.get("skipped"):
            continue
        command = " ".join(str(part) for part in step.get("command", []))
        sections.extend(
            [
                f"STEP: {step.get('name', 'unknown')}",
                f"COMMAND: {command}",
                f"EXIT_CODE: {step.get('exit_code')}",
                f"OBSERVATION: {step.get('observation', '')}",
                "STDOUT:",
                str(step.get("stdout") or "").strip(),
                "STDERR:",
                str(step.get("stderr") or "").strip(),
            ]
        )
    summary = "\n".join(sections).strip()
    if not summary:
        raise CodingAgentError("诊断结果中没有可用于修复的失败步骤。")
    if len(summary) > max_chars:
        return summary[:max_chars] + "\n... diagnostic output truncated ..."
    return summary


def build_repair_messages(
    task: str,
    analysis: dict[str, Any],
    context: dict[str, Any],
    diagnostic: dict[str, Any],
    parent: dict[str, Any],
    *,
    use_edits: bool = False,
) -> list[dict[str, str]]:
    messages = build_coding_messages(task, analysis, context, use_edits=use_edits)
    messages[0]["content"] = (
        "你是面向 C++ 服务端项目的 Coding Agent。上一轮补丁已经应用，但构建或测试失败。"
        "根据真实诊断输出定位首个根因，生成最小修复补丁，不要撤销无关变更。"
        "输出必须是单个 JSON 对象，不要使用 Markdown code fence。"
    )
    parent_summary = {
        "id": parent.get("id"),
        "round": parent.get("round", 1),
        "summary": parent.get("summary"),
        "files": parent.get("files", []),
        "patch": str(parent.get("patch") or "")[:30000],
    }
    messages[1]["content"] += (
        "\n\n上一轮修改：\n"
        + json.dumps(parent_summary, ensure_ascii=False, indent=2)
        + "\n\n失败诊断：\n"
        + diagnostic_failure_summary(diagnostic)
        + "\n\n只修复诊断暴露的根因。tests 字段应列出能够验证本轮修复的命令。"
    )
    return messages


def _parse_coding_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise CodingAgentError("LLM 没有返回有效的 JSON 修改方案。") from exc
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as nested_exc:
            raise CodingAgentError("LLM 返回的修改方案无法解析为 JSON。") from nested_exc
    if not isinstance(data, dict):
        raise CodingAgentError("LLM 修改方案必须是 JSON 对象。")
    return data


def _proposal_from_data(data: dict[str, Any], patch: str) -> dict[str, Any]:
    return {
        "summary": str(data.get("summary") or "代码修改方案"),
        "plan": [str(item) for item in data.get("plan", []) if str(item).strip()],
        "risks": [str(item) for item in data.get("risks", []) if str(item).strip()],
        "tests": [str(item) for item in data.get("tests", []) if str(item).strip()],
        "patch": patch,
    }


def parse_coding_response(content: str, project_path: Path | None = None) -> dict[str, Any]:
    data = _parse_coding_object(content)
    patch = data.get("patch")
    if not isinstance(patch, str) or not patch.strip():
        raise CodingAgentError("LLM 修改方案中缺少 unified diff。")
    return _proposal_from_data(data, normalize_unified_diff(patch, project_path))


def parse_coding_edits(content: str, project_path: Path) -> dict[str, Any]:
    data = _parse_coding_object(content)
    edits = data.get("edits")
    if not isinstance(edits, list) or not edits:
        raise CodingAgentError("LLM 修改方案中缺少结构化 edits。")
    if len(edits) > MAX_PATCH_FILES * 2:
        raise CodingAgentError("结构化修改步骤过多，请缩小单次修改范围。")
    if len(json.dumps(edits, ensure_ascii=False).encode("utf-8")) > MAX_PATCH_BYTES:
        raise CodingAgentError("结构化修改内容过大，请缩小单次修改范围。")

    root = project_path.resolve()
    original_files: dict[str, str] = {}
    file_edits: dict[str, list[tuple[int, int, str]]] = {}
    for edit in edits:
        if not isinstance(edit, dict):
            raise CodingAgentError("每个 edit 必须是 JSON 对象。")
        relative_input = str(edit.get("path") or "").strip()
        start_line = edit.get("start_line")
        end_line = edit.get("end_line")
        replacement = edit.get("replacement")
        if (
            not relative_input
            or isinstance(start_line, bool)
            or not isinstance(start_line, int)
            or isinstance(end_line, bool)
            or not isinstance(end_line, int)
            or not isinstance(replacement, str)
        ):
            raise CodingAgentError(
                "每个 edit 都必须包含 path、start_line、end_line 和 replacement。"
            )

        if any(is_ignored_directory(part) for part in Path(relative_input).parts):
            raise CodingAgentError(f"不允许修改生成目录或内部目录：{relative_input}")
        target = _resolve_edit_target(root, relative_input)
        relative = target.relative_to(root).as_posix()
        if any(is_ignored_directory(part) for part in Path(relative).parts):
            raise CodingAgentError(f"不允许修改生成目录或内部目录：{relative}")
        if not target.is_file() or not _is_text_file(target):
            raise CodingAgentError(f"结构化修改只能编辑已有文本文件：{relative}")

        if relative not in original_files:
            try:
                original_files[relative] = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise CodingAgentError(f"无法读取待修改文件：{relative}") from exc
            file_edits[relative] = []

        line_count = len(original_files[relative].splitlines(keepends=True))
        if start_line > line_count:
            start_line = end_line = line_count + 1
        elif start_line <= line_count < end_line:
            end_line = line_count
        is_eof_append = start_line == end_line == line_count + 1
        if not is_eof_append and (
            start_line < 1 or end_line < start_line or end_line > line_count
        ):
            raise CodingAgentError(
                f"edit 行范围越界：{relative}:{start_line}-{end_line}，文件共 {line_count} 行。"
            )
        replaced_line_count = 0 if is_eof_append else end_line - start_line + 1
        if replaced_line_count > MAX_EDIT_LINES:
            raise CodingAgentError(f"单个 edit 最多替换 {MAX_EDIT_LINES} 行：{relative}")
        file_edits[relative].append((start_line, end_line, replacement))

    if len(file_edits) > MAX_PATCH_FILES:
        raise CodingAgentError(f"单次补丁最多修改 {MAX_PATCH_FILES} 个文件。")

    patch_parts: list[str] = []
    for relative, ranges in file_edits.items():
        original = original_files[relative]
        ordered = sorted(ranges, key=lambda item: (item[0], item[1]))
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] <= previous[1]:
                raise CodingAgentError(f"结构化 edits 在 {relative} 中存在重叠行范围。")

        modified_lines = original.splitlines(keepends=True)
        for start_line, end_line, replacement in reversed(ordered):
            original_segment = modified_lines[start_line - 1 : end_line]
            is_eof_append = start_line == end_line == len(original.splitlines(keepends=True)) + 1
            if is_eof_append and replacement and original and not original.endswith("\n"):
                replacement = "\n" + replacement
            if (
                replacement
                and ((original_segment and original_segment[-1].endswith("\n")) or is_eof_append)
                and not replacement.endswith("\n")
            ):
                replacement += "\n"
            modified_lines[start_line - 1 : end_line] = replacement.splitlines(keepends=True)
        modified = "".join(modified_lines)
        if modified == original:
            continue
        unified = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                modified.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                lineterm="\n",
            )
        )
        patch_parts.append(f"diff --git a/{relative} b/{relative}\n{unified}")

    if not patch_parts:
        raise CodingAgentError("结构化修改没有产生任何文件变更。")
    return _proposal_from_data(data, "".join(patch_parts))


def normalize_unified_diff(patch: str, project_path: Path | None = None) -> str:
    cleaned = patch.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:diff|patch)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    file_header = re.compile(
        r"^--- (?P<old>a/[^\n]+|/dev/null)\n"
        r"\+\+\+ (?P<new>b/[^\n]+|/dev/null)\n",
        flags=re.MULTILINE,
    )

    def add_git_header(match: re.Match[str]) -> str:
        old_path = match.group("old")
        new_path = match.group("new")
        git_old = old_path if old_path != "/dev/null" else "a/" + new_path[2:]
        git_new = new_path if new_path != "/dev/null" else "b/" + old_path[2:]
        return (
            f"diff --git {git_old} {git_new}\n"
            f"--- {old_path}\n"
            f"+++ {new_path}\n"
        )

    if re.search(r"^diff --git ", cleaned, flags=re.MULTILINE):
        normalized = cleaned
    else:
        normalized = file_header.sub(add_git_header, cleaned)
    normalized = normalize_diff_preamble(normalized)
    return normalize_hunk_context(normalized, project_path) + "\n"


def normalize_diff_preamble(patch: str) -> str:
    allowed_metadata = (
        "index ",
        "new file mode ",
        "deleted file mode ",
        "old mode ",
        "new mode ",
        "similarity index ",
        "dissimilarity index ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
    )
    normalized: list[str] = []
    in_preamble = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            in_preamble = True
            normalized.append(line)
            continue
        if in_preamble:
            if line.startswith("--- "):
                in_preamble = False
                normalized.append(line)
            elif line.startswith(allowed_metadata):
                normalized.append(line)
            continue
        normalized.append(line)
    return "\n".join(normalized)


def normalize_hunk_context(patch: str, project_path: Path | None = None) -> str:
    lines: list[str] = []
    in_hunk = False
    root = project_path.resolve() if project_path is not None else None
    source_available = False
    original_lines: list[str] = []
    original_index = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            source_available = False
            original_lines = []
            if root is not None:
                try:
                    parts = shlex.split(line)
                    relative = parts[2][2:] if len(parts) == 4 and parts[2].startswith("a/") else ""
                    target = _safe_target(root, relative)
                    source_available = True
                    if target.is_file():
                        original_lines = target.read_text(
                            encoding="utf-8", errors="replace"
                        ).splitlines()
                except (CodingAgentError, OSError, ValueError):
                    source_available = False
        elif line.startswith("@@"):
            in_hunk = True
            match = re.match(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
            original_index = max(0, int(match.group(1)) - 1) if match else 0
        elif in_hunk:
            if line.startswith(" ") and source_available:
                current = (
                    original_lines[original_index]
                    if original_index < len(original_lines)
                    else None
                )
                if current is not None and line[1:] == current:
                    original_index += 1
                elif current is not None and line == current:
                    line = " " + line
                    original_index += 1
                else:
                    line = "+" + line
            elif line.startswith((" ", "-")):
                original_index += 1
            elif not line.startswith(("+", "\\")):
                if (
                    source_available
                    and original_index < len(original_lines)
                    and line == original_lines[original_index]
                ):
                    line = " " + line
                    original_index += 1
                elif source_available:
                    line = "+" + line
                else:
                    line = " " + line
        lines.append(line)
    return "\n".join(lines)


def _safe_target(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CodingAgentError(f"补丁包含不安全路径：{relative}")
    target = (root / candidate).resolve()
    if target == root or root not in target.parents:
        raise CodingAgentError(f"补丁路径超出项目目录：{relative}")
    return target


def _resolve_edit_target(root: Path, relative: str) -> Path:
    target = _safe_target(root, relative)
    if target.is_file():
        return target

    basename = Path(relative).name
    matches = [path.resolve() for path in _iter_project_files(root) if path.name == basename]
    if len(matches) == 1:
        return matches[0]
    return target


def patch_paths(project_path: Path, patch: str) -> list[str]:
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        raise CodingAgentError("补丁过大，请缩小单次修改范围。")
    root = project_path.resolve()
    paths: list[str] = []
    if re.search(r"^\+\+\+ /dev/null$", patch, flags=re.MULTILINE):
        raise CodingAgentError("第一版暂不支持删除文件。")
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            raise CodingAgentError("补丁文件头格式无效。") from exc
        if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
            raise CodingAgentError("补丁必须使用标准 git diff 文件头。")
        old_path = parts[2][2:]
        new_path = parts[3][2:]
        if old_path != new_path:
            raise CodingAgentError("第一版暂不支持在补丁中重命名文件。")
        target = _safe_target(root, new_path)
        relative = target.relative_to(root).as_posix()
        if any(is_ignored_directory(part) for part in Path(relative).parts):
            raise CodingAgentError(f"不允许修改生成目录或内部目录：{relative}")
        if not _is_text_file(target):
            raise CodingAgentError(f"不允许修改未识别的文件类型：{relative}")
        if relative not in paths:
            paths.append(relative)
    if not paths:
        raise CodingAgentError("补丁没有包含任何文件变更。")
    if len(paths) > MAX_PATCH_FILES:
        raise CodingAgentError(f"单次补丁最多修改 {MAX_PATCH_FILES} 个文件。")
    if "@@" not in patch:
        raise CodingAgentError("补丁缺少 unified diff hunk。")
    return paths


def _run_git_apply(project_path: Path, patch: str, check: bool) -> None:
    command = ["git", "apply", "--recount", "--whitespace=nowarn"]
    if check:
        command.append("--check")
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_path),
            input=patch,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodingAgentError(f"无法校验补丁：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git apply failed").strip()
        action = "校验" if check else "应用"
        raise CodingAgentError(f"补丁{action}失败：{detail}")


def validate_patch(project_path: Path, patch: str) -> list[str]:
    paths = patch_paths(project_path, patch)
    _run_git_apply(project_path, patch, check=True)
    return paths


def _proposal_path(proposal_dir: Path, proposal_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", proposal_id):
        raise CodingAgentError("无效的修改方案 ID。")
    return proposal_dir / f"{proposal_id}.json"


def save_proposal(
    proposal_dir: Path,
    project_path: Path,
    task: str,
    proposal: dict[str, Any],
    context: dict[str, Any],
    *,
    kind: str = "change",
    parent_id: str = "",
    round_number: int = 1,
    diagnostic_history_id: str = "",
) -> dict[str, Any]:
    proposal_dir.mkdir(parents=True, exist_ok=True)
    proposal_id = uuid.uuid4().hex
    record = {
        "id": proposal_id,
        "project_path": str(project_path.resolve()),
        "task": task,
        "summary": proposal["summary"],
        "plan": proposal["plan"],
        "risks": proposal["risks"],
        "tests": proposal["tests"],
        "patch": proposal["patch"],
        "files": patch_paths(project_path, proposal["patch"]),
        "context_files": [item["path"] for item in context.get("files", [])],
        "kind": kind,
        "parent_id": parent_id,
        "round": round_number,
        "diagnostic_history_id": diagnostic_history_id,
        "status": "pending",
        "created_at": _now(),
    }
    _proposal_path(proposal_dir, proposal_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def load_proposal(proposal_dir: Path, proposal_id: str) -> dict[str, Any]:
    path = _proposal_path(proposal_dir, proposal_id)
    if not path.is_file():
        raise CodingAgentError("修改方案不存在或已被清理。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodingAgentError("修改方案文件已损坏。") from exc
    if not isinstance(data, dict):
        raise CodingAgentError("修改方案格式无效。")
    return data


def _write_proposal(proposal_dir: Path, record: dict[str, Any]) -> None:
    _proposal_path(proposal_dir, str(record["id"])).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_proposal(proposal_dir: Path, backup_dir: Path, proposal_id: str) -> dict[str, Any]:
    with _MUTATION_LOCK:
        return _apply_proposal_unlocked(proposal_dir, backup_dir, proposal_id)


def _apply_proposal_unlocked(
    proposal_dir: Path, backup_dir: Path, proposal_id: str
) -> dict[str, Any]:
    record = load_proposal(proposal_dir, proposal_id)
    if record.get("status") != "pending":
        raise CodingAgentError(f"当前修改方案状态为 {record.get('status')}，不能重复应用。")
    project_path = Path(str(record["project_path"])).resolve()
    if not project_path.is_dir():
        raise CodingAgentError("修改方案对应的项目目录不存在。")
    patch = str(record["patch"])
    files = validate_patch(project_path, patch)

    current_backup_dir = backup_dir / proposal_id
    if current_backup_dir.exists():
        shutil.rmtree(current_backup_dir)
    current_backup_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for relative in files:
        target = _safe_target(project_path, relative)
        existed = target.is_file()
        item: dict[str, Any] = {"path": relative, "existed": existed}
        if existed:
            backup_path = current_backup_dir / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)
            item["before_sha256"] = _sha256(target)
        manifest.append(item)

    try:
        _run_git_apply(project_path, patch, check=False)
        for item in manifest:
            target = _safe_target(project_path, item["path"])
            if not target.is_file():
                raise CodingAgentError(f"应用后文件不存在：{item['path']}")
            item["applied_sha256"] = _sha256(target)
    except Exception:
        for item in manifest:
            target = _safe_target(project_path, item["path"])
            if item.get("existed"):
                backup_path = current_backup_dir / item["path"]
                if backup_path.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_path, target)
            else:
                target.unlink(missing_ok=True)
        raise

    record["status"] = "applied"
    record["applied_at"] = _now()
    record["backup_dir"] = str(current_backup_dir)
    record["backup_manifest"] = manifest
    _write_proposal(proposal_dir, record)
    return record


def rollback_proposal(proposal_dir: Path, proposal_id: str) -> dict[str, Any]:
    with _MUTATION_LOCK:
        return _rollback_proposal_unlocked(proposal_dir, proposal_id)


def _rollback_proposal_unlocked(proposal_dir: Path, proposal_id: str) -> dict[str, Any]:
    record = load_proposal(proposal_dir, proposal_id)
    if record.get("status") != "applied":
        raise CodingAgentError(f"当前修改方案状态为 {record.get('status')}，不能回滚。")
    project_path = Path(str(record["project_path"])).resolve()
    backup_root = Path(str(record.get("backup_dir") or "")).resolve()
    manifest = record.get("backup_manifest") or []
    if not project_path.is_dir() or not backup_root.is_dir():
        raise CodingAgentError("回滚所需的项目目录或备份不存在。")

    for item in manifest:
        target = _safe_target(project_path, str(item["path"]))
        if not target.is_file() or _sha256(target) != item.get("applied_sha256"):
            raise CodingAgentError(f"文件在应用补丁后又被修改，已拒绝覆盖：{item['path']}")

    for item in manifest:
        target = _safe_target(project_path, str(item["path"]))
        if item.get("existed"):
            backup_path = backup_root / str(item["path"])
            if not backup_path.is_file():
                raise CodingAgentError(f"缺少备份文件：{item['path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, target)
        else:
            target.unlink(missing_ok=True)

    record["status"] = "rolled_back"
    record["rolled_back_at"] = _now()
    _write_proposal(proposal_dir, record)
    return record


def proposal_markdown(record: dict[str, Any]) -> str:
    lines = [f"# {record.get('summary', '代码修改方案')}", "", "## 修改计划", ""]
    plan = record.get("plan") or ["应用候选补丁并执行构建测试。"]
    lines.extend(f"{index}. {item}" for index, item in enumerate(plan, 1))
    lines.extend(["", "## 变更文件", ""])
    lines.extend(f"- `{path}`" for path in record.get("files", []))
    if record.get("risks"):
        lines.extend(["", "## 风险", ""])
        lines.extend(f"- {item}" for item in record["risks"])
    if record.get("tests"):
        lines.extend(["", "## 建议验证", ""])
        lines.extend(f"- {item}" for item in record["tests"])
    return "\n".join(lines) + "\n"
