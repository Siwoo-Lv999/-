import discord
from discord import app_commands

from config import DISCORD_TOKEN
from llm import (
    LlmConnectionError,
    LlmResponseError,
    LlmTimeoutError,
    generate_reply,
)
from storage import (
    ConversationStorageError,
    build_session_key,
    delete_user_conversations,
    get_recent_messages,
    initialize_database,
    save_exchange,
)


EMPTY_MESSAGE_REPLY = "무엇을 도와드릴까요, 선생님?"
CONNECTION_ERROR_REPLY = (
    "현재 언어 모델에 연결할 수 없습니다.\nOllama가 실행 중인지 확인해 주세요."
)
TIMEOUT_ERROR_REPLY = "답변 생성 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
RESPONSE_ERROR_REPLY = "답변을 생성하는 중 문제가 발생했습니다."
DISCORD_MESSAGE_LIMIT = 2000

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
command_tree = app_commands.CommandTree(client)
conversation_group = app_commands.Group(
    name="대화", description="저장된 대화 기록을 관리합니다."
)
commands_synced = False


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

    await message.reply(chunks[0], mention_author=False)
    for chunk in chunks[1:]:
        await message.channel.send(chunk)


@conversation_group.command(
    name="초기화", description="내 모든 대화 기록을 삭제합니다."
)
async def reset_conversation(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
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


@client.event
async def on_ready() -> None:
    global commands_synced

    if not commands_synced:
        try:
            synced_commands = await command_tree.sync()
        except discord.HTTPException as error:
            print(f"슬래시 명령어 동기화 오류: {error}")
        else:
            commands_synced = True
            print(f"슬래시 명령어를 동기화했습니다: {len(synced_commands)}개")

    print(f"Discord에 로그인했습니다: {client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
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

        async with message.channel.typing():
            if not user_message:
                await message.reply(EMPTY_MESSAGE_REPLY, mention_author=False)
                return

            guild_id = message.guild.id if message.guild is not None else None
            session_key = build_session_key(
                guild_id, message.channel.id, message.author.id
            )

            try:
                conversation_history = await get_recent_messages(session_key)
            except ConversationStorageError as error:
                print(f"대화 조회 오류: {error}")
                conversation_history = []

            generated_reply: str | None = None
            try:
                generated_reply = await generate_reply(
                    user_message, conversation_history
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

            await send_reply(message, reply)

            if generated_reply is not None:
                try:
                    await save_exchange(
                        session_key,
                        guild_id,
                        message.channel.id,
                        message.author.id,
                        user_message,
                        generated_reply,
                    )
                except ConversationStorageError as error:
                    print(f"대화 저장 오류: {error}")
    except Exception as error:
        print(f"메시지 처리 중 오류가 발생했습니다: {error}")


if __name__ == "__main__":
    initialize_database()
    client.run(DISCORD_TOKEN)
