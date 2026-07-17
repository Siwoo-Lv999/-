import json
from pathlib import Path
import re

import aiohttp

from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS


PROMPT_PATH = Path(__file__).parent / "prompts" / "personality.txt"
STYLE_EXAMPLES_PATH = Path(__file__).parent / "prompts" / "style_examples.json"


class LlmConnectionError(Exception):
    pass


class LlmTimeoutError(Exception):
    pass


class LlmResponseError(Exception):
    pass


EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]"
)


def load_system_prompt() -> str:
    try:
        prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise LlmResponseError("캐릭터 프롬프트를 읽을 수 없습니다.") from error

    if not prompt:
        raise LlmResponseError("캐릭터 프롬프트가 비어 있습니다.")
    return prompt


def load_style_examples(user_message: str) -> list[dict[str, str]]:
    try:
        raw_examples = json.loads(STYLE_EXAMPLES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LlmResponseError("말투 예시를 읽을 수 없습니다.") from error

    if not isinstance(raw_examples, list):
        raise LlmResponseError("말투 예시 형식이 올바르지 않습니다.")

    normalized_message = user_message.replace(" ", "").lower()
    for item in raw_examples:
        if not isinstance(item, dict):
            raise LlmResponseError("말투 예시 형식이 올바르지 않습니다.")
        keywords = item.get("keywords")
        example_user = item.get("user")
        example_assistant = item.get("assistant")
        if (
            not isinstance(keywords, list)
            or not all(isinstance(keyword, str) for keyword in keywords)
            or not isinstance(example_user, str)
            or not isinstance(example_assistant, str)
        ):
            raise LlmResponseError("말투 예시 형식이 올바르지 않습니다.")

        if any(
            keyword.replace(" ", "").lower() in normalized_message
            for keyword in keywords
        ):
            return [
                {"role": "user", "content": example_user},
                {"role": "assistant", "content": example_assistant},
            ]

    return []


def normalize_persona_reply(reply: str) -> str:
    reply = EMOJI_PATTERN.sub("", reply)

    title_seen = False

    def keep_first_title(_: re.Match[str]) -> str:
        nonlocal title_seen
        if title_seen:
            return ""
        title_seen = True
        return "선생님"

    reply = re.sub("선생님", keep_first_title, reply)
    if not title_seen:
        reply = f"선생님, {reply}"

    reply = re.sub(r"\s+([,.?!])", r"\1", reply)
    reply = re.sub(r",\s*([,.?!])", r"\1", reply)
    reply = re.sub(r" {2,}", " ", reply)
    return reply.strip()


async def generate_reply(
    user_message: str, conversation_history: list[dict[str, str]] | None = None
) -> str:
    system_prompt = load_system_prompt()
    style_example = load_style_examples(user_message)
    if style_example:
        system_prompt += (
            "\n\n# 현재 상황과 가장 가까운 말투 예시\n\n"
            f"사용자: {style_example[0]['content']}\n"
            f"케이: {style_example[1]['content']}\n\n"
            "위 문장을 그대로 반복할 필요는 없지만 같은 성격과 말투로 답하세요. "
            "예시의 반응 범위를 벗어난 새 화제, 건강 추측, 추가 질문을 "
            "덧붙이지 마세요."
        )

    messages = [{"role": "system", "content": system_prompt}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0.1,
            "top_p": 0.8,
        },
    }
    timeout = aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT_SECONDS)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/chat", json=payload
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

    reply = data.get("message", {}).get("content", "").strip()
    if not reply:
        raise LlmResponseError("Ollama가 빈 응답을 반환했습니다.")
    return normalize_persona_reply(reply)
