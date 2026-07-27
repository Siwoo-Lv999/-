import asyncio
import time

import discord
from discord import app_commands

from auto_roles import (
    get_auto_role_id,
    initialize_auto_roles,
    remove_auto_role,
    set_auto_role,
)
from config import (
    CONVERSATION_RETENTION_DAYS,
    DISCORD_TOKEN,
    GITHUB_WEBHOOK_CHANNEL_ID,
    IGNORE_BOT_MESSAGES,
    OLLAMA_WARMUP_ON_START,
    USER_COOLDOWN_SECONDS,
)
from github_webhook import (
    add_repository_branch,
    allow_all_repository_branches,
    delete_repository_branch,
    delete_repository_channel,
    get_repository_branches,
    get_repository_channels,
    set_repository_channel,
    start_github_webhook_server,
)
from llm import (
    LlmConnectionError,
    LlmResponseError,
    LlmTimeoutError,
    generate_reply,
    warm_up_model,
)
from moderation import check_message
from storage import (
    ConversationStorageError,
    build_session_key,
    delete_user_conversations,
    get_session_record,
    initialize_database,
    purge_expired_records,
    save_exchange,
)


EMPTY_MESSAGE_REPLY = "무엇을 도와드릴까요, 선생님?"
CONNECTION_ERROR_REPLY = (
    "지금은 답변을 제대로 준비할 수 없네요, 선생님. "
    "잠시 뒤에 다시 말씀해 주세요."
)
TIMEOUT_ERROR_REPLY = (
    "생각을 정리하는 데 너무 오래 걸렸네요, 선생님. "
    "잠시 뒤에 다시 시도해 주세요."
)
RESPONSE_ERROR_REPLY = (
    "답변을 만들다가 문제가 생겼습니다, 선생님. "
    "잠시 뒤에 다시 말씀해 주세요."
)
DISCORD_MESSAGE_LIMIT = 2000
COOLDOWN_REPLY = "조금만 기다렸다가 다시 말씀해 주세요, 선생님."

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)
command_tree = app_commands.CommandTree(client)
conversation_group = app_commands.Group(
    name="대화", description="저장된 대화 기록을 관리합니다."
)
role_group = app_commands.Group(
    name="역할",
    description="서버 역할 설정을 관리합니다.",
    guild_only=True,
)
auto_role_group = app_commands.Group(
    name="자동지급",
    description="새 멤버에게 지급할 역할을 관리합니다.",
    parent=role_group,
)
github_group = app_commands.Group(
    name="github",
    description="GitHub 알림 설정을 관리합니다.",
    guild_only=True,
)
github_channel_group = app_commands.Group(
    name="채널",
    description="저장소별 알림 채널을 관리합니다.",
    parent=github_group,
)
github_branch_group = app_commands.Group(
    name="브랜치",
    description="저장소별 Push 알림 브랜치를 관리합니다.",
    parent=github_group,
)
commands_synced = False
last_message_times: dict[int, float] = {}
user_operation_locks: dict[int, asyncio.Lock] = {}
retention_cleanup_task: asyncio.Task[None] | None = None
ollama_warmup_task: asyncio.Task[None] | None = None


def get_user_operation_lock(user_id: int) -> asyncio.Lock:
    return user_operation_locks.setdefault(user_id, asyncio.Lock())


def contains_bot_mention(content: str, bot_user: discord.ClientUser) -> bool:
    mention_formats = (f"<@{bot_user.id}>", f"<@!{bot_user.id}>")
    return any(mention in content for mention in mention_formats)


def remove_bot_mention(content: str, bot_user: discord.ClientUser) -> str:
    cleaned_content = content
    for mention in (f"<@{bot_user.id}>", f"<@!{bot_user.id}>"):
        cleaned_content = cleaned_content.replace(mention, "")
    return cleaned_content.strip()


def split_discord_message(content: str) -> list[str]:
    chunks: list[str] = []
    remaining = content.strip()

    while len(remaining) > DISCORD_MESSAGE_LIMIT:
        split_at = remaining.rfind("\n", 0, DISCORD_MESSAGE_LIMIT + 1)
        if split_at < DISCORD_MESSAGE_LIMIT // 2:
            split_at = DISCORD_MESSAGE_LIMIT

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks


async def send_reply(message: discord.Message, content: str) -> None:
    chunks = split_discord_message(content)
    if not chunks:
        return

    await message.reply(
        chunks[0],
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    for chunk in chunks[1:]:
        await message.channel.send(
            chunk,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def redact_discord_token(content: str) -> str:
    if DISCORD_TOKEN and DISCORD_TOKEN in content:
        return content.replace(DISCORD_TOKEN, "[Discord 봇 토큰 제거]")
    return content


async def send_ephemeral_chunks(
    interaction: discord.Interaction, content: str
) -> None:
    chunks = split_discord_message(content)
    for chunk in chunks:
        await interaction.followup.send(
            chunk,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def require_github_admin_permission(
    interaction: discord.Interaction,
) -> bool:
    member = interaction.user
    if (
        interaction.guild is not None
        and isinstance(member, discord.Member)
        and member.guild_permissions.manage_guild
    ):
        return True

    await interaction.response.send_message(
        "이 설정은 서버 관리 권한이 있는 사용자만 변경할 수 있습니다.",
        ephemeral=True,
    )
    return False


async def require_role_manager_permission(
    interaction: discord.Interaction,
) -> bool:
    member = interaction.user
    if (
        interaction.guild is not None
        and isinstance(member, discord.Member)
        and member.guild_permissions.manage_roles
    ):
        return True

    await interaction.response.send_message(
        "이 설정은 역할 관리 권한이 있는 사용자만 변경할 수 있습니다.",
        ephemeral=True,
    )
    return False


def validate_auto_role(
    guild: discord.Guild,
    actor: discord.Member,
    role: discord.Role,
) -> str | None:
    bot_member = guild.me
    if bot_member is None:
        return "현재 서버의 봇 역할을 확인할 수 없습니다."
    if role.is_default():
        return "@everyone 역할은 자동 지급 역할로 설정할 수 없습니다."
    if role.managed:
        return "연동 서비스나 봇이 관리하는 역할은 자동 지급할 수 없습니다."
    if (
        role.permissions.administrator
        or role.permissions.manage_guild
        or role.permissions.manage_roles
    ):
        return "관리 권한이 포함된 역할은 자동 지급할 수 없습니다."
    if not bot_member.guild_permissions.manage_roles:
        return "봇에게 `역할 관리` 권한이 필요합니다."
    if bot_member.top_role <= role:
        return "지급할 역할을 봇의 가장 높은 역할보다 아래로 옮겨 주세요."
    if actor.id != guild.owner_id and actor.top_role <= role:
        return "본인의 가장 높은 역할보다 낮은 역할만 설정할 수 있습니다."
    return None


@auto_role_group.command(
    name="설정",
    description="새 멤버에게 자동으로 지급할 역할을 설정합니다.",
)
@app_commands.guild_only()
@app_commands.describe(role="새 멤버에게 자동 지급할 역할")
@app_commands.rename(role="역할")
async def configure_auto_role(
    interaction: discord.Interaction,
    role: discord.Role,
) -> None:
    if not await require_role_manager_permission(interaction):
        return

    guild = interaction.guild
    actor = interaction.user
    if guild is None or not isinstance(actor, discord.Member):
        await interaction.response.send_message(
            "서버에서만 설정할 수 있습니다.",
            ephemeral=True,
        )
        return

    validation_error = validate_auto_role(guild, actor, role)
    if validation_error is not None:
        await interaction.response.send_message(
            validation_error,
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await set_auto_role(guild.id, role.id)
    except RuntimeError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    await interaction.followup.send(
        f"새로 들어오는 사람에게 {role.mention} 역할을 지급하겠습니다.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@auto_role_group.command(
    name="확인",
    description="현재 자동 지급 역할을 확인합니다.",
)
@app_commands.guild_only()
async def show_auto_role(interaction: discord.Interaction) -> None:
    if not await require_role_manager_permission(interaction):
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "서버에서만 확인할 수 있습니다.",
            ephemeral=True,
        )
        return

    role_id = get_auto_role_id(guild.id)
    if role_id is None:
        result = "현재 자동 지급 역할이 설정되어 있지 않습니다."
    else:
        role = guild.get_role(role_id)
        result = (
            f"현재 자동 지급 역할은 {role.mention}입니다."
            if role is not None
            else (
                f"설정된 역할(`{role_id}`)이 서버에서 삭제됐거나 "
                "확인되지 않습니다."
            )
        )

    await interaction.response.send_message(
        result,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@auto_role_group.command(
    name="해제",
    description="새 멤버 역할 자동 지급을 끕니다.",
)
@app_commands.guild_only()
async def disable_auto_role(interaction: discord.Interaction) -> None:
    if not await require_role_manager_permission(interaction):
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "서버에서만 설정할 수 있습니다.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        changed = await remove_auto_role(guild.id)
    except RuntimeError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    result = (
        "역할 자동 지급을 해제했습니다."
        if changed
        else "현재 설정된 자동 지급 역할이 없습니다."
    )
    await interaction.followup.send(result, ephemeral=True)


@github_channel_group.command(
    name="설정",
    description="GitHub 저장소의 Push 알림 채널을 지정합니다.",
)
@app_commands.guild_only()
@app_commands.describe(
    repository_name="GitHub 저장소 전체 이름(owner/repository)",
    channel="Push 알림을 받을 Discord 채널",
)
@app_commands.rename(repository_name="저장소", channel="채널")
async def configure_github_channel(
    interaction: discord.Interaction,
    repository_name: str,
    channel: discord.TextChannel,
) -> None:
    if not await require_github_admin_permission(interaction):
        return

    guild = interaction.guild
    if guild is None or guild.me is None:
        await interaction.response.send_message(
            "현재 서버의 봇 권한을 확인할 수 없습니다.",
            ephemeral=True,
        )
        return

    permissions = channel.permissions_for(guild.me)
    missing_permissions: list[str] = []
    if not permissions.view_channel:
        missing_permissions.append("채널 보기")
    if not permissions.send_messages:
        missing_permissions.append("메시지 보내기")
    if not permissions.embed_links:
        missing_permissions.append("링크 첨부")

    if missing_permissions:
        await interaction.response.send_message(
            (
                f"{channel.mention} 채널에서 봇에게 다음 권한이 필요합니다: "
                f"{', '.join(missing_permissions)}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        normalized_name = await set_repository_channel(
            repository_name,
            channel.id,
        )
    except RuntimeError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    await interaction.followup.send(
        (
            f"`{normalized_name}` 저장소의 Push 알림 채널을 "
            f"{channel.mention}로 설정했습니다."
        ),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@github_channel_group.command(
    name="목록",
    description="저장소별 Push 알림 채널 설정을 확인합니다.",
)
@app_commands.guild_only()
async def show_github_channels(
    interaction: discord.Interaction,
) -> None:
    if not await require_github_admin_permission(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    repository_channels = get_repository_channels()
    sections = ["**GitHub 저장소별 알림 채널**"]

    if GITHUB_WEBHOOK_CHANNEL_ID is not None:
        sections.append(
            f"기본 채널: <#{GITHUB_WEBHOOK_CHANNEL_ID}>"
        )

    if repository_channels:
        sections.append("")
        sections.extend(
            (
                f"`{repository_name}` → <#{channel_id}>"
                if channel_id is not None
                else f"`{repository_name}` → **알림 꺼짐**"
            )
            for repository_name, channel_id in repository_channels.items()
        )
    else:
        sections.extend(("", "저장소별로 지정된 채널이 없습니다."))

    await send_ephemeral_chunks(interaction, "\n".join(sections))


@github_channel_group.command(
    name="삭제",
    description="해당 GitHub 저장소의 Push 알림을 끕니다.",
)
@app_commands.guild_only()
@app_commands.describe(
    repository_name="삭제할 GitHub 저장소 전체 이름(owner/repository)"
)
@app_commands.rename(repository_name="저장소")
async def remove_github_channel(
    interaction: discord.Interaction,
    repository_name: str,
) -> None:
    if not await require_github_admin_permission(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        changed = await delete_repository_channel(repository_name)
    except RuntimeError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    if changed:
        result = (
            f"`{repository_name.strip().casefold()}` 저장소의 "
            "Push 알림을 껐습니다."
        )
    else:
        result = "해당 저장소의 Push 알림은 이미 꺼져 있습니다."
    await interaction.followup.send(result, ephemeral=True)


@github_branch_group.command(
    name="추가",
    description="저장소의 Push 알림 허용 브랜치를 추가합니다.",
)
@app_commands.guild_only()
@app_commands.describe(
    repository_name="GitHub 저장소 전체 이름(owner/repository)",
    branch_name="알림을 허용할 브랜치 이름",
)
@app_commands.rename(repository_name="저장소", branch_name="브랜치")
async def add_github_branch(
    interaction: discord.Interaction,
    repository_name: str,
    branch_name: str,
) -> None:
    if not await require_github_admin_permission(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        repository, branch, changed = await add_repository_branch(
            repository_name,
            branch_name,
        )
    except RuntimeError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    if changed:
        result = (
            f"`{repository}` 저장소의 허용 목록에 `{branch}` 브랜치를 "
            "추가했습니다. 이제 등록된 브랜치만 알림을 보냅니다."
        )
    else:
        result = "해당 브랜치는 이미 알림 허용 목록에 있습니다."
    await interaction.followup.send(result, ephemeral=True)


@github_branch_group.command(
    name="삭제",
    description="저장소의 Push 알림 허용 브랜치를 삭제합니다.",
)
@app_commands.guild_only()
@app_commands.describe(
    repository_name="GitHub 저장소 전체 이름(owner/repository)",
    branch_name="허용 목록에서 삭제할 브랜치 이름",
)
@app_commands.rename(repository_name="저장소", branch_name="브랜치")
async def remove_github_branch(
    interaction: discord.Interaction,
    repository_name: str,
    branch_name: str,
) -> None:
    if not await require_github_admin_permission(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        changed, remaining_count = await delete_repository_branch(
            repository_name,
            branch_name,
        )
    except RuntimeError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    if changed and remaining_count == 0:
        result = (
            "마지막 허용 브랜치를 삭제했습니다. 이 저장소는 이제 어떤 "
            "브랜치의 Push 알림도 보내지 않습니다."
        )
    elif changed:
        result = (
            f"허용 목록에서 `{branch_name.strip()}` 브랜치를 삭제했습니다. "
            f"남은 브랜치는 {remaining_count}개입니다."
        )
    elif remaining_count is None:
        result = (
            "이 저장소는 브랜치 필터가 없어 모든 브랜치 알림을 "
            "보내고 있습니다."
        )
    else:
        result = "해당 브랜치를 허용 목록에서 찾지 못했습니다."
    await interaction.followup.send(result, ephemeral=True)


@github_branch_group.command(
    name="목록",
    description="저장소의 Push 알림 허용 브랜치를 확인합니다.",
)
@app_commands.guild_only()
@app_commands.describe(
    repository_name="확인할 GitHub 저장소 전체 이름(owner/repository)"
)
@app_commands.rename(repository_name="저장소")
async def show_github_branches(
    interaction: discord.Interaction,
    repository_name: str,
) -> None:
    if not await require_github_admin_permission(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        repository = repository_name.strip().casefold()
        branches = get_repository_branches(repository_name)
    except RuntimeError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    if branches is None:
        result = (
            f"`{repository}` 저장소는 모든 브랜치의 Push 알림을 보냅니다."
        )
    elif not branches:
        result = (
            f"`{repository}` 저장소는 허용 브랜치가 없어 Push 알림을 "
            "보내지 않습니다."
        )
    else:
        branch_lines = "\n".join(
            f"- `{branch}`" for branch in sorted(branches)
        )
        result = (
            f"**{repository} 허용 브랜치**\n{branch_lines}"
        )
    await interaction.followup.send(result, ephemeral=True)


@github_branch_group.command(
    name="전체",
    description="브랜치 필터를 제거하고 모든 브랜치 알림을 허용합니다.",
)
@app_commands.guild_only()
@app_commands.describe(
    repository_name="전체 브랜치를 허용할 저장소(owner/repository)"
)
@app_commands.rename(repository_name="저장소")
async def allow_all_github_branches(
    interaction: discord.Interaction,
    repository_name: str,
) -> None:
    if not await require_github_admin_permission(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        changed = await allow_all_repository_branches(repository_name)
    except RuntimeError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    result = (
        "브랜치 필터를 제거했습니다. 이제 모든 브랜치의 Push 알림을 "
        "보냅니다."
        if changed
        else "이 저장소는 이미 모든 브랜치 알림을 보내고 있습니다."
    )
    await interaction.followup.send(result, ephemeral=True)


@conversation_group.command(
    name="초기화", description="내 모든 대화 기록을 삭제합니다."
)
async def reset_conversation(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        async with get_user_operation_lock(interaction.user.id):
            deleted_count = await delete_user_conversations(interaction.user.id)
    except ConversationStorageError as error:
        print(f"대화 초기화 오류: {error}")
        await interaction.followup.send(
            "대화 기록을 삭제하는 중 문제가 발생했습니다.", ephemeral=True
        )
        return

    if deleted_count == 0:
        result_message = "삭제할 대화 기록이 없습니다, 선생님."
    else:
        result_message = (
            f"선생님의 저장된 대화 기록 {deleted_count}개를 모두 삭제했습니다."
        )

    await interaction.followup.send(result_message, ephemeral=True)


command_tree.add_command(conversation_group)
command_tree.add_command(role_group)
command_tree.add_command(github_group)


@client.event
async def on_ready() -> None:
    global commands_synced
    global ollama_warmup_task
    global retention_cleanup_task

    if not commands_synced:
        try:
            synced_commands = await command_tree.sync()
        except discord.HTTPException as error:
            print(f"슬래시 명령어 동기화 오류: {error}")
        else:
            commands_synced = True
            print(f"슬래시 명령어를 동기화했습니다: {len(synced_commands)}개")

    print(f"Discord에 로그인했습니다: {client.user}")

    try:
        await start_github_webhook_server(client)
    except Exception as error:
        print(f"GitHub Webhook 서버 시작 오류: {error}")

    if (
        OLLAMA_WARMUP_ON_START
        and (ollama_warmup_task is None or ollama_warmup_task.done())
    ):
        ollama_warmup_task = asyncio.create_task(
            run_ollama_warmup(),
            name="ollama-model-warmup",
        )

    if (
        CONVERSATION_RETENTION_DAYS > 0
        and (
            retention_cleanup_task is None
            or retention_cleanup_task.done()
        )
    ):
        retention_cleanup_task = asyncio.create_task(
            run_retention_cleanup(),
            name="conversation-retention-cleanup",
        )


async def run_retention_cleanup() -> None:
    while not client.is_closed():
        await asyncio.sleep(3600)
        try:
            await purge_expired_records()
        except ConversationStorageError as error:
            print(f"대화 보존 기간 정리 오류: {error}")


async def run_ollama_warmup() -> None:
    started_at = time.perf_counter()
    try:
        await warm_up_model()
    except (LlmConnectionError, LlmTimeoutError, LlmResponseError) as error:
        print(f"Ollama 모델 예열 오류: {error}")
    else:
        elapsed = time.perf_counter() - started_at
        print(f"Ollama 모델 예열 완료: {elapsed:.2f}초")


@client.event
async def on_member_join(member: discord.Member) -> None:
    if member.bot:
        return

    role_id = get_auto_role_id(member.guild.id)
    if role_id is None:
        return

    role = member.guild.get_role(role_id)
    bot_member = member.guild.me
    if role is None:
        print(
            f"자동 역할 지급 실패: 서버 {member.guild.id}에서 "
            f"역할 {role_id}을 찾지 못했습니다."
        )
        return
    if (
        bot_member is None
        or not bot_member.guild_permissions.manage_roles
        or bot_member.top_role <= role
    ):
        print(
            f"자동 역할 지급 실패: 서버 {member.guild.id}의 "
            "봇 권한 또는 역할 순서를 확인해 주세요."
        )
        return

    try:
        await member.add_roles(
            role,
            reason="새 멤버 자동 역할 지급",
        )
    except discord.Forbidden:
        print(
            f"자동 역할 지급 거부: 서버 {member.guild.id}에서 "
            f"역할 {role.id}을 지급할 권한이 없습니다."
        )
    except discord.HTTPException as error:
        print(
            f"자동 역할 지급 오류: 서버 {member.guild.id}, "
            f"역할 {role.id}, {error}"
        )


async def process_conversation_message(
    message: discord.Message,
    user_message: str,
) -> None:
    guild_id = message.guild.id if message.guild is not None else None
    session_key = build_session_key(
        guild_id, message.channel.id, message.author.id
    )

    try:
        session_record = await get_session_record(session_key)
    except ConversationStorageError as error:
        print(f"대화 조회 오류: {error}")
        conversation_history = []
    else:
        raw_messages = session_record["messages"]
        conversation_history = [
            {
                "role": str(item.get("role", "")),
                "content": str(item.get("content", "")),
            }
            for item in raw_messages
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
        ]

    generated_reply: str | None = None
    llm_started_at = time.perf_counter()
    async with message.channel.typing():
        try:
            generated_reply = await generate_reply(
                user_message,
                conversation_history,
            )
            reply = generated_reply
        except LlmConnectionError as error:
            print(f"Ollama 연결 오류: {error}")
            reply = CONNECTION_ERROR_REPLY
        except LlmTimeoutError as error:
            print(f"Ollama 시간 초과: {error}")
            reply = TIMEOUT_ERROR_REPLY
        except LlmResponseError as error:
            print(f"Ollama 응답 오류: {error}")
            reply = RESPONSE_ERROR_REPLY
        finally:
            elapsed = time.perf_counter() - llm_started_at
            print(f"LLM 응답 처리 시간: {elapsed:.2f}초")

        await send_reply(message, reply)

    if generated_reply is None:
        return

    try:
        await save_exchange(
            session_key,
            guild_id,
            message.channel.id,
            message.author.id,
            redact_discord_token(user_message),
            redact_discord_token(generated_reply),
        )
    except ConversationStorageError as error:
        print(f"대화 저장 오류: {error}")


@client.event
async def on_message(message: discord.Message) -> None:
    if client.user is not None and message.author.id == client.user.id:
        return
    if message.author.bot and IGNORE_BOT_MESSAGES:
        return

    try:
        is_direct_message = message.guild is None

        if is_direct_message:
            user_message = message.content.strip()
        else:
            if client.user is None or not contains_bot_mention(
                message.content, client.user
            ):
                return
            user_message = remove_bot_mention(message.content, client.user)

        if not user_message:
            async with message.channel.typing():
                await message.reply(EMPTY_MESSAGE_REPLY, mention_author=False)
            return

        current_time = time.monotonic()
        previous_time = last_message_times.get(message.author.id)
        if (
            previous_time is not None
            and current_time - previous_time < USER_COOLDOWN_SECONDS
        ):
            await message.reply(COOLDOWN_REPLY, mention_author=False)
            return
        last_message_times[message.author.id] = current_time

        moderation_result = check_message(user_message)
        if moderation_result is not None:
            _, moderation_reply = moderation_result
            async with message.channel.typing():
                await send_reply(message, moderation_reply)
            return

        async with get_user_operation_lock(message.author.id):
            await process_conversation_message(message, user_message)
    except Exception as error:
        print(f"메시지 처리 중 오류가 발생했습니다: {error}")


if __name__ == "__main__":
    initialize_database()
    initialize_auto_roles()
    client.run(DISCORD_TOKEN)
