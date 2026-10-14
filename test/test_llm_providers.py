import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp


os.environ.setdefault("DISCORD_TOKEN", "test-token")

import llm
import main
from system_guard import SYSTEM_ACCESS_REJECT_REPLY


def openai_response(text: str) -> dict[str, object]:
    return {
        "status": "completed",
        "output": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        }],
    }


def make_session(*data: object, status: int = 200) -> MagicMock:
    responses = []
    for item in data:
        response = MagicMock(status=status)
        response.__aenter__.return_value = response
        response.json = AsyncMock(return_value=item)
        responses.append(response)
    session = MagicMock()
    session.__aenter__.return_value = session
    session.post.side_effect = responses
    session.responses = responses
    return session


class LlmProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for name, value in (
            ("LLM_PROVIDER", "openai"),
            ("OPENAI_API_KEY", "test-openai-key"),
            ("OPENAI_MODEL", "gpt-6-luna"),
            ("OPENAI_REASONING_EFFORT", "none"),
            ("OPENAI_MAX_OUTPUT_TOKENS", 256),
        ):
            setting = patch.object(llm, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    async def test_openai_sends_authenticated_request_with_shared_context(self) -> None:
        reply = "안녕하세요. 편하게 말씀해 주세요."
        session = make_session(openai_response(reply))
        examples = [
            {"role": "user", "content": "예시 질문입니다."},
            {"role": "assistant", "content": "예시 답변입니다."},
        ]
        history = [
            {"role": "user", "content": "지난 질문입니다."},
            {"role": "assistant", "content": "지난 답변입니다."},
        ]
        with (
            patch.object(llm.aiohttp, "ClientSession", return_value=session),
            patch.object(llm, "load_conversation_examples", return_value=examples),
            patch.object(llm, "_post_ollama", new_callable=AsyncMock) as local,
        ):
            result = await llm.generate_reply("안녕하세요", history, "친구")

        self.assertEqual(result, reply)
        local.assert_not_awaited()
        call = session.post.call_args
        self.assertEqual(call.args, ("https://api.openai.com/v1/responses",))
        self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer test-openai-key"})
        self.assertFalse(call.kwargs["allow_redirects"])
        payload = call.kwargs["json"]
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertIn(llm.PUBLIC_IDENTITY_INVARIANT, payload["instructions"])
        self.assertIn("친구", payload["instructions"])
        self.assertEqual(payload["input"], examples + history + [{"role": "user", "content": "안녕하세요"}])
        self.assertEqual(payload["reasoning"], {"effort": "none"})
        self.assertEqual(payload["max_output_tokens"], 256)
        self.assertFalse(payload["store"])
        self.assertNotIn("keep_alive", payload)
        self.assertNotIn("options", payload)

    async def test_ollama_selection_preserves_existing_payload(self) -> None:
        with (
            patch.object(llm, "LLM_PROVIDER", "ollama"),
            patch.object(llm, "_post_ollama", new_callable=AsyncMock,
                         return_value={"message": {"content": "로컬 답변입니다."}}) as local,
            patch.object(llm, "_post_openai", new_callable=AsyncMock) as cloud,
        ):
            result = await llm.generate_reply("안녕하세요")
        self.assertEqual(result, "로컬 답변입니다.")
        cloud.assert_not_awaited()
        endpoint, payload = local.await_args.args
        self.assertEqual(endpoint, "/api/chat")
        self.assertEqual(payload["model"], llm.OLLAMA_MODEL)
        self.assertEqual(payload["keep_alive"], llm.OLLAMA_KEEP_ALIVE)
        self.assertEqual(payload["options"], {
            "num_ctx": llm.OLLAMA_NUM_CTX, "num_predict": 96,
            "temperature": 0.1, "repeat_penalty": 1.15,
        })
        self.assertEqual(payload["messages"][-1], {"role": "user", "content": "안녕하세요"})

    async def test_openai_repair_preserves_user_turn_and_excludes_bad_reply(self) -> None:
        bad_reply = "뭘 도와줄까?"
        good_reply = "무엇을 도와드릴까요?"
        session = make_session(openai_response(bad_reply), openai_response(good_reply))
        with patch.object(llm.aiohttp, "ClientSession", return_value=session):
            self.assertEqual(await llm.generate_reply("안녕하세요"), good_reply)
        self.assertEqual(session.post.call_count, 2)
        original = session.post.call_args_list[0].kwargs["json"]
        retry = session.post.call_args_list[1].kwargs["json"]
        self.assertNotIn(llm.TONE_REPAIR_INSTRUCTION, original["instructions"])
        self.assertIn(llm.TONE_REPAIR_INSTRUCTION, retry["instructions"])
        self.assertEqual(original["input"], retry["input"])
        self.assertNotIn(bad_reply, [item["content"] for item in retry["input"]])
        self.assertEqual(retry["input"][-1], {"role": "user", "content": "안녕하세요"})

    async def test_openai_security_guards_apply_before_and_after_request(self) -> None:
        with patch.object(llm, "_post_openai", new_callable=AsyncMock) as cloud:
            self.assertEqual(await llm.generate_reply("<unused8>"), SYSTEM_ACCESS_REJECT_REPLY)
            cloud.assert_not_awaited()
            cloud.return_value = openai_response(llm.TONE_INVARIANT)
            with self.assertRaises(llm.LlmSecurityError):
                await llm.generate_reply("안녕하세요")

    async def test_reads_message_text_and_refusal_but_ignores_reasoning(self) -> None:
        messages = [{"role": "system", "content": "지침"}, {"role": "user", "content": "질문"}]
        for data, expected in (
            ({"status": "completed", "output": [
                {"type": "reasoning", "summary": [{"text": "숨겨진 추론"}]},
                {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": "첫 문장입니다."},
                    {"type": "output_text", "text": "둘째 문장입니다."},
                ]},
            ]}, "첫 문장입니다.\n둘째 문장입니다."),
            ({"status": "completed", "output": [
                {"type": "message", "role": "assistant", "content": [
                    {"type": "refusal", "refusal": "요청을 도와드릴 수 없습니다."},
                ]},
            ]}, "요청을 도와드릴 수 없습니다."),
        ):
            with self.subTest(expected=expected):
                with patch.object(llm, "_post_openai", new_callable=AsyncMock, return_value=data):
                    self.assertEqual(await llm._request_openai(messages), expected)

    async def test_incomplete_reply_is_not_sent_or_saved(self) -> None:
        data = openai_response("잘린 문장")
        data["status"] = "incomplete"
        message = SimpleNamespace(
            guild=None, channel=MagicMock(id=10),
            author=SimpleNamespace(id=123, display_name="친구"),
        )
        with (
            patch.object(llm, "_post_openai", new_callable=AsyncMock, return_value=data),
            patch.object(llm, "_post_ollama", new_callable=AsyncMock) as local,
            patch.object(main, "get_session_record", new_callable=AsyncMock, return_value={"messages": []}),
            patch.object(main, "send_reply", new_callable=AsyncMock) as send,
            patch.object(main, "save_exchange", new_callable=AsyncMock) as save,
            patch("builtins.print"),
        ):
            await main.process_conversation_message(message, "안녕하세요")
        send.assert_awaited_once_with(message, main.RESPONSE_ERROR_REPLY)
        save.assert_not_awaited()
        local.assert_not_awaited()

    async def test_http_errors_report_safe_hints_without_reading_body(self) -> None:
        for status, hint in ((401, "OPENAI_API_KEY"), (429, "잔액")):
            with self.subTest(status=status):
                session = make_session({"error": "test-openai-key private conversation"}, status=status)
                with patch.object(llm.aiohttp, "ClientSession", return_value=session):
                    with self.assertRaises(llm.LlmResponseError) as caught:
                        await llm._post_openai({})
                self.assertIn(hint, str(caught.exception))
                self.assertNotIn("test-openai-key", str(caught.exception))
                self.assertNotIn("private conversation", str(caught.exception))
                session.responses[0].json.assert_not_awaited()
                session.responses[0].text.assert_not_called()
                # 실패한 요청을 자동으로 재전송하지 않는다.
                self.assertEqual(session.post.call_count, 1)

    async def test_transport_failures_use_existing_bot_error_types(self) -> None:
        for error, expected in (
            (TimeoutError(), llm.LlmTimeoutError),
            (aiohttp.ClientError("network failure"), llm.LlmConnectionError),
        ):
            with self.subTest(error=type(error).__name__):
                session = make_session()
                session.post.side_effect = error
                with patch.object(llm.aiohttp, "ClientSession", return_value=session):
                    with self.assertRaises(expected):
                        await llm._post_openai({})

    async def test_rejects_malformed_or_empty_responses(self) -> None:
        messages = [{"role": "system", "content": "지침"}, {"role": "user", "content": "질문"}]
        for data in (
            {"status": "failed", "error": {"message": "private content"}},
            {"status": "completed", "output": None},
            {"status": "completed", "output": []},
            {"status": "completed", "output": [None, {"type": "message", "role": "assistant", "content": None}]},
        ):
            with self.subTest(data=data):
                with patch.object(llm, "_post_openai", new_callable=AsyncMock, return_value=data):
                    with self.assertRaises(llm.LlmResponseError):
                        await llm._request_openai(messages)
        session = make_session({})
        response = MagicMock(status=200)
        response.__aenter__.return_value = response
        response.json = AsyncMock(side_effect=ValueError("invalid JSON"))
        session.post.side_effect = [response]
        with patch.object(llm.aiohttp, "ClientSession", return_value=session):
            with self.assertRaises(llm.LlmResponseError):
                await llm._post_openai({})

    async def test_blank_reasoning_setting_omits_parameter(self) -> None:
        with (
            patch.object(llm, "OPENAI_REASONING_EFFORT", ""),
            patch.object(llm, "_post_openai", new_callable=AsyncMock,
                         return_value=openai_response("답변입니다.")) as cloud,
        ):
            await llm.generate_reply("안녕하세요")
        self.assertNotIn("reasoning", cloud.await_args.args[0])


class ProviderStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_ollama_startup_schedules_warmup(self) -> None:
        for provider in ("openai", "ollama"):
            with self.subTest(provider=provider):
                with (
                    patch.object(main, "LLM_PROVIDER", provider),
                    patch.object(main, "OLLAMA_WARMUP_ON_START", True),
                    patch.object(main, "commands_synced", True),
                    patch.object(main, "CONVERSATION_RETENTION_DAYS", 0),
                    patch.object(main, "ollama_warmup_task", None),
                    patch.object(main, "client", SimpleNamespace(user="test-bot")),
                    patch.object(main, "start_github_webhook_server", new_callable=AsyncMock),
                    patch.object(main, "run_ollama_warmup", new_callable=AsyncMock) as warmup,
                    patch("builtins.print"),
                ):
                    await main.on_ready()
                    if provider == "openai":
                        self.assertIsNone(main.ollama_warmup_task)
                        warmup.assert_not_awaited()
                    else:
                        await main.ollama_warmup_task
                        warmup.assert_awaited_once()

    async def test_direct_warmup_never_calls_api_in_openai_mode(self) -> None:
        with (
            patch.object(llm, "LLM_PROVIDER", "openai"),
            patch.object(llm, "_post_ollama", new_callable=AsyncMock) as local,
            patch.object(llm, "_post_openai", new_callable=AsyncMock) as cloud,
        ):
            await llm.warm_up_model()
        local.assert_not_awaited()
        cloud.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
