from __future__ import annotations

import json
import threading
import time
import unittest
from unittest import mock

from agent.project_agent.llm_client import (
    LLMClient,
    LLMConfig,
    LLMRequestCancelled,
    LLMRequestError,
)


class StreamingResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self) -> "StreamingResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


class BlockingStreamingResponse:
    def __init__(self) -> None:
        self.release = threading.Event()

    def __iter__(self):
        self.release.wait(2)
        return iter(())

    def close(self) -> None:
        time.sleep(1)
        self.release.set()


class LLMClientTest(unittest.TestCase):
    def test_timeout_is_wrapped_as_llm_request_error(self) -> None:
        client = LLMClient(
            LLMConfig(model="test-model", api_key="test-key", timeout_seconds=7)
        )

        with mock.patch(
            "agent.project_agent.llm_client.urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            with self.assertRaisesRegex(LLMRequestError, "timed out after 7s"):
                client.chat([])

    def test_response_format_is_sent_when_requested(self) -> None:
        client = LLMClient(LLMConfig(model="test-model", api_key="test-key"))
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"choices":[{"message":{"content":"{}"}}]}'
        )

        with mock.patch(
            "agent.project_agent.llm_client.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            client.chat(
                [],
                response_format={"type": "json_object"},
                max_tokens=512,
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual({"type": "json_object"}, payload["response_format"])
        self.assertEqual(512, payload["max_tokens"])

    def test_streaming_response_reports_deltas(self) -> None:
        client = LLMClient(LLMConfig(model="test-model", api_key="test-key"))
        response = StreamingResponse(
            [
                b'data: {"choices":[{"delta":{"content":"hello "}}]}\n',
                b'data: {"choices":[{"delta":{"content":"world"}}]}\n',
                b"data: [DONE]\n",
            ]
        )
        deltas: list[str] = []

        with mock.patch(
            "agent.project_agent.llm_client.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            content = client.chat([], stream=True, on_delta=deltas.append)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertTrue(payload["stream"])
        self.assertEqual("hello world", content)
        self.assertEqual(["hello ", "world"], deltas)

    def test_streaming_response_honors_cancellation(self) -> None:
        client = LLMClient(LLMConfig(model="test-model", api_key="test-key"))
        response = StreamingResponse(
            [
                b'data: {"choices":[{"delta":{"content":"first"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"second"}}]}\n',
            ]
        )
        checks = 0

        def should_cancel() -> bool:
            nonlocal checks
            checks += 1
            return checks > 1

        with mock.patch(
            "agent.project_agent.llm_client.urllib.request.urlopen",
            return_value=response,
        ):
            with self.assertRaises(LLMRequestCancelled):
                client.chat([], stream=True, should_cancel=should_cancel)

    def test_streaming_cancellation_does_not_wait_for_response_close(self) -> None:
        client = LLMClient(LLMConfig(model="test-model", api_key="test-key"))
        response = BlockingStreamingResponse()

        with mock.patch(
            "agent.project_agent.llm_client.urllib.request.urlopen",
            return_value=response,
        ):
            started_at = time.monotonic()
            with self.assertRaises(LLMRequestCancelled):
                client.chat([], stream=True, should_cancel=lambda: True)

        self.assertLess(time.monotonic() - started_at, 0.5)

    def test_streaming_cancellation_does_not_wait_for_response_headers(self) -> None:
        client = LLMClient(LLMConfig(model="test-model", api_key="test-key"))
        release = threading.Event()

        def delayed_open(*_args: object, **_kwargs: object) -> StreamingResponse:
            release.wait(1)
            return StreamingResponse([])

        checks = 0

        def should_cancel() -> bool:
            nonlocal checks
            checks += 1
            return checks > 1

        with mock.patch(
            "agent.project_agent.llm_client.urllib.request.urlopen",
            side_effect=delayed_open,
        ):
            started_at = time.monotonic()
            with self.assertRaises(LLMRequestCancelled):
                client.chat([], stream=True, should_cancel=should_cancel)

        release.set()
        self.assertLess(time.monotonic() - started_at, 0.5)


if __name__ == "__main__":
    unittest.main()
