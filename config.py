import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent


def read_bool_environment(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name}은 true 또는 false여야 합니다.")


def read_keep_alive_environment() -> int | str:
    raw_value = os.getenv("OLLAMA_KEEP_ALIVE", "-1").strip()
    if not raw_value:
        raise RuntimeError("OLLAMA_KEEP_ALIVE는 비어 있을 수 없습니다.")

    try:
        return int(raw_value)
    except ValueError:
        return raw_value


def read_optional_int_environment(name: str) -> int | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None

    try:
        return int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name}은 정수여야 합니다.") from error


def read_positive_int_environment(
    name: str, default: int, minimum: int = 1
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name}은 정수여야 합니다.") from error
    if value < minimum:
        raise RuntimeError(f"{name}은 {minimum} 이상의 정수여야 합니다.")
    return value


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").strip().lower()
if LLM_PROVIDER not in {"auto", "ollama", "openai"}:
    raise RuntimeError("LLM_PROVIDER는 auto, ollama 또는 openai여야 합니다.")
if LLM_PROVIDER == "auto":
    LLM_PROVIDER = "openai" if OPENAI_API_KEY else "ollama"

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna").strip()
OPENAI_REASONING_EFFORT = os.getenv(
    "OPENAI_REASONING_EFFORT", "none"
).strip().lower()
OPENAI_TIMEOUT_SECONDS = 60
OPENAI_MAX_OUTPUT_TOKENS = 256
OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
).rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b").strip()
OLLAMA_KEEP_ALIVE: int | str = -1
OLLAMA_WARMUP_ON_START = False
OLLAMA_TIMEOUT_SECONDS = 60
OLLAMA_NUM_CTX = 2048

# 선택한 제공자의 설정만 검사한다. 다른 제공자의 설정은 복귀용으로 남겨 둔다.
if LLM_PROVIDER == "openai":
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI를 사용하려면 .env에 OPENAI_API_KEY가 필요합니다.")
    if not OPENAI_MODEL:
        raise RuntimeError("OPENAI_MODEL은 비어 있을 수 없습니다.")
    if OPENAI_REASONING_EFFORT not in {
        "", "none", "low", "medium", "high", "xhigh", "max"
    }:
        raise RuntimeError(
            "OPENAI_REASONING_EFFORT는 비우거나 none, low, medium, high, xhigh, max여야 합니다."
        )
    OPENAI_TIMEOUT_SECONDS = read_positive_int_environment(
        "OPENAI_TIMEOUT_SECONDS", 60
    )
    OPENAI_MAX_OUTPUT_TOKENS = read_positive_int_environment(
        "OPENAI_MAX_OUTPUT_TOKENS", 256
    )
else:
    if not OLLAMA_MODEL:
        raise RuntimeError("OLLAMA_MODEL이 설정되지 않았습니다. .env 파일을 확인해 주세요.")
    OLLAMA_KEEP_ALIVE = read_keep_alive_environment()
    OLLAMA_WARMUP_ON_START = read_bool_environment(
        "OLLAMA_WARMUP_ON_START", True
    )
    OLLAMA_TIMEOUT_SECONDS = read_positive_int_environment(
        "OLLAMA_TIMEOUT_SECONDS", 60
    )
    OLLAMA_NUM_CTX = read_positive_int_environment(
        "OLLAMA_NUM_CTX", 2048, minimum=2048
    )

LLM_MODEL = OPENAI_MODEL if LLM_PROVIDER == "openai" else OLLAMA_MODEL
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/bot.db"))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = PROJECT_ROOT / DATABASE_PATH
AUTO_ROLES_PATH = Path(
    os.getenv("AUTO_ROLES_PATH", "config/auto_roles.yml")
)
if not AUTO_ROLES_PATH.is_absolute():
    AUTO_ROLES_PATH = PROJECT_ROOT / AUTO_ROLES_PATH

IGNORE_BOT_MESSAGES = read_bool_environment("IGNORE_BOT_MESSAGES", True)
MAX_CONCURRENT_LLM_REQUESTS = 1
GITHUB_WEBHOOK_ENABLED = read_bool_environment(
    "GITHUB_WEBHOOK_ENABLED", False
)
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
GITHUB_WEBHOOK_CHANNEL_ID = read_optional_int_environment(
    "GITHUB_WEBHOOK_CHANNEL_ID"
)
GITHUB_WEBHOOK_HOST = os.getenv(
    "GITHUB_WEBHOOK_HOST", "127.0.0.1"
).strip()
GITHUB_WEBHOOK_CHANNELS_PATH = Path(
    os.getenv(
        "GITHUB_WEBHOOK_CHANNELS_PATH",
        "config/github_channels.yml",
    )
)
if not GITHUB_WEBHOOK_CHANNELS_PATH.is_absolute():
    GITHUB_WEBHOOK_CHANNELS_PATH = (
        PROJECT_ROOT / GITHUB_WEBHOOK_CHANNELS_PATH
    )
GITHUB_WEBHOOK_BRANCHES_PATH = Path(
    os.getenv(
        "GITHUB_WEBHOOK_BRANCHES_PATH",
        "config/github_branches.yml",
    )
)
if not GITHUB_WEBHOOK_BRANCHES_PATH.is_absolute():
    GITHUB_WEBHOOK_BRANCHES_PATH = (
        PROJECT_ROOT / GITHUB_WEBHOOK_BRANCHES_PATH
    )

try:
    USER_COOLDOWN_SECONDS = float(os.getenv("USER_COOLDOWN_SECONDS", "2"))
except ValueError as error:
    raise RuntimeError("USER_COOLDOWN_SECONDS는 숫자여야 합니다.") from error
try:
    CONVERSATION_RETENTION_DAYS = int(
        os.getenv("CONVERSATION_RETENTION_DAYS", "0")
    )
except ValueError as error:
    raise RuntimeError("CONVERSATION_RETENTION_DAYS는 정수여야 합니다.") from error
try:
    GITHUB_WEBHOOK_PORT = int(os.getenv("GITHUB_WEBHOOK_PORT", "8080"))
except ValueError as error:
    raise RuntimeError("GITHUB_WEBHOOK_PORT는 정수여야 합니다.") from error

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인해 주세요.")

if USER_COOLDOWN_SECONDS < 0:
    raise RuntimeError("USER_COOLDOWN_SECONDS는 0 이상의 숫자여야 합니다.")

if CONVERSATION_RETENTION_DAYS < 0:
    raise RuntimeError(
        "CONVERSATION_RETENTION_DAYS는 0 이상의 정수여야 합니다."
    )

if not GITHUB_WEBHOOK_HOST:
    raise RuntimeError("GITHUB_WEBHOOK_HOST는 비어 있을 수 없습니다.")

if not 1 <= GITHUB_WEBHOOK_PORT <= 65535:
    raise RuntimeError("GITHUB_WEBHOOK_PORT는 1부터 65535 사이여야 합니다.")

if (
    GITHUB_WEBHOOK_CHANNEL_ID is not None
    and GITHUB_WEBHOOK_CHANNEL_ID <= 0
):
    raise RuntimeError("GITHUB_WEBHOOK_CHANNEL_ID는 양의 정수여야 합니다.")

if GITHUB_WEBHOOK_ENABLED:
    if not GITHUB_WEBHOOK_SECRET:
        raise RuntimeError(
            "GitHub Webhook을 사용하려면 GITHUB_WEBHOOK_SECRET이 필요합니다."
        )
