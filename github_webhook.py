import asyncio
from collections import deque
import hashlib
import hmac
import json
from typing import Any

from aiohttp import web
import discord

from config import (
    GITHUB_WEBHOOK_CHANNEL_ID,
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

_runner: web.AppRunner | None = None


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
) -> None:
    if GITHUB_WEBHOOK_CHANNEL_ID is None:
        return

    try:
        channel = client.get_channel(GITHUB_WEBHOOK_CHANNEL_ID)
        if channel is None:
            channel = await client.fetch_channel(GITHUB_WEBHOOK_CHANNEL_ID)

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

    task = asyncio.create_task(
        _send_push_notification(
            request.app[DISCORD_CLIENT_KEY],
            payload,
            delivery_id,
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
) -> web.Application:
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app[DISCORD_CLIENT_KEY] = client
    app[WEBHOOK_SECRET_KEY] = secret
    app[DELIVERY_IDS_KEY] = set()
    app[DELIVERY_ORDER_KEY] = deque()
    app[NOTIFICATION_TASKS_KEY] = set()
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

    app = create_github_webhook_app(client, GITHUB_WEBHOOK_SECRET)
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
    return runner
