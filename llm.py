import asyncio
import json
import re
from pathlib import Path

import aiohttp

from config import (
    MAX_CONCURRENT_LLM_REQUESTS,
    OLLAMA_BASE_URL,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_MODEL,
    OLLAMA_NUM_CTX,
    OLLAMA_TIMEOUT_SECONDS,
)


PROMPT_PATH = Path(__file__).parent / "prompts" / "personality.txt"
CONVERSATION_EXAMPLES_PATH = (
    Path(__file__).parent / "prompts" / "conversation_examples.json"
)


class LlmConnectionError(Exception):
    pass


class LlmTimeoutError(Exception):
    pass


class LlmResponseError(Exception):
    pass


EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]"
)
_llm_request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_REQUESTS)


def load_system_prompt() -> str:
    try:
        prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise LlmResponseError("캐릭터 프롬프트를 읽을 수 없습니다.") from error

    if not prompt:
        raise LlmResponseError("캐릭터 프롬프트가 비어 있습니다.")
    return prompt


def load_conversation_examples() -> list[dict[str, str]]:
    try:
        raw_examples = json.loads(
            CONVERSATION_EXAMPLES_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise LlmResponseError("예시 대화를 읽을 수 없습니다.") from error

    if not isinstance(raw_examples, list):
        raise LlmResponseError("예시 대화 형식이 올바르지 않습니다.")

    messages: list[dict[str, str]] = []
    for item in raw_examples:
        if not isinstance(item, dict):
            raise LlmResponseError("예시 대화 형식이 올바르지 않습니다.")
        example_user = item.get("user")
        example_assistant = item.get("assistant")
        if (
            not isinstance(example_user, str)
            or not isinstance(example_assistant, str)
            or not example_user.strip()
            or not example_assistant.strip()
        ):
            raise LlmResponseError("예시 대화 형식이 올바르지 않습니다.")
        messages.extend(
            (
                {"role": "user", "content": example_user.strip()},
                {"role": "assistant", "content": example_assistant.strip()},
            )
        )
    return messages


def normalize_persona_reply(reply: str) -> str:
    reply = EMOJI_PATTERN.sub("", reply)
    reply = re.sub(r"\s+([,.?!])", r"\1", reply)
    reply = re.sub(r",\s*([,.?!])", r"\1", reply)
    reply = re.sub(r" {2,}", " ", reply)
    return reply.strip()


async def _post_ollama(
    endpoint: str, payload: dict[str, object]
) -> dict[str, object]:
    timeout = aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT_SECONDS)

    try:
        async with _llm_request_semaphore:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{OLLAMA_BASE_URL}{endpoint}", json=payload
                ) as response:
                    if response.status != 200:
                        detail = (await response.text())[:200]
                        raise LlmResponseError(
                            f"Ollama API 오류 ({response.status}): {detail}"
                        )
                    data = await response.json()
    except TimeoutError as error:
        raise LlmTimeoutError("Ollama 응답 시간이 초과되었습니다.") from error
    except aiohttp.ClientError as error:
        raise LlmConnectionError("Ollama에 연결할 수 없습니다.") from error

    if not isinstance(data, dict):
        raise LlmResponseError("Ollama가 올바르지 않은 응답을 반환했습니다.")
    return data


async def _request_ollama(payload: dict[str, object]) -> str:
    data = await _post_ollama("/api/chat", payload)
    content = data.get("message", {}).get("content", "").strip()
    if not content:
        raise LlmResponseError("Ollama가 빈 응답을 반환했습니다.")
    return content


async def warm_up_model() -> None:
    await _post_ollama(
        "/api/generate",
        {
            "model": OLLAMA_MODEL,
            "prompt": "",
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        },
    )


async def generate_reply(
    user_message: str,
    conversation_history: list[dict[str, str]] | None = None,
    speaker_name: str | None = None,
) -> str:
    system_prompt = load_system_prompt()
    if speaker_name:
        safe_speaker_name = re.sub(r"\s+", " ", speaker_name).strip()[:80]
        if safe_speaker_name:
            system_prompt += (
                f'\n현재 대화 상대의 표시 이름은 "{safe_speaker_name}"입니다.'
            )
    conversation_examples = load_conversation_examples()
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_examples)
    if conversation_history:
        history_budget = 1200
        trimmed_history = []
        for history_item in reversed(conversation_history):
            content = history_item.get("content", "")[:600]
            if len(content) > history_budget and trimmed_history:
                break
            content = content[-history_budget:]
            trimmed_history.append(
                {"role": history_item["role"], "content": content}
            )
            history_budget -= len(content)
            if history_budget <= 0:
                break
        messages.extend(reversed(trimmed_history))
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": 96,
            "temperature": 0.1,
            "repeat_penalty": 1.15,
        },
    }
    reply = await _request_ollama(payload)
    return normalize_persona_reply(reply)
