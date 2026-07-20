from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def prompt_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "prompts"


def load_template(name: str) -> str:
    return (prompt_dir() / name).read_text(encoding="utf-8")


def compact_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def render_template(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def build_report_messages(data: dict[str, Any]) -> list[dict[str, str]]:
    system = load_template("system.md")
    user = render_template(
        load_template("report.md"),
        {"analysis_json": compact_json(data)},
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_interview_messages(data: dict[str, Any], question: str) -> list[dict[str, str]]:
    system = load_template("system.md")
    user = render_template(
        load_template("interview_qa.md"),
        {
            "analysis_json": compact_json(data),
            "question": question,
        },
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def messages_to_text(messages: list[dict[str, str]]) -> str:
    return "\n\n".join(f"{message['role'].upper()}:\n{message['content']}" for message in messages)
