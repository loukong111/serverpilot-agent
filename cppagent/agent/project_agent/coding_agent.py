from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
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
    "reports",
}
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


class CodingAgentError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_text_file(path: Path) -> bool:
    return path.name in SPECIAL_TEXT_FILES or path.suffix.lower() in TEXT_SUFFIXES


def _iter_project_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in IGNORED_DIRECTORIES)
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
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_:-]{1,}|[\u4e00-\u9fff]{2,8}", query)
    ignored = {"项目", "代码", "功能", "增加", "添加", "修改", "实现", "修复", "一个", "进行"}
    unique: list[str] = []
    for token in tokens:
        normalized = token.lower()
        if normalized in ignored or normalized in unique:
            continue
        unique.append(normalized)
    return unique[:16]


def search_code(project_path: Path, query: str, max_results: int = 40) -> list[dict[str, Any]]:
    root = project_path.resolve()
    tokens = _search_tokens(query)
    if not tokens:
        return []

    matches: list[dict[str, Any]] = []
    for path in _iter_project_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, 1):
            lowered = line.lower()
            matched = [token for token in tokens if token in lowered or token in relative.lower()]
            if not matched:
                continue
            matches.append(
                {
                    "path": relative,
                    "line": line_number,
                    "text": line.strip()[:300],
                    "score": len(matched) + (1 if any(token in relative.lower() for token in matched) else 0),
                }
            )
    matches.sort(key=lambda item: (-item["score"], item["path"], item["line"]))
    return matches[:max_results]


def collect_code_context(project_path: Path, task: str, max_files: int = 12) -> dict[str, Any]:
    root = project_path.resolve()
    matches = search_code(root, task)
    ranked_paths: list[str] = []

    for preferred in ("README.md", "CMakeLists.txt"):
        if (root / preferred).is_file():
            ranked_paths.append(preferred)
    for match in matches:
        if match["path"] not in ranked_paths:
            ranked_paths.append(match["path"])
    if len(ranked_paths) < max_files:
        for path in _iter_project_files(root):
            relative = path.relative_to(root).as_posix()
            if relative not in ranked_paths:
                ranked_paths.append(relative)
            if len(ranked_paths) >= max_files:
                break

    context_files: list[dict[str, str]] = []
    used_bytes = 0
    for relative in ranked_paths[:max_files]:
        path = _safe_target(root, relative)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        encoded_size = len(content.encode("utf-8"))
        if used_bytes + encoded_size > MAX_CONTEXT_BYTES:
            remaining = max(0, MAX_CONTEXT_BYTES - used_bytes)
            if remaining < 1024:
                break
            content = content.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
            encoded_size = len(content.encode("utf-8"))
        context_files.append({"path": relative, "content": content})
        used_bytes += encoded_size
        if used_bytes >= MAX_CONTEXT_BYTES:
            break

    return {
        "matches": matches,
        "files": context_files,
        "context_bytes": used_bytes,
    }


def build_coding_messages(task: str, analysis: dict[str, Any], context: dict[str, Any]) -> list[dict[str, str]]:
    file_blocks = []
    for item in context.get("files", []):
        file_blocks.append(f"===== FILE: {item['path']} =====\n{item['content']}")
    project_facts = json.dumps(analysis, ensure_ascii=False, indent=2)
    source_context = "\n\n".join(file_blocks)
    return [
        {
            "role": "system",
            "content": (
                "你是面向 C++ 服务端项目的 Coding Agent。只能依据给出的项目事实和源码上下文提出修改。"
                "保持修改范围小，遵循现有代码风格，并优先补充测试。输出必须是单个 JSON 对象，不要使用 Markdown code fence。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"任务：\n{task}\n\n"
                f"项目事实：\n{project_facts}\n\n"
                f"源码上下文：\n{source_context}\n\n"
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
            ),
        },
    ]


def parse_coding_response(content: str) -> dict[str, Any]:
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
    patch = data.get("patch")
    if not isinstance(patch, str) or not patch.strip():
        raise CodingAgentError("LLM 修改方案中缺少 unified diff。")
    return {
        "summary": str(data.get("summary") or "代码修改方案"),
        "plan": [str(item) for item in data.get("plan", []) if str(item).strip()],
        "risks": [str(item) for item in data.get("risks", []) if str(item).strip()],
        "tests": [str(item) for item in data.get("tests", []) if str(item).strip()],
        "patch": patch.strip() + "\n",
    }


def _safe_target(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CodingAgentError(f"补丁包含不安全路径：{relative}")
    target = (root / candidate).resolve()
    if target == root or root not in target.parents:
        raise CodingAgentError(f"补丁路径超出项目目录：{relative}")
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
        if any(part in IGNORED_DIRECTORIES for part in Path(relative).parts):
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
