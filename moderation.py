import logging
import re
from pathlib import Path

import yaml

from config import MODERATION_CONFIG_PATH


LOG_PATH = Path(__file__).resolve().parent / "logs" / "moderation.log"


class ModerationConfigError(Exception):
    pass


def _load_rules() -> list[tuple[str, str, list[re.Pattern[str]]]]:
    try:
        raw_config = yaml.safe_load(
            MODERATION_CONFIG_PATH.read_text(encoding="utf-8")
        )
    except (OSError, yaml.YAMLError) as error:
        raise ModerationConfigError(
            "안전 필터 설정을 읽을 수 없습니다."
        ) from error

    categories = (
        raw_config.get("categories") if isinstance(raw_config, dict) else None
    )
    if not isinstance(categories, dict):
        raise ModerationConfigError("안전 필터 설정 형식이 올바르지 않습니다.")

    rules = []
    for category, settings in categories.items():
        if not isinstance(category, str) or not isinstance(settings, dict):
            raise ModerationConfigError(
                "안전 필터 분류 형식이 올바르지 않습니다."
            )

        response = settings.get("response")
        patterns = settings.get("patterns")
        if (
            not isinstance(response, str)
            or not isinstance(patterns, list)
            or not all(isinstance(pattern, str) for pattern in patterns)
        ):
            raise ModerationConfigError(
                f"안전 필터 '{category}' 설정이 올바르지 않습니다."
            )

        try:
            compiled_patterns = [
                re.compile(pattern, re.IGNORECASE) for pattern in patterns
            ]
        except re.error as error:
            raise ModerationConfigError(
                f"안전 필터 '{category}' 정규식이 올바르지 않습니다."
            ) from error
        rules.append((category, response, compiled_patterns))

    return rules


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("kei.moderation")
    if logger.handlers:
        return logger

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s category=%(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    return logger


MODERATION_RULES = _load_rules()


def check_message(content: str) -> tuple[str, str] | None:
    for category, response, patterns in MODERATION_RULES:
        if any(pattern.search(content) for pattern in patterns):
            _get_logger().warning(category)
            return category, response
    return None
