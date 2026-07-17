import asyncio
import sqlite3

from config import DATABASE_PATH


RECENT_MESSAGE_LIMIT = 10


class ConversationStorageError(Exception):
    pass


def build_session_key(
    guild_id: int | None, channel_id: int, user_id: int
) -> str:
    if guild_id is None:
        return f"dm:{channel_id}:user:{user_id}"
    return f"guild:{guild_id}:channel:{channel_id}:user:{user_id}"


def initialize_database() -> None:
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DATABASE_PATH, timeout=5) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    guild_id INTEGER,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_session
                ON conversation_messages (session_key, id)
                """
            )
    except (OSError, sqlite3.Error) as error:
        raise ConversationStorageError(
            "대화 데이터베이스를 초기화할 수 없습니다."
        ) from error


def _get_recent_messages(session_key: str) -> list[dict[str, str]]:
    with sqlite3.connect(DATABASE_PATH, timeout=5) as connection:
        rows = connection.execute(
            """
            SELECT role, content
            FROM (
                SELECT id, role, content
                FROM conversation_messages
                WHERE session_key = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (session_key, RECENT_MESSAGE_LIMIT),
        ).fetchall()
    return [{"role": role, "content": content} for role, content in rows]


async def get_recent_messages(session_key: str) -> list[dict[str, str]]:
    try:
        return await asyncio.to_thread(_get_recent_messages, session_key)
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "최근 대화를 불러올 수 없습니다."
        ) from error


def _save_exchange(
    session_key: str,
    guild_id: int | None,
    channel_id: int,
    user_id: int,
    user_message: str,
    assistant_message: str,
) -> None:
    with sqlite3.connect(DATABASE_PATH, timeout=5) as connection:
        connection.executemany(
            """
            INSERT INTO conversation_messages (
                session_key, guild_id, channel_id, user_id, role, content
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    session_key,
                    guild_id,
                    channel_id,
                    user_id,
                    "user",
                    user_message,
                ),
                (
                    session_key,
                    guild_id,
                    channel_id,
                    user_id,
                    "assistant",
                    assistant_message,
                ),
            ),
        )
        connection.execute(
            """
            DELETE FROM conversation_messages
            WHERE session_key = ?
              AND id NOT IN (
                  SELECT id
                  FROM conversation_messages
                  WHERE session_key = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (session_key, session_key, RECENT_MESSAGE_LIMIT),
        )


async def save_exchange(
    session_key: str,
    guild_id: int | None,
    channel_id: int,
    user_id: int,
    user_message: str,
    assistant_message: str,
) -> None:
    try:
        await asyncio.to_thread(
            _save_exchange,
            session_key,
            guild_id,
            channel_id,
            user_id,
            user_message,
            assistant_message,
        )
    except sqlite3.Error as error:
        raise ConversationStorageError("대화를 저장할 수 없습니다.") from error


def _delete_user_conversations(user_id: int) -> int:
    with sqlite3.connect(DATABASE_PATH, timeout=5) as connection:
        cursor = connection.execute(
            "DELETE FROM conversation_messages WHERE user_id = ?",
            (user_id,),
        )
    return cursor.rowcount


async def delete_user_conversations(user_id: int) -> int:
    try:
        return await asyncio.to_thread(_delete_user_conversations, user_id)
    except sqlite3.Error as error:
        raise ConversationStorageError("대화를 삭제할 수 없습니다.") from error
