import asyncio
from pathlib import Path

import yaml

from config import AUTO_ROLES_PATH


_auto_roles: dict[int, int] = {}
_auto_roles_lock = asyncio.Lock()


def _parse_positive_id(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{label}는 양의 정수여야 합니다.")

    if isinstance(value, int):
        parsed_value = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed_value = int(value.strip())
    else:
        raise RuntimeError(f"{label}는 양의 정수여야 합니다.")

    if parsed_value <= 0:
        raise RuntimeError(f"{label}는 양의 정수여야 합니다.")
    return parsed_value


def load_auto_roles(config_path: Path) -> dict[int, int]:
    try:
        raw_config = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise RuntimeError(
            f"자동 역할 설정 파일을 읽지 못했습니다: {config_path}"
        ) from error

    try:
        config_data = yaml.safe_load(raw_config)
    except yaml.YAMLError as error:
        raise RuntimeError(
            f"자동 역할 설정 YAML이 올바르지 않습니다: {config_path}"
        ) from error

    if config_data is None:
        return {}
    if not isinstance(config_data, dict):
        raise RuntimeError("자동 역할 설정의 최상위 값은 객체여야 합니다.")

    raw_guilds = config_data.get("guilds", {})
    if not isinstance(raw_guilds, dict):
        raise RuntimeError("자동 역할 설정의 guilds는 객체여야 합니다.")

    auto_roles: dict[int, int] = {}
    for raw_guild_id, raw_role_id in raw_guilds.items():
        guild_id = _parse_positive_id(raw_guild_id, "서버 ID")
        role_id = _parse_positive_id(raw_role_id, "역할 ID")
        if guild_id in auto_roles:
            raise RuntimeError(f"서버 설정이 중복되었습니다: {guild_id}")
        auto_roles[guild_id] = role_id
    return auto_roles


def _write_auto_roles(
    config_path: Path, auto_roles: dict[int, int]
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(
        {
            "guilds": {
                str(guild_id): role_id
                for guild_id, role_id in sorted(auto_roles.items())
            }
        },
        allow_unicode=True,
        sort_keys=False,
    )
    temporary_path = config_path.with_name(f"{config_path.name}.tmp")

    try:
        temporary_path.write_text(serialized, encoding="utf-8")
        temporary_path.replace(config_path)
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(
            f"자동 역할 설정 파일을 저장하지 못했습니다: {config_path}"
        ) from error


def initialize_auto_roles() -> None:
    auto_roles = load_auto_roles(AUTO_ROLES_PATH)
    _auto_roles.clear()
    _auto_roles.update(auto_roles)


def get_auto_role_id(guild_id: int) -> int | None:
    return _auto_roles.get(guild_id)


async def set_auto_role(guild_id: int, role_id: int) -> None:
    guild_id = _parse_positive_id(guild_id, "서버 ID")
    role_id = _parse_positive_id(role_id, "역할 ID")

    async with _auto_roles_lock:
        auto_roles = await asyncio.to_thread(
            load_auto_roles, AUTO_ROLES_PATH
        )
        auto_roles[guild_id] = role_id
        await asyncio.to_thread(
            _write_auto_roles, AUTO_ROLES_PATH, auto_roles
        )
        _auto_roles.clear()
        _auto_roles.update(auto_roles)


async def remove_auto_role(guild_id: int) -> bool:
    guild_id = _parse_positive_id(guild_id, "서버 ID")

    async with _auto_roles_lock:
        auto_roles = await asyncio.to_thread(
            load_auto_roles, AUTO_ROLES_PATH
        )
        changed = auto_roles.pop(guild_id, None) is not None
        if changed:
            await asyncio.to_thread(
                _write_auto_roles, AUTO_ROLES_PATH, auto_roles
            )
        _auto_roles.clear()
        _auto_roles.update(auto_roles)
    return changed
