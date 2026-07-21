from __future__ import annotations

import json
import os
import queue
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


class LLMRequestCancelled(LLMRequestError):
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

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
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
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream"] = True
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
            if stream and should_cancel:
                response = self._open_response_cancellable(request, should_cancel)
            else:
                response = urllib.request.urlopen(request, timeout=self.config.timeout_seconds)
            if stream:
                try:
                    return self._read_stream(response, on_delta, should_cancel)
                finally:
                    self._close_response_async(response)
            with response as opened_response:
                body = opened_response.read().decode("utf-8")
        except LLMRequestCancelled:
            raise
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMRequestError(f"LLM request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMRequestError(f"LLM request failed: {exc}") from exc
        except TimeoutError as exc:
            raise LLMRequestError(
                f"LLM request timed out after {self.config.timeout_seconds}s"
            ) from exc

        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMRequestError(f"Unexpected LLM response: {body[:500]}") from exc

    def _open_response_cancellable(
        self,
        request: urllib.request.Request,
        should_cancel: Callable[[], bool],
    ) -> Any:
        if should_cancel():
            raise LLMRequestCancelled("LLM request cancelled")

        events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def open_response() -> None:
            try:
                response = urllib.request.urlopen(
                    request,
                    timeout=self.config.timeout_seconds,
                )
                if should_cancel():
                    self._close_response_async(response)
                    return
                events.put(("response", response))
            except Exception as exc:  # noqa: BLE001
                if not should_cancel():
                    events.put(("error", exc))

        threading.Thread(target=open_response, daemon=True).start()
        while True:
            if should_cancel():
                raise LLMRequestCancelled("LLM request cancelled")
            try:
                event_type, value = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if event_type == "error":
                raise value
            return value

    @staticmethod
    def _close_response_async(response: Any) -> None:
        close = getattr(response, "close", None)
        if not close:
            return

        def close_response() -> None:
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=close_response, daemon=True).start()

    @staticmethod
    def _read_stream(
        response: Any,
        on_delta: Callable[[str], None] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> str:
        events: queue.Queue[tuple[str, Any]] = queue.Queue()

        def read_response() -> None:
            try:
                for raw_line in response:
                    events.put(("line", raw_line))
            except Exception as exc:  # noqa: BLE001
                events.put(("error", exc))
            finally:
                events.put(("done", None))

        reader = threading.Thread(target=read_response, daemon=True)
        reader.start()
        chunks: list[str] = []
        while True:
            if should_cancel and should_cancel():
                raise LLMRequestCancelled("LLM request cancelled")
            try:
                event_type, value = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if event_type == "done":
                break
            if event_type == "error":
                raise LLMRequestError(f"LLM stream failed: {value}") from value
            raw_line = value
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                event = json.loads(line)
                delta = event["choices"][0].get("delta", {}).get("content", "")
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise LLMRequestError(f"Unexpected LLM stream event: {line[:500]}") from exc
            if not isinstance(delta, str) or not delta:
                continue
            chunks.append(delta)
            if on_delta:
                on_delta(delta)
        if should_cancel and should_cancel():
            raise LLMRequestCancelled("LLM request cancelled")
        content = "".join(chunks).strip()
        if not content:
            raise LLMRequestError("LLM stream ended without content")
        return content


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
