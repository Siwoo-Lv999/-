import asyncio
import time

import discord
from discord import app_commands

from config import (
    CONVERSATION_MAINTENANCE_DELAY_SECONDS,
    CONVERSATION_RETENTION_DAYS,
    DISCORD_TOKEN,
    IGNORE_BOT_MESSAGES,
    OLLAMA_WARMUP_ON_START,
    USER_COOLDOWN_SECONDS,
)
from llm import (
    LlmConnectionError,
    LlmResponseError,
    LlmTimeoutError,
    extract_memory_candidates,
    generate_reply,
    summarize_conversation,
    warm_up_model,
)
from moderation import check_message
from storage import (
    ConversationStorageError,
    approve_user_memory,
    build_session_key,
    delete_user_conversations,
    delete_user_memory,
    get_messages_to_summarize,
    get_session_record,
    get_user_memories,
    initialize_database,
    is_storage_enabled,
    purge_expired_records,
    save_exchange,
    save_memory_candidates,
    set_storage_enabled,
    store_summary_and_delete_messages,
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

client = discord.Client(intents=intents)
command_tree = app_commands.CommandTree(client)
conversation_group = app_commands.Group(
    name="대화", description="저장된 대화 기록을 관리합니다."
)
conversation_storage_group = app_commands.Group(
    name="저장",
    description="대화 기록 저장 여부를 설정합니다.",
    parent=conversation_group,
)
conversation_memory_group = app_commands.Group(
    name="기억",
    description="장기 기억 후보와 승인된 기억을 관리합니다.",
    parent=conversation_group,
)
commands_synced = False
last_message_times: dict[int, float] = {}
user_operation_locks: dict[int, asyncio.Lock] = {}
user_data_versions: dict[int, int] = {}
retention_cleanup_task: asyncio.Task[None] | None = None
ollama_warmup_task: asyncio.Task[None] | None = None
conversation_maintenance_task: asyncio.Task[None] | None = None
conversation_maintenance_queue: asyncio.Queue[tuple[str, int, int]] = (
    asyncio.Queue()
)
queued_maintenance_sessions: set[str] = set()
maintenance_not_before: dict[str, float] = {}


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


def build_interaction_session_key(interaction: discord.Interaction) -> str:
    if interaction.channel_id is None:
        raise ConversationStorageError("현재 대화 채널을 확인할 수 없습니다.")

    guild_id = interaction.guild_id
    return build_session_key(guild_id, interaction.channel_id, interaction.user.id)


def escape_record_text(content: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(content))


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


@conversation_group.command(
    name="초기화", description="내 모든 대화 기록을 삭제합니다."
)
async def reset_conversation(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        async with get_user_operation_lock(interaction.user.id):
            user_data_versions[interaction.user.id] = (
                user_data_versions.get(interaction.user.id, 0) + 1
            )
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


@conversation_group.command(
    name="기록", description="현재 대화의 최근 기록과 요약을 확인합니다."
)
async def show_conversation_record(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        async with get_user_operation_lock(interaction.user.id):
            session_key = build_interaction_session_key(interaction)
            record = await get_session_record(session_key)
    except ConversationStorageError as error:
        print(f"대화 기록 조회 오류: {error}")
        await interaction.followup.send(
            "대화 기록을 불러오는 중 문제가 발생했습니다.", ephemeral=True
        )
        return

    summary = record["summary"]
    messages = record["messages"]
    if not summary and not messages:
        await interaction.followup.send(
            "현재 대화에 저장된 기록이 없습니다, 선생님.", ephemeral=True
        )
        return

    sections = ["**현재 대화 기록**"]
    if isinstance(summary, str) and summary:
        sections.extend(
            ("", "**이전 대화 요약**", escape_record_text(summary))
        )

    if isinstance(messages, list) and messages:
        sections.extend(("", "**최근 메시지**"))
        for message_record in messages:
            if not isinstance(message_record, dict):
                continue
            speaker = (
                "선생님"
                if message_record.get("role") == "user"
                else "케이"
            )
            content = str(message_record.get("content", ""))
            sections.append(f"**{speaker}:** {escape_record_text(content)}")

    await send_ephemeral_chunks(interaction, "\n".join(sections))


@conversation_storage_group.command(
    name="켜기", description="앞으로의 대화 기록 저장을 켭니다."
)
async def enable_conversation_storage(
    interaction: discord.Interaction,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        async with get_user_operation_lock(interaction.user.id):
            await set_storage_enabled(interaction.user.id, True)
    except ConversationStorageError as error:
        print(f"대화 저장 설정 오류: {error}")
        await interaction.followup.send(
            "대화 저장 설정을 변경하는 중 문제가 발생했습니다.", ephemeral=True
        )
        return

    await interaction.followup.send(
        "이제부터 대화 기록을 저장하겠습니다, 선생님.", ephemeral=True
    )


@conversation_storage_group.command(
    name="끄기", description="앞으로의 대화 기록 저장을 끕니다."
)
async def disable_conversation_storage(
    interaction: discord.Interaction,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        async with get_user_operation_lock(interaction.user.id):
            await set_storage_enabled(interaction.user.id, False)
    except ConversationStorageError as error:
        print(f"대화 저장 설정 오류: {error}")
        await interaction.followup.send(
            "대화 저장 설정을 변경하는 중 문제가 발생했습니다.", ephemeral=True
        )
        return

    await interaction.followup.send(
        (
            "이제부터 새 대화 기록을 저장하지 않겠습니다, 선생님.\n"
            "기존 기록은 `/대화 초기화`로 따로 삭제할 수 있습니다."
        ),
        ephemeral=True,
    )


@conversation_memory_group.command(
    name="보기", description="내 장기 기억 후보와 승인된 기억을 확인합니다."
)
async def show_user_memories(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        async with get_user_operation_lock(interaction.user.id):
            memories = await get_user_memories(interaction.user.id)
    except ConversationStorageError as error:
        print(f"장기 기억 조회 오류: {error}")
        await interaction.followup.send(
            "장기 기억을 불러오는 중 문제가 발생했습니다.", ephemeral=True
        )
        return

    if not memories:
        await interaction.followup.send(
            "저장된 장기 기억이나 후보가 없습니다, 선생님.", ephemeral=True
        )
        return

    sections = ["**장기 기억**"]
    for memory in memories:
        status = "승인됨" if memory["status"] == "approved" else "승인 대기"
        category = escape_record_text(str(memory["category"]))
        content = escape_record_text(str(memory["content"]))
        sections.append(
            f"`#{memory['id']}` **{status} · {category}**\n{content}"
        )

    sections.extend(
        (
            "",
            "승인 대기 항목은 `/대화 기억 승인`으로 허용해야 답변에 사용됩니다.",
        )
    )
    await send_ephemeral_chunks(interaction, "\n\n".join(sections))


@conversation_memory_group.command(
    name="승인", description="장기 기억 후보 하나를 승인합니다."
)
@app_commands.describe(memory_id="승인할 기억 번호")
@app_commands.rename(memory_id="번호")
async def approve_memory(
    interaction: discord.Interaction,
    memory_id: app_commands.Range[int, 1],
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        async with get_user_operation_lock(interaction.user.id):
            approved = await approve_user_memory(
                interaction.user.id, memory_id
            )
    except ConversationStorageError as error:
        print(f"장기 기억 승인 오류: {error}")
        await interaction.followup.send(
            "장기 기억을 승인하는 중 문제가 발생했습니다.", ephemeral=True
        )
        return

    result = (
        "선택한 기억을 승인했습니다, 선생님."
        if approved
        else "승인 대기 중인 해당 기억을 찾지 못했습니다, 선생님."
    )
    await interaction.followup.send(result, ephemeral=True)


@conversation_memory_group.command(
    name="삭제", description="장기 기억 또는 후보 하나를 삭제합니다."
)
@app_commands.describe(memory_id="삭제할 기억 번호")
@app_commands.rename(memory_id="번호")
async def delete_memory(
    interaction: discord.Interaction,
    memory_id: app_commands.Range[int, 1],
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        async with get_user_operation_lock(interaction.user.id):
            deleted = await delete_user_memory(interaction.user.id, memory_id)
    except ConversationStorageError as error:
        print(f"장기 기억 삭제 오류: {error}")
        await interaction.followup.send(
            "장기 기억을 삭제하는 중 문제가 발생했습니다.", ephemeral=True
        )
        return

    result = (
        "선택한 기억을 삭제했습니다, 선생님."
        if deleted
        else "해당 기억을 찾지 못했습니다, 선생님."
    )
    await interaction.followup.send(result, ephemeral=True)


command_tree.add_command(conversation_group)


@client.event
async def on_ready() -> None:
    global commands_synced
    global conversation_maintenance_task
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

    if (
        OLLAMA_WARMUP_ON_START
        and (ollama_warmup_task is None or ollama_warmup_task.done())
    ):
        ollama_warmup_task = asyncio.create_task(
            run_ollama_warmup(),
            name="ollama-model-warmup",
        )

    if (
        conversation_maintenance_task is None
        or conversation_maintenance_task.done()
    ):
        conversation_maintenance_task = asyncio.create_task(
            run_conversation_maintenance_worker(),
            name="conversation-maintenance",
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


def schedule_conversation_maintenance(
    session_key: str,
    user_id: int,
) -> None:
    maintenance_not_before[session_key] = (
        time.monotonic() + CONVERSATION_MAINTENANCE_DELAY_SECONDS
    )
    if session_key in queued_maintenance_sessions:
        return

    queued_maintenance_sessions.add(session_key)
    conversation_maintenance_queue.put_nowait(
        (session_key, user_id, user_data_versions.get(user_id, 0))
    )


async def wait_for_conversation_idle(session_key: str) -> None:
    while True:
        delay = maintenance_not_before.get(session_key, 0) - time.monotonic()
        if delay <= 0:
            return
        await asyncio.sleep(delay)


async def maintain_conversation(
    session_key: str,
    user_id: int,
    data_version: int,
) -> None:
    if data_version != user_data_versions.get(user_id, 0):
        return

    try:
        session_record = await get_session_record(session_key)
    except ConversationStorageError as error:
        print(f"백그라운드 대화 조회 오류: {error}")
        return

    raw_summary = session_record.get("summary", "")
    current_summary = raw_summary if isinstance(raw_summary, str) else ""

    while data_version == user_data_versions.get(user_id, 0):
        try:
            messages_to_summarize = await get_messages_to_summarize(
                session_key
            )
        except ConversationStorageError as error:
            print(f"백그라운드 요약 대화 조회 오류: {error}")
            return

        if not messages_to_summarize:
            return

        await wait_for_conversation_idle(session_key)
        if data_version != user_data_versions.get(user_id, 0):
            return

        try:
            next_summary = await summarize_conversation(
                current_summary,
                messages_to_summarize,
            )
        except (
            LlmConnectionError,
            LlmTimeoutError,
            LlmResponseError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            print(f"백그라운드 대화 요약 오류: {error}")
            return

        await wait_for_conversation_idle(session_key)
        if data_version != user_data_versions.get(user_id, 0):
            return

        try:
            memory_candidates = await extract_memory_candidates(
                messages_to_summarize
            )
        except (LlmConnectionError, LlmTimeoutError, LlmResponseError) as error:
            print(f"백그라운드 장기 기억 후보 추출 오류: {error}")
            memory_candidates = []

        message_ids = [int(item["id"]) for item in messages_to_summarize]
        try:
            async with get_user_operation_lock(user_id):
                if data_version != user_data_versions.get(user_id, 0):
                    return
                await store_summary_and_delete_messages(
                    session_key,
                    next_summary,
                    message_ids,
                )
                await save_memory_candidates(user_id, memory_candidates)
        except (
            ConversationStorageError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            print(f"백그라운드 대화 정리 저장 오류: {error}")
            return

        current_summary = next_summary


async def run_conversation_maintenance_worker() -> None:
    while not client.is_closed():
        session_key, user_id, data_version = (
            await conversation_maintenance_queue.get()
        )
        was_cancelled = False
        try:
            await maintain_conversation(session_key, user_id, data_version)
        except asyncio.CancelledError:
            was_cancelled = True
            raise
        except Exception as error:
            print(f"백그라운드 대화 정리 오류: {error}")
        finally:
            conversation_maintenance_queue.task_done()
            queued_maintenance_sessions.discard(session_key)

            if not was_cancelled and not client.is_closed():
                try:
                    pending_messages = await get_messages_to_summarize(
                        session_key
                    )
                except ConversationStorageError as error:
                    print(f"백그라운드 대화 재확인 오류: {error}")
                else:
                    if pending_messages:
                        schedule_conversation_maintenance(
                            session_key,
                            user_id,
                        )
                    elif session_key not in queued_maintenance_sessions:
                        maintenance_not_before.pop(session_key, None)


async def process_conversation_message(
    message: discord.Message,
    user_message: str,
) -> None:
    guild_id = message.guild.id if message.guild is not None else None
    session_key = build_session_key(
        guild_id, message.channel.id, message.author.id
    )

    conversation_summary = ""
    try:
        session_record = await get_session_record(session_key)
    except ConversationStorageError as error:
        print(f"대화 조회 오류: {error}")
        conversation_history = []
    else:
        raw_summary = session_record["summary"]
        raw_messages = session_record["messages"]
        if isinstance(raw_summary, str):
            conversation_summary = raw_summary
        conversation_history = [
            {
                "role": str(item.get("role", "")),
                "content": str(item.get("content", "")),
            }
            for item in raw_messages
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
        ]

    try:
        storage_enabled = await is_storage_enabled(message.author.id)
    except ConversationStorageError as error:
        print(f"대화 저장 설정 조회 오류: {error}")
        storage_enabled = False

    try:
        approved_memory_records = await get_user_memories(
            message.author.id,
            include_candidates=False,
        )
    except ConversationStorageError as error:
        print(f"장기 기억 조회 오류: {error}")
        approved_memories = []
    else:
        approved_memories = [
            str(item["content"])
            for item in approved_memory_records
            if item.get("status") == "approved"
        ]

    generated_reply: str | None = None
    llm_started_at = time.perf_counter()
    async with message.channel.typing():
        try:
            generated_reply = await generate_reply(
                user_message,
                conversation_history,
                conversation_summary,
                approved_memories,
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

    if generated_reply is None or not storage_enabled:
        return

    try:
        messages_to_summarize = await save_exchange(
            session_key,
            guild_id,
            message.channel.id,
            message.author.id,
            redact_discord_token(user_message),
            redact_discord_token(generated_reply),
        )
    except ConversationStorageError as error:
        print(f"대화 저장 오류: {error}")
        return

    if messages_to_summarize:
        schedule_conversation_maintenance(
            session_key,
            message.author.id,
        )


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
    client.run(DISCORD_TOKEN)
