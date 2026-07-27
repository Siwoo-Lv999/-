import asyncio
import json
import random
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
DANGLING_HONORIFIC_PATTERN = re.compile(
    r"(?<!\S)(께서는|께서|께도|께만|께)(?=\s|[,.?!]|$)"
)
CUTE_COMPLIMENT_PATTERN = re.compile(
    r"^(?:(?:케이(?:짱)?|너|넌|네가|니가))?"
    r"(?:(?:진짜|정말|너무|완전|엄청|되게|무척|좀|개))*"
    r"(?:귀여워|귀엽다|귀엽네|귀엽구나|귀엽군|귀엽잖아|"
    r"귀엽다고|귀여운데|귀여움)$"
)
GREETING_PATTERN = re.compile(
    r"^(?:(?:케이(?:짱)?)(?:아|야)?)?"
    r"(?:안녕|좋은아침|좋은저녁)"
    r"(?:케이(?:짱)?)?$"
)
THANKS_PATTERN = re.compile(
    r"^(?:(?:케이(?:짱)?)(?:아|야)?)?"
    r"(?:고마워|고맙다|고맙네|감사해|감사합니다)$"
)
LAUGHTER_PATTERN = re.compile(r"^(?:히히+|헤헤+|하하+|[ㅋㅎ]{2,})$")
CUTE_REPLIES = (
    "귀, 귀엽다고 하지 말라고요, 선생님! ……갑자기 그런 말을 하면 곤란하잖아요.",
    "아, 진짜…… 또 놀리시는 건가요, 선생님? 그런 말로 반응을 보려고 하지 마세요!",
    "그, 그런 말은 갑자기 왜 하시는 건가요, 선생님?! ……싫다는 뜻은 아니지만요.",
    "제가 어디가 귀엽다는 건가요, 선생님? 정말이지…… 아무렇지도 않으니까 그만 웃으세요.",
)
LAUGHTER_REPLIES = (
    "뭘 그렇게 히죽히죽 웃고 있는 건가요, 선생님?!",
    "왜 혼자 그렇게 웃는 건가요, 선생님? 또 무슨 장난을 꾸미고 있죠?",
    "……그 웃음은 뭔가요, 선생님? 분명 저를 놀릴 생각이죠!",
    "아, 왜 그렇게 웃는 건가요, 선생님?! 수상하잖아요.",
)
GREETING_REPLIES = (
    "안녕하세요, 선생님. 이제야 오셨네요.",
    "오셨군요, 선생님. ……기다린 건 아니니까요.",
    "어서 오세요, 선생님. 오늘도 잘 부탁드리죠.",
)
THANKS_REPLIES = (
    "해야 할 일을 했을 뿐이에요, 선생님. ……그래도 도움이 됐다면 다행이네요.",
    "이 정도로 감사까지 하실 필요는 없어요, 선생님. 맡은 일을 한 것뿐이니까요.",
    "당연한 일을 했을 뿐이에요, 선생님. ……그렇게 말씀해 주시면 나쁘진 않네요.",
)
_last_direct_replies: dict[str, str] = {}
_direct_reply_pools: dict[str, list[str]] = {}
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


def _normalize_short_intent_text(content: str) -> str:
    compact = re.sub(r"[\s~!?.…,，]+", "", content.lower())
    return re.sub(r"[ㅋㅎ]+$", "", compact)


def _select_direct_reply(category: str, replies: tuple[str, ...]) -> str:
    previous_reply = _last_direct_replies.get(category)
    reply_pool = _direct_reply_pools.get(category)
    if not reply_pool:
        reply_pool = list(replies)
        random.shuffle(reply_pool)
        if (
            previous_reply is not None
            and len(reply_pool) > 1
            and reply_pool[-1] == previous_reply
        ):
            reply_pool[0], reply_pool[-1] = reply_pool[-1], reply_pool[0]
        _direct_reply_pools[category] = reply_pool

    selected_reply = reply_pool.pop()
    _last_direct_replies[category] = selected_reply
    return selected_reply


def find_direct_reply(user_message: str) -> str | None:
    compact = re.sub(r"[\s~!?.…,，]+", "", user_message.lower())
    if LAUGHTER_PATTERN.fullmatch(compact):
        return _select_direct_reply("laughter", LAUGHTER_REPLIES)

    compact_without_laughter = _normalize_short_intent_text(user_message)
    if CUTE_COMPLIMENT_PATTERN.fullmatch(compact_without_laughter):
        return _select_direct_reply("cute", CUTE_REPLIES)
    if GREETING_PATTERN.fullmatch(compact_without_laughter):
        return _select_direct_reply("greeting", GREETING_REPLIES)
    if THANKS_PATTERN.fullmatch(compact_without_laughter):
        return _select_direct_reply("thanks", THANKS_REPLIES)
    return None


def normalize_persona_reply(reply: str) -> str:
    reply = EMOJI_PATTERN.sub("", reply)
    reply = re.sub(
        r"(저는|제가)\s+(?:께서는|께서)\s+",
        r"\1 ",
        reply,
    )
    reply = DANGLING_HONORIFIC_PATTERN.sub(
        lambda match: f"선생님{match.group(1)}",
        reply,
    )

    if "선생님" not in reply:
        reply = f"선생님, {reply}"

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
) -> str:
    direct_reply = find_direct_reply(user_message)
    if direct_reply is not None:
        return direct_reply

    system_prompt = load_system_prompt()
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
