import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from config import CONVERSATION_RETENTION_DAYS, DATABASE_PATH


RECENT_EXCHANGE_LIMIT = 5
RECENT_MESSAGE_LIMIT = RECENT_EXCHANGE_LIMIT * 2


class ConversationStorageError(Exception):
    pass


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_PATH, timeout=5)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def build_session_key(
    guild_id: int | None, channel_id: int, user_id: int
) -> str:
    if guild_id is None:
        return f"dm:{channel_id}:user:{user_id}"
    return f"guild:{guild_id}:channel:{channel_id}:user:{user_id}"


def validate_session_identity(
    session_key: str,
    guild_id: int | None,
    channel_id: int,
    user_id: int,
) -> None:
    expected_session_key = build_session_key(guild_id, channel_id, user_id)
    if session_key != expected_session_key:
        raise ConversationStorageError(
            "대화 세션과 사용자 정보가 일치하지 않습니다."
        )


def _purge_expired_conversation_data(
    connection: sqlite3.Connection,
) -> None:
    if CONVERSATION_RETENTION_DAYS == 0:
        return

    retention_modifier = f"-{CONVERSATION_RETENTION_DAYS} days"
    connection.execute(
        """
        DELETE FROM conversation_messages
        WHERE created_at < datetime('now', ?)
        """,
        (retention_modifier,),
    )
    connection.execute(
        """
        DELETE FROM conversation_sessions
        WHERE updated_at < datetime('now', ?)
        """,
        (retention_modifier,),
    )


def _prune_all_sessions(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        DELETE FROM conversation_messages
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY session_key
                        ORDER BY id DESC
                    ) AS message_number
                FROM conversation_messages
            )
            WHERE message_number > ?
        )
        """,
        (RECENT_MESSAGE_LIMIT,),
    )


def initialize_database(*, clear_conversations: bool = False) -> int:
    deleted_record_count = 0
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as connection:
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_key TEXT PRIMARY KEY,
                    guild_id INTEGER,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_session_user
                ON conversation_sessions (user_id)
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO conversation_sessions (
                    session_key, guild_id, channel_id, user_id, updated_at
                )
                SELECT
                    session_key,
                    guild_id,
                    channel_id,
                    user_id,
                    MAX(created_at)
                FROM conversation_messages
                GROUP BY session_key, guild_id, channel_id, user_id
                """
            )

            # 이전 버전의 요약과 장기 기억은 더 이상 사용하지 않는다.
            connection.execute(
                "UPDATE conversation_sessions SET summary = ''"
            )
            connection.execute(
                "DROP TABLE IF EXISTS conversation_preferences"
            )
            connection.execute("DROP TABLE IF EXISTS user_memories")
            _prune_all_sessions(connection)
            _purge_expired_conversation_data(connection)

            if clear_conversations:
                message_cursor = connection.execute(
                    "DELETE FROM conversation_messages"
                )
                session_cursor = connection.execute(
                    "DELETE FROM conversation_sessions"
                )
                deleted_record_count = (
                    message_cursor.rowcount + session_cursor.rowcount
                )
    except (OSError, sqlite3.Error) as error:
        raise ConversationStorageError(
            "대화 데이터베이스를 초기화할 수 없습니다."
        ) from error
    return deleted_record_count


def _get_session_record(session_key: str) -> dict[str, object]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT role, content, created_at
            FROM (
                SELECT id, role, content, created_at
                FROM conversation_messages
                WHERE session_key = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (session_key, RECENT_MESSAGE_LIMIT),
        ).fetchall()

    return {
        "messages": [
            {"role": role, "content": content, "created_at": created_at}
            for role, content, created_at in rows
        ],
    }


async def get_session_record(session_key: str) -> dict[str, object]:
    try:
        return await asyncio.to_thread(_get_session_record, session_key)
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "대화 기록을 불러올 수 없습니다."
        ) from error


def _save_exchange(
    session_key: str,
    guild_id: int | None,
    channel_id: int,
    user_id: int,
    user_message: str,
    assistant_message: str,
) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO conversation_sessions (
                session_key, guild_id, channel_id, user_id, summary, updated_at
            )
            VALUES (?, ?, ?, ?, '', CURRENT_TIMESTAMP)
            ON CONFLICT(session_key) DO UPDATE SET
                summary = '',
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_key, guild_id, channel_id, user_id),
        )
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
    validate_session_identity(session_key, guild_id, channel_id, user_id)
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
        raise ConversationStorageError(
            "대화를 저장할 수 없습니다."
        ) from error


def _delete_user_conversations(user_id: int) -> int:
    with _connect() as connection:
        message_cursor = connection.execute(
            "DELETE FROM conversation_messages WHERE user_id = ?",
            (user_id,),
        )
        session_cursor = connection.execute(
            "DELETE FROM conversation_sessions WHERE user_id = ?",
            (user_id,),
        )
    return message_cursor.rowcount + session_cursor.rowcount


async def delete_user_conversations(user_id: int) -> int:
    try:
        return await asyncio.to_thread(_delete_user_conversations, user_id)
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "대화를 삭제할 수 없습니다."
        ) from error


def _purge_expired_records() -> None:
    with _connect() as connection:
        _purge_expired_conversation_data(connection)


async def purge_expired_records() -> None:
    try:
        await asyncio.to_thread(_purge_expired_records)
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "보존 기간이 지난 대화를 정리할 수 없습니다."
        ) from error
