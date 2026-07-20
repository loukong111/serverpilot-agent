from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    api_key: str = ""
    timeout_seconds: int = 60
    temperature: float = 0.2


class LLMClient:
    """Tiny OpenAI-compatible Chat Completions client using only Python stdlib."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def chat(self, messages: list[dict[str, str]]) -> str:
        api_key = self.config.api_key or os.environ.get(self.config.api_key_env)
        if not api_key:
            raise LLMConfigurationError(
                f"Missing API key. Set {self.config.api_key_env} or run without --llm."
            )
        if not self.config.model:
            raise LLMConfigurationError(
                "Missing model. Pass --model or set PROJECTAGENTCPP_MODEL / OPENAI_MODEL."
            )

        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMRequestError(f"LLM request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMRequestError(f"LLM request failed: {exc}") from exc

        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMRequestError(f"Unexpected LLM response: {body[:500]}") from exc


def config_from_args(args: Any) -> LLMConfig:
    model = (
        getattr(args, "model", None)
        or os.environ.get("PROJECTAGENTCPP_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or ""
    )
    base_url = (
        getattr(args, "base_url", None)
        or os.environ.get("PROJECTAGENTCPP_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    api_key_env = getattr(args, "api_key_env", None) or "OPENAI_API_KEY"
    return LLMConfig(model=model, base_url=base_url, api_key_env=api_key_env)
