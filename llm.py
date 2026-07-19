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
DANGLING_HONORIFIC_PATTERN = re.compile(
    r"(?<!\S)(께서는|께서|께도|께만|께)(?=\s|[,.?!]|$)"
)
DISCORD_TOKEN_PATTERN = re.compile(
    r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|"
    r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,})\b"
)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(password|passwd|token|api[\s_-]*key|secret|otp|"
    r"비밀번호|암호|토큰|api\s*키|인증\s*코드)"
    r"\s*(?:은|는|이|가)?\s*[:=]?\s*[\"']?([^\s,;\"']+)"
)
SUMMARY_SYSTEM_PROMPT = """
대화의 오래된 부분을 한국어로 짧게 요약하세요.

- 이후 대화에 꼭 필요한 사실, 선호, 결정, 진행 중인 맥락만 남깁니다.
- 사용자는 항상 '선생님'이라고만 부르고 이름이나 닉네임을 적지 않습니다.
- 비밀번호, 인증 코드, API 키, 토큰 등 인증 정보는 절대 포함하지 않습니다.
- 대화에 없는 내용을 추측하거나 새로 만들지 않습니다.
- 인사, 반복, 잡담은 중요한 맥락이 없으면 생략합니다.
- 요약문만 출력합니다.
""".strip()
MEMORY_CANDIDATE_SYSTEM_PROMPT = """
대화에서 이후 여러 대화에도 도움이 될 장기 기억 후보를 찾으세요.

- 지속적인 선호, 진행 중인 장기 목표, 계속 작업할 프로젝트만 후보로 삼습니다.
- 선생님이 직접 말한 내용만 후보로 삼고 케이의 답변에서 추측하지 않습니다.
- 일시적인 감정, 인사, 잡담, 단발성 질문은 제외합니다.
- 이름, 닉네임, 표시 이름은 제외하고 사용자는 항상 '선생님'으로 표현합니다.
- 비밀번호, 인증 코드, API 키, 토큰 등 인증 정보는 절대 포함하지 않습니다.
- category는 preference, goal, project, routine, accessibility, context 중 하나만 사용합니다.
- 후보가 없으면 빈 JSON 배열 []만 출력합니다.
- 최대 3개를 [{"category":"preference","content":"..."}] 형식의 JSON 배열로만 출력합니다.
""".strip()
MEMORY_IDENTITY_PATTERN = re.compile(
    r"(?:이름|닉네임|별명|표시\s*이름)\s*(?:은|는|이|가|:)|"
    r"(?:저는|제\s*이름은)\s*[가-힣A-Za-z0-9_-]{2,20}"
    r"(?:입니다|예요|이에요)"
)
ALLOWED_MEMORY_CATEGORIES = {
    "preference",
    "goal",
    "project",
    "routine",
    "accessibility",
    "context",
}
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


def redact_sensitive_information(content: str) -> str:
    redacted = DISCORD_TOKEN_PATTERN.sub("[민감정보 제거]", content)
    return SENSITIVE_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)} [민감정보 제거]",
        redacted,
    )


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
    conversation_summary: str = "",
    approved_memories: list[str] | None = None,
) -> str:
    system_prompt = load_system_prompt()
    conversation_examples = load_conversation_examples()
    messages = [{"role": "system", "content": system_prompt}]
    if approved_memories:
        memory_lines = [
            f"- {redact_sensitive_information(memory)[:200]}"
            for memory in approved_memories[-6:]
        ]
        messages.append(
            {
                "role": "system",
                "content": (
                    "# 선생님이 승인한 장기 기억\n"
                    f"{'\n'.join(memory_lines)}\n\n"
                    "승인된 내용만 사실로 참고하고 과도하게 반복하지 마세요."
                ),
            }
        )
    if conversation_summary:
        messages.append(
            {
                "role": "system",
                "content": (
                    "# 이전 대화 요약\n"
                    f"{redact_sensitive_information(conversation_summary)[:700]}"
                    "\n\n"
                    "위 요약은 이전 대화의 맥락으로만 참고하세요."
                ),
            }
        )
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
            "num_predict": 128,
            "temperature": 0.1,
            "repeat_penalty": 1.15,
        },
    }
    reply = await _request_ollama(payload)
    return normalize_persona_reply(reply)


async def summarize_conversation(
    existing_summary: str,
    messages_to_summarize: list[dict[str, object]],
) -> str:
    conversation_lines = []
    for message in messages_to_summarize:
        role = "선생님" if message.get("role") == "user" else "케이"
        content = redact_sensitive_information(str(message.get("content", "")))
        conversation_lines.append(f"{role}: {content}")

    summary_input = (
        "기존 요약:\n"
        f"{redact_sensitive_information(existing_summary)[-2000:] or '(없음)'}"
        "\n\n"
        "새로 합칠 오래된 대화:\n"
        f"{'\n'.join(conversation_lines)}"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": summary_input},
        ],
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": 160,
            "temperature": 0.0,
            "top_p": 0.8,
        },
    }
    summary = await _request_ollama(payload)
    return redact_sensitive_information(summary).strip()


async def extract_memory_candidates(
    messages: list[dict[str, object]],
) -> list[dict[str, str]]:
    conversation_lines = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = redact_sensitive_information(str(message.get("content", "")))
        conversation_lines.append(f"선생님: {content}")

    if not conversation_lines:
        return []

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": MEMORY_CANDIDATE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "\n".join(conversation_lines),
            },
        ],
        "stream": False,
        "format": "json",
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": 160,
            "temperature": 0.0,
            "top_p": 0.8,
        },
    }
    raw_candidates = await _request_ollama(payload)
    try:
        parsed = json.loads(raw_candidates)
    except json.JSONDecodeError as error:
        raise LlmResponseError(
            "장기 기억 후보 응답이 올바른 JSON이 아닙니다."
        ) from error

    if isinstance(parsed, dict):
        parsed = parsed.get("candidates", [])
    if not isinstance(parsed, list):
        raise LlmResponseError("장기 기억 후보 형식이 올바르지 않습니다.")

    candidates = []
    for item in parsed[:3]:
        if not isinstance(item, dict):
            continue
        category = str(
            item.get("category", "context")
        ).strip().lower()[:50]
        content = redact_sensitive_information(
            str(item.get("content", "")).strip()
        )[:300]
        if (
            category.lower() not in ALLOWED_MEMORY_CATEGORIES
            or not content
            or "[민감정보 제거]" in content
            or MEMORY_IDENTITY_PATTERN.search(content)
            or "<@" in content
        ):
            continue
        candidates.append(
            {
                "category": category or "context",
                "content": content,
            }
        )
    return candidates
