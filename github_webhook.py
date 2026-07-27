import asyncio
from collections import deque
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from aiohttp import web
import discord
import yaml

from config import (
    GITHUB_WEBHOOK_BRANCHES_PATH,
    GITHUB_WEBHOOK_CHANNEL_ID,
    GITHUB_WEBHOOK_CHANNELS_PATH,
    GITHUB_WEBHOOK_ENABLED,
    GITHUB_WEBHOOK_HOST,
    GITHUB_WEBHOOK_PORT,
    GITHUB_WEBHOOK_SECRET,
)


WEBHOOK_PATH = "/github/webhook"
HEALTH_PATH = "/github/health"
MAX_DELIVERY_IDS = 500
MAX_COMMIT_LINES = 5

DISCORD_CLIENT_KEY = web.AppKey("discord_client", discord.Client)
WEBHOOK_SECRET_KEY = web.AppKey("webhook_secret", str)
DELIVERY_IDS_KEY = web.AppKey("delivery_ids", set[str])
DELIVERY_ORDER_KEY = web.AppKey("delivery_order", deque[str])
NOTIFICATION_TASKS_KEY = web.AppKey(
    "notification_tasks", set[asyncio.Task[None]]
)
REPOSITORY_CHANNELS_KEY = web.AppKey(
    "repository_channels", dict[str, int | None]
)
REPOSITORY_BRANCHES_KEY = web.AppKey(
    "repository_branches", dict[str, set[str]]
)

_runner: web.AppRunner | None = None
_repository_channels: dict[str, int | None] = {}
_repository_channels_lock = asyncio.Lock()
_repository_branches: dict[str, set[str]] = {}
_repository_branches_lock = asyncio.Lock()


def verify_github_signature(
    payload_body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    if not signature_header or not secret:
        return False

    expected_signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)


def normalize_repository_name(repository_name: str) -> str:
    normalized_name = repository_name.strip()
    repository_parts = normalized_name.split("/")
    if (
        len(repository_parts) != 2
        or not all(part.strip() for part in repository_parts)
    ):
        raise RuntimeError(
            "GitHub 저장소 이름은 owner/repository 형식이어야 합니다."
        )
    return normalized_name.casefold()


def _validate_channel_id(channel_id: int) -> int:
    if isinstance(channel_id, bool) or channel_id <= 0:
        raise RuntimeError("Discord 채널 ID가 올바르지 않습니다.")
    return channel_id


def load_repository_channels(config_path: Path) -> dict[str, int | None]:
    try:
        raw_config = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise RuntimeError(
            f"GitHub 채널 설정 파일을 읽지 못했습니다: {config_path}"
        ) from error

    try:
        config_data = yaml.safe_load(raw_config)
    except yaml.YAMLError as error:
        raise RuntimeError(
            f"GitHub 채널 설정 YAML이 올바르지 않습니다: {config_path}"
        ) from error

    if config_data is None:
        return {}
    if not isinstance(config_data, dict):
        raise RuntimeError(
            "GitHub 채널 설정의 최상위 값은 객체여야 합니다."
        )

    raw_repositories = config_data.get("repositories", {})
    if not isinstance(raw_repositories, dict):
        raise RuntimeError(
            "GitHub 채널 설정의 repositories는 객체여야 합니다."
        )

    repository_channels: dict[str, int | None] = {}
    for raw_name, raw_channel_id in raw_repositories.items():
        if not isinstance(raw_name, str):
            raise RuntimeError("GitHub 저장소 이름은 문자열이어야 합니다.")

        try:
            repository_name = normalize_repository_name(raw_name)
        except RuntimeError as error:
            raise RuntimeError(
                "GitHub 저장소 이름은 owner/repository 형식이어야 합니다: "
                f"{raw_name}"
            ) from error

        if raw_channel_id is None:
            channel_id = None
        elif (
            isinstance(raw_channel_id, int)
            and not isinstance(raw_channel_id, bool)
        ):
            channel_id = raw_channel_id
        elif (
            isinstance(raw_channel_id, str)
            and raw_channel_id.strip().isdigit()
        ):
            channel_id = int(raw_channel_id.strip())
        else:
            raise RuntimeError(
                f"{raw_name}의 Discord 채널 ID는 정수여야 합니다."
            )

        if channel_id is not None:
            try:
                channel_id = _validate_channel_id(channel_id)
            except RuntimeError as error:
                raise RuntimeError(
                    f"{raw_name}의 Discord 채널 ID가 올바르지 않습니다."
                ) from error

        if repository_name in repository_channels:
            raise RuntimeError(
                f"GitHub 저장소 설정이 중복되었습니다: {raw_name}"
            )
        repository_channels[repository_name] = channel_id

    return repository_channels


def _write_repository_channels(
    config_path: Path,
    repository_channels: dict[str, int | None],
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(
        {"repositories": dict(sorted(repository_channels.items()))},
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
            f"GitHub 채널 설정 파일을 저장하지 못했습니다: {config_path}"
        ) from error


def _replace_active_repository_channels(
    repository_channels: dict[str, int | None],
) -> None:
    _repository_channels.clear()
    _repository_channels.update(repository_channels)


async def set_repository_channel(
    repository_name: str,
    channel_id: int,
) -> str:
    normalized_name = normalize_repository_name(repository_name)
    channel_id = _validate_channel_id(channel_id)

    async with _repository_channels_lock:
        repository_channels = await asyncio.to_thread(
            load_repository_channels,
            GITHUB_WEBHOOK_CHANNELS_PATH,
        )
        repository_channels[normalized_name] = channel_id
        await asyncio.to_thread(
            _write_repository_channels,
            GITHUB_WEBHOOK_CHANNELS_PATH,
            repository_channels,
        )
        _replace_active_repository_channels(repository_channels)

    return normalized_name


async def delete_repository_channel(repository_name: str) -> bool:
    normalized_name = normalize_repository_name(repository_name)

    async with _repository_channels_lock:
        repository_channels = await asyncio.to_thread(
            load_repository_channels,
            GITHUB_WEBHOOK_CHANNELS_PATH,
        )
        already_disabled = (
            normalized_name in repository_channels
            and repository_channels[normalized_name] is None
        )
        if not already_disabled:
            repository_channels[normalized_name] = None
            await asyncio.to_thread(
                _write_repository_channels,
                GITHUB_WEBHOOK_CHANNELS_PATH,
                repository_channels,
            )
        _replace_active_repository_channels(repository_channels)

    return not already_disabled


def get_repository_channels() -> dict[str, int | None]:
    return dict(sorted(_repository_channels.items()))


def normalize_branch_name(branch_name: str) -> str:
    normalized_name = branch_name.strip()
    invalid_characters = " ~^:?*[\\"
    if (
        not normalized_name
        or normalized_name == "@"
        or normalized_name.startswith(("-", "/", "."))
        or normalized_name.endswith(("/", ".", ".lock"))
        or ".." in normalized_name
        or "//" in normalized_name
        or "@{" in normalized_name
        or any(
            character in invalid_characters
            or ord(character) < 32
            or ord(character) == 127
            for character in normalized_name
        )
    ):
        raise RuntimeError("올바른 Git 브랜치 이름을 입력해 주세요.")
    return normalized_name


def load_repository_branches(
    config_path: Path,
) -> dict[str, set[str]]:
    try:
        raw_config = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise RuntimeError(
            f"GitHub 브랜치 설정 파일을 읽지 못했습니다: {config_path}"
        ) from error

    try:
        config_data = yaml.safe_load(raw_config)
    except yaml.YAMLError as error:
        raise RuntimeError(
            f"GitHub 브랜치 설정 YAML이 올바르지 않습니다: {config_path}"
        ) from error

    if config_data is None:
        return {}
    if not isinstance(config_data, dict):
        raise RuntimeError(
            "GitHub 브랜치 설정의 최상위 값은 객체여야 합니다."
        )

    raw_repositories = config_data.get("repositories", {})
    if not isinstance(raw_repositories, dict):
        raise RuntimeError(
            "GitHub 브랜치 설정의 repositories는 객체여야 합니다."
        )

    repository_branches: dict[str, set[str]] = {}
    for raw_name, raw_branches in raw_repositories.items():
        if not isinstance(raw_name, str):
            raise RuntimeError("GitHub 저장소 이름은 문자열이어야 합니다.")

        try:
            repository_name = normalize_repository_name(raw_name)
        except RuntimeError as error:
            raise RuntimeError(
                "GitHub 저장소 이름은 owner/repository 형식이어야 합니다: "
                f"{raw_name}"
            ) from error

        if not isinstance(raw_branches, list):
            raise RuntimeError(
                f"{raw_name}의 브랜치 설정은 목록이어야 합니다."
            )

        branches: set[str] = set()
        for raw_branch in raw_branches:
            if not isinstance(raw_branch, str):
                raise RuntimeError(
                    f"{raw_name}의 브랜치 이름은 문자열이어야 합니다."
                )
            try:
                branch_name = normalize_branch_name(raw_branch)
            except RuntimeError as error:
                raise RuntimeError(
                    f"{raw_name}의 브랜치 이름이 올바르지 않습니다: "
                    f"{raw_branch}"
                ) from error
            branches.add(branch_name)

        if repository_name in repository_branches:
            raise RuntimeError(
                f"GitHub 브랜치 설정이 중복되었습니다: {raw_name}"
            )
        repository_branches[repository_name] = branches

    return repository_branches


def _write_repository_branches(
    config_path: Path,
    repository_branches: dict[str, set[str]],
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(
        {
            "repositories": {
                repository_name: sorted(branches)
                for repository_name, branches in sorted(
                    repository_branches.items()
                )
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
            f"GitHub 브랜치 설정 파일을 저장하지 못했습니다: {config_path}"
        ) from error


def _replace_active_repository_branches(
    repository_branches: dict[str, set[str]],
) -> None:
    _repository_branches.clear()
    _repository_branches.update(
        {
            repository_name: set(branches)
            for repository_name, branches in repository_branches.items()
        }
    )


async def add_repository_branch(
    repository_name: str,
    branch_name: str,
) -> tuple[str, str, bool]:
    normalized_repository = normalize_repository_name(repository_name)
    normalized_branch = normalize_branch_name(branch_name)

    async with _repository_branches_lock:
        repository_branches = await asyncio.to_thread(
            load_repository_branches,
            GITHUB_WEBHOOK_BRANCHES_PATH,
        )
        branches = repository_branches.setdefault(
            normalized_repository,
            set(),
        )
        changed = normalized_branch not in branches
        if changed:
            branches.add(normalized_branch)
            await asyncio.to_thread(
                _write_repository_branches,
                GITHUB_WEBHOOK_BRANCHES_PATH,
                repository_branches,
            )
        _replace_active_repository_branches(repository_branches)

    return normalized_repository, normalized_branch, changed


async def delete_repository_branch(
    repository_name: str,
    branch_name: str,
) -> tuple[bool, int | None]:
    normalized_repository = normalize_repository_name(repository_name)
    normalized_branch = normalize_branch_name(branch_name)

    async with _repository_branches_lock:
        repository_branches = await asyncio.to_thread(
            load_repository_branches,
            GITHUB_WEBHOOK_BRANCHES_PATH,
        )
        branches = repository_branches.get(normalized_repository)
        if branches is None or normalized_branch not in branches:
            _replace_active_repository_branches(repository_branches)
            return False, None if branches is None else len(branches)

        branches.remove(normalized_branch)
        await asyncio.to_thread(
            _write_repository_branches,
            GITHUB_WEBHOOK_BRANCHES_PATH,
            repository_branches,
        )
        _replace_active_repository_branches(repository_branches)
        return True, len(branches)


async def allow_all_repository_branches(
    repository_name: str,
) -> bool:
    normalized_repository = normalize_repository_name(repository_name)

    async with _repository_branches_lock:
        repository_branches = await asyncio.to_thread(
            load_repository_branches,
            GITHUB_WEBHOOK_BRANCHES_PATH,
        )
        removed = repository_branches.pop(normalized_repository, None)
        changed = removed is not None
        if changed:
            await asyncio.to_thread(
                _write_repository_branches,
                GITHUB_WEBHOOK_BRANCHES_PATH,
                repository_branches,
            )
        _replace_active_repository_branches(repository_branches)

    return changed


def get_repository_branches(
    repository_name: str,
) -> set[str] | None:
    normalized_repository = normalize_repository_name(repository_name)
    branches = _repository_branches.get(normalized_repository)
    return None if branches is None else set(branches)


def should_notify_for_branch(
    payload: dict[str, Any],
    repository_branches: dict[str, set[str]],
) -> bool:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return True

    full_name = repository.get("full_name")
    if not isinstance(full_name, str) or not full_name.strip():
        return True

    branches = repository_branches.get(full_name.strip().casefold())
    if branches is None:
        return True

    ref = payload.get("ref")
    if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
        return False
    branch_name = ref.removeprefix("refs/heads/")
    return branch_name in branches


def resolve_repository_channel_id(
    payload: dict[str, Any],
    repository_channels: dict[str, int | None],
    default_channel_id: int | None,
) -> int | None:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return default_channel_id

    full_name = repository.get("full_name")
    if not isinstance(full_name, str) or not full_name.strip():
        return default_channel_id

    return repository_channels.get(
        full_name.strip().casefold(),
        default_channel_id,
    )


def _remember_delivery(app: web.Application, delivery_id: str) -> bool:
    seen_ids = app[DELIVERY_IDS_KEY]
    delivery_order = app[DELIVERY_ORDER_KEY]

    if delivery_id in seen_ids:
        return False

    if len(delivery_order) >= MAX_DELIVERY_IDS:
        oldest_id = delivery_order.popleft()
        seen_ids.discard(oldest_id)

    delivery_order.append(delivery_id)
    seen_ids.add(delivery_id)
    return True


def _text(value: Any, fallback: str = "알 수 없음") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _format_ref(ref: str) -> str:
    if ref.startswith("refs/heads/"):
        return ref.removeprefix("refs/heads/")
    if ref.startswith("refs/tags/"):
        return f"태그 {ref.removeprefix('refs/tags/')}"
    return ref


def _commit_line(commit: dict[str, Any]) -> str:
    commit_id = _text(commit.get("id"), "unknown")[:7]
    message = _text(commit.get("message"), "커밋 메시지 없음").splitlines()[0]
    message = discord.utils.escape_markdown(_truncate(message, 100))

    author_data = commit.get("author")
    author = (
        _text(author_data.get("name"))
        if isinstance(author_data, dict)
        else "알 수 없음"
    )
    author = discord.utils.escape_markdown(_truncate(author, 40))

    commit_url = commit.get("url")
    if isinstance(commit_url, str) and commit_url.startswith("https://"):
        commit_label = f"[`{commit_id}`]({commit_url})"
    else:
        commit_label = f"`{commit_id}`"

    return f"{commit_label} {message} - {author}"


def build_push_embed(
    payload: dict[str, Any],
    delivery_id: str,
) -> discord.Embed:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        repository = {}

    sender = payload.get("sender")
    if not isinstance(sender, dict):
        sender = {}

    repository_name = _text(
        repository.get("full_name"),
        _text(repository.get("name"), "GitHub 저장소"),
    )
    repository_name = discord.utils.escape_markdown(repository_name)
    repository_url = repository.get("html_url")
    compare_url = payload.get("compare")
    embed_url = (
        compare_url
        if isinstance(compare_url, str) and compare_url.startswith("https://")
        else repository_url
        if isinstance(repository_url, str)
        and repository_url.startswith("https://")
        else None
    )

    ref = _format_ref(_text(payload.get("ref"), "알 수 없는 브랜치"))
    ref = discord.utils.escape_markdown(ref)
    commits = payload.get("commits")
    if not isinstance(commits, list):
        commits = []

    created = payload.get("created") is True
    deleted = payload.get("deleted") is True
    forced = payload.get("forced") is True

    if deleted:
        title = f"{repository_name}: 브랜치 삭제"
        description = f"`{ref}` 브랜치가 삭제됐습니다."
        color = 0xCF222E
    elif created:
        title = f"{repository_name}: 브랜치 생성"
        description = f"`{ref}` 브랜치가 생성됐습니다."
        color = 0x1F883D
    else:
        title = f"{repository_name}: 새 푸시"
        description = (
            f"`{ref}` 브랜치에 **{len(commits)}개**의 커밋이 "
            "푸시됐습니다."
        )
        color = 0x238636

    embed = discord.Embed(
        title=_truncate(title, 256),
        url=embed_url,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )

    sender_name = discord.utils.escape_markdown(
        _text(sender.get("login"), "알 수 없음")
    )
    sender_url = sender.get("html_url")
    if isinstance(sender_url, str) and sender_url.startswith("https://"):
        sender_value = f"[{sender_name}]({sender_url})"
    else:
        sender_value = sender_name
    embed.add_field(name="보낸 사람", value=sender_value, inline=True)
    embed.add_field(name="브랜치", value=f"`{ref}`", inline=True)

    if forced:
        embed.add_field(name="강제 푸시", value="예", inline=True)

    commit_lines = [
        _commit_line(commit)
        for commit in commits[:MAX_COMMIT_LINES]
        if isinstance(commit, dict)
    ]
    if commit_lines:
        hidden_count = max(0, len(commits) - len(commit_lines))
        if hidden_count:
            commit_lines.append(f"외 {hidden_count}개 커밋")
        embed.add_field(
            name="커밋",
            value=_truncate("\n".join(commit_lines), 1024),
            inline=False,
        )

    avatar_url = sender.get("avatar_url")
    if isinstance(avatar_url, str) and avatar_url.startswith("https://"):
        embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text=f"GitHub delivery: {delivery_id}")
    return embed


async def _send_push_notification(
    client: discord.Client,
    payload: dict[str, Any],
    delivery_id: str,
    repository_channels: dict[str, int | None],
) -> None:
    channel_id = resolve_repository_channel_id(
        payload,
        repository_channels,
        GITHUB_WEBHOOK_CHANNEL_ID,
    )
    if channel_id is None:
        return

    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            channel = await client.fetch_channel(channel_id)

        send = getattr(channel, "send", None)
        if send is None:
            raise RuntimeError("설정된 채널에는 메시지를 보낼 수 없습니다.")

        await send(
            embed=build_push_embed(payload, delivery_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as error:
        print(f"GitHub 푸시 알림 전송 오류: {error}")


async def _handle_health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _handle_webhook(request: web.Request) -> web.Response:
    payload_body = await request.read()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(
        payload_body,
        signature,
        request.app[WEBHOOK_SECRET_KEY],
    ):
        return web.json_response(
            {"status": "invalid signature"},
            status=401,
        )

    delivery_id = request.headers.get("X-GitHub-Delivery", "").strip()
    event_name = request.headers.get("X-GitHub-Event", "").strip()
    if not delivery_id or not event_name:
        return web.json_response(
            {"status": "missing GitHub headers"},
            status=400,
        )

    try:
        payload = json.loads(payload_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return web.json_response({"status": "invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response(
            {"status": "invalid payload"},
            status=400,
        )

    if not _remember_delivery(request.app, delivery_id):
        return web.json_response({"status": "duplicate"})

    if event_name == "ping":
        return web.json_response({"status": "pong"})
    if event_name != "push":
        return web.json_response(
            {"status": "ignored", "event": event_name}
        )
    if not should_notify_for_branch(
        payload,
        request.app[REPOSITORY_BRANCHES_KEY],
    ):
        return web.json_response(
            {"status": "ignored", "reason": "branch filter"},
            status=202,
        )

    task = asyncio.create_task(
        _send_push_notification(
            request.app[DISCORD_CLIENT_KEY],
            payload,
            delivery_id,
            request.app[REPOSITORY_CHANNELS_KEY],
        ),
        name=f"github-push-{delivery_id}",
    )
    notification_tasks = request.app[NOTIFICATION_TASKS_KEY]
    notification_tasks.add(task)
    task.add_done_callback(notification_tasks.discard)
    return web.json_response({"status": "accepted"}, status=202)


def create_github_webhook_app(
    client: discord.Client,
    secret: str,
    repository_channels: dict[str, int | None] | None = None,
    repository_branches: dict[str, set[str]] | None = None,
) -> web.Application:
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app[DISCORD_CLIENT_KEY] = client
    app[WEBHOOK_SECRET_KEY] = secret
    app[DELIVERY_IDS_KEY] = set()
    app[DELIVERY_ORDER_KEY] = deque()
    app[NOTIFICATION_TASKS_KEY] = set()
    app[REPOSITORY_CHANNELS_KEY] = (
        repository_channels if repository_channels is not None else {}
    )
    app[REPOSITORY_BRANCHES_KEY] = (
        repository_branches if repository_branches is not None else {}
    )
    app.router.add_get(HEALTH_PATH, _handle_health)
    app.router.add_post(WEBHOOK_PATH, _handle_webhook)
    return app


async def start_github_webhook_server(
    client: discord.Client,
) -> web.AppRunner | None:
    global _runner

    if not GITHUB_WEBHOOK_ENABLED:
        return None
    if _runner is not None:
        return _runner

    repository_channels = load_repository_channels(
        GITHUB_WEBHOOK_CHANNELS_PATH
    )
    repository_branches = load_repository_branches(
        GITHUB_WEBHOOK_BRANCHES_PATH
    )
    _replace_active_repository_channels(repository_channels)
    _replace_active_repository_branches(repository_branches)
    app = create_github_webhook_app(
        client,
        GITHUB_WEBHOOK_SECRET,
        _repository_channels,
        _repository_branches,
    )
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    try:
        site = web.TCPSite(
            runner,
            host=GITHUB_WEBHOOK_HOST,
            port=GITHUB_WEBHOOK_PORT,
        )
        await site.start()
    except Exception:
        await runner.cleanup()
        raise

    _runner = runner
    print(
        "GitHub Webhook 서버를 시작했습니다: "
        f"http://{GITHUB_WEBHOOK_HOST}:{GITHUB_WEBHOOK_PORT}{WEBHOOK_PATH}"
    )
    print(
        "GitHub 저장소별 Discord 채널 설정을 불러왔습니다: "
        f"{len(repository_channels)}개"
    )
    print(
        "GitHub 저장소별 브랜치 필터를 불러왔습니다: "
        f"{len(repository_branches)}개"
    )
    return runner
