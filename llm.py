import json
from pathlib import Path
import random
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
_last_direct_replies: dict[str, str] = {}
_direct_reply_pools: dict[str, list[str]] = {}


def load_system_prompt() -> str:
    try:
        prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise LlmResponseError("캐릭터 프롬프트를 읽을 수 없습니다.") from error

    if not prompt:
        raise LlmResponseError("캐릭터 프롬프트가 비어 있습니다.")
    return prompt


def find_style_example(user_message: str) -> dict[str, object] | None:
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
        direct = item.get("direct", False)
        direct_replies = item.get("direct_replies", [])
        if (
            not isinstance(keywords, list)
            or not all(isinstance(keyword, str) for keyword in keywords)
            or not isinstance(example_user, str)
            or not isinstance(example_assistant, str)
            or not isinstance(direct, bool)
            or not isinstance(direct_replies, list)
            or not all(isinstance(reply, str) for reply in direct_replies)
        ):
            raise LlmResponseError("말투 예시 형식이 올바르지 않습니다.")

        if any(
            keyword.replace(" ", "").lower() in normalized_message
            for keyword in keywords
        ):
            normalized_keywords = {
                re.sub(r"[\s~!?.…]+", "", keyword.lower())
                for keyword in keywords
            }
            direct_match = direct and re.sub(
                r"[\s~!?.…]+", "", user_message.lower()
            ) in normalized_keywords
            return {
                "user": example_user,
                "assistant": example_assistant,
                "direct": direct_match,
                "direct_replies": direct_replies,
            }

    return None


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


def select_direct_reply(style_example: dict[str, object]) -> str:
    example_key = str(style_example["user"])
    replies = [str(reply) for reply in style_example["direct_replies"]]
    if not replies:
        replies = [str(style_example["assistant"])]

    previous_reply = _last_direct_replies.get(example_key)
    reply_pool = _direct_reply_pools.get(example_key)
    if not reply_pool:
        reply_pool = replies.copy()
        random.shuffle(reply_pool)
        if (
            previous_reply is not None
            and len(reply_pool) > 1
            and reply_pool[-1] == previous_reply
        ):
            reply_pool[0], reply_pool[-1] = reply_pool[-1], reply_pool[0]
        _direct_reply_pools[example_key] = reply_pool

    selected_reply = reply_pool.pop()
    _last_direct_replies[example_key] = selected_reply
    return selected_reply


async def generate_reply(
    user_message: str, conversation_history: list[dict[str, str]] | None = None
) -> str:
    system_prompt = load_system_prompt()
    style_example = find_style_example(user_message)
    if style_example and style_example["direct"]:
        return normalize_persona_reply(select_direct_reply(style_example))

    if style_example:
        system_prompt += (
            "\n\n# 현재 상황과 가장 가까운 말투 예시\n\n"
            f"사용자: {style_example['user']}\n"
            f"케이: {style_example['assistant']}\n\n"
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
        "keep_alive": "30m",
        "options": {
            "num_ctx": 4096,
            "num_predict": 128 if style_example else 384,
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
