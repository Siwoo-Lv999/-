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


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
).rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b").strip()
OLLAMA_KEEP_ALIVE = read_keep_alive_environment()
OLLAMA_WARMUP_ON_START = read_bool_environment(
    "OLLAMA_WARMUP_ON_START", True
)
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/bot.db"))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = PROJECT_ROOT / DATABASE_PATH
MODERATION_CONFIG_PATH = Path(
    os.getenv("MODERATION_CONFIG_PATH", "config/moderation.yml")
)
if not MODERATION_CONFIG_PATH.is_absolute():
    MODERATION_CONFIG_PATH = PROJECT_ROOT / MODERATION_CONFIG_PATH

IGNORE_BOT_MESSAGES = read_bool_environment("IGNORE_BOT_MESSAGES", True)
MAX_CONCURRENT_LLM_REQUESTS = 1

try:
    OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
except ValueError as error:
    raise RuntimeError("OLLAMA_TIMEOUT_SECONDS는 정수여야 합니다.") from error
try:
    OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
except ValueError as error:
    raise RuntimeError("OLLAMA_NUM_CTX는 정수여야 합니다.") from error
try:
    USER_COOLDOWN_SECONDS = float(os.getenv("USER_COOLDOWN_SECONDS", "2"))
except ValueError as error:
    raise RuntimeError("USER_COOLDOWN_SECONDS는 숫자여야 합니다.") from error
try:
    CONVERSATION_MAINTENANCE_DELAY_SECONDS = float(
        os.getenv("CONVERSATION_MAINTENANCE_DELAY_SECONDS", "5")
    )
except ValueError as error:
    raise RuntimeError(
        "CONVERSATION_MAINTENANCE_DELAY_SECONDS는 숫자여야 합니다."
    ) from error
try:
    CONVERSATION_RETENTION_DAYS = int(
        os.getenv("CONVERSATION_RETENTION_DAYS", "0")
    )
except ValueError as error:
    raise RuntimeError("CONVERSATION_RETENTION_DAYS는 정수여야 합니다.") from error

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인해 주세요.")

if not OLLAMA_MODEL:
    raise RuntimeError("OLLAMA_MODEL이 설정되지 않았습니다. .env 파일을 확인해 주세요.")

if OLLAMA_TIMEOUT_SECONDS <= 0:
    raise RuntimeError("OLLAMA_TIMEOUT_SECONDS는 1 이상의 정수여야 합니다.")

if OLLAMA_NUM_CTX < 2048:
    raise RuntimeError("OLLAMA_NUM_CTX는 2048 이상의 정수여야 합니다.")

if USER_COOLDOWN_SECONDS < 0:
    raise RuntimeError("USER_COOLDOWN_SECONDS는 0 이상의 숫자여야 합니다.")

if CONVERSATION_MAINTENANCE_DELAY_SECONDS < 0:
    raise RuntimeError(
        "CONVERSATION_MAINTENANCE_DELAY_SECONDS는 0 이상의 숫자여야 합니다."
    )

if CONVERSATION_RETENTION_DAYS < 0:
    raise RuntimeError(
        "CONVERSATION_RETENTION_DAYS는 0 이상의 정수여야 합니다."
    )
