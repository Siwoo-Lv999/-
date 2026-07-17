import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
).rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b").strip()
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/bot.db"))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = PROJECT_ROOT / DATABASE_PATH

try:
    OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
except ValueError as error:
    raise RuntimeError("OLLAMA_TIMEOUT_SECONDS는 정수여야 합니다.") from error

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인해 주세요.")

if not OLLAMA_MODEL:
    raise RuntimeError("OLLAMA_MODEL이 설정되지 않았습니다. .env 파일을 확인해 주세요.")

if OLLAMA_TIMEOUT_SECONDS <= 0:
    raise RuntimeError("OLLAMA_TIMEOUT_SECONDS는 1 이상의 정수여야 합니다.")
