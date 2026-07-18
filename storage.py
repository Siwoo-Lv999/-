import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from config import CONVERSATION_RETENTION_DAYS, DATABASE_PATH


RECENT_MESSAGE_LIMIT = 10
SUMMARY_BATCH_MESSAGE_LIMIT = 10


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
    connection.execute(
        """
        DELETE FROM user_memories
        WHERE updated_at < datetime('now', ?)
        """,
        (retention_modifier,),
    )


def initialize_database() -> None:
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
                CREATE TABLE IF NOT EXISTS conversation_preferences (
                    user_id INTEGER PRIMARY KEY,
                    storage_enabled INTEGER NOT NULL DEFAULT 1
                        CHECK (storage_enabled IN (0, 1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate'
                        CHECK (status IN ('candidate', 'approved')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (user_id, content)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_memories_user
                ON user_memories (user_id, status, id)
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
            _purge_expired_conversation_data(connection)
    except (OSError, sqlite3.Error) as error:
        raise ConversationStorageError(
            "대화 데이터베이스를 초기화할 수 없습니다."
        ) from error


def _get_session_record(session_key: str) -> dict[str, object]:
    with _connect() as connection:
        summary_row = connection.execute(
            """
            SELECT summary
            FROM conversation_sessions
            WHERE session_key = ?
            """,
            (session_key,),
        ).fetchone()
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
        "summary": summary_row[0] if summary_row else "",
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


def _is_storage_enabled(user_id: int) -> bool:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT storage_enabled
            FROM conversation_preferences
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return row is None or bool(row[0])


async def is_storage_enabled(user_id: int) -> bool:
    try:
        return await asyncio.to_thread(_is_storage_enabled, user_id)
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "대화 저장 설정을 불러올 수 없습니다."
        ) from error


def _set_storage_enabled(user_id: int, enabled: bool) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO conversation_preferences (
                user_id, storage_enabled, updated_at
            )
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                storage_enabled = excluded.storage_enabled,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, int(enabled)),
        )


async def set_storage_enabled(user_id: int, enabled: bool) -> None:
    try:
        await asyncio.to_thread(_set_storage_enabled, user_id, enabled)
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "대화 저장 설정을 변경할 수 없습니다."
        ) from error


def _get_user_memories(
    user_id: int,
    include_candidates: bool,
) -> list[dict[str, object]]:
    status_filter = "" if include_candidates else "AND status = 'approved'"
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT id, category, content, status, created_at
            FROM user_memories
            WHERE user_id = ?
              {status_filter}
            ORDER BY
                CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                id ASC
            """,
            (user_id,),
        ).fetchall()

    return [
        {
            "id": memory_id,
            "category": category,
            "content": content,
            "status": status,
            "created_at": created_at,
        }
        for memory_id, category, content, status, created_at in rows
    ]


async def get_user_memories(
    user_id: int,
    include_candidates: bool = True,
) -> list[dict[str, object]]:
    try:
        return await asyncio.to_thread(
            _get_user_memories,
            user_id,
            include_candidates,
        )
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "장기 기억을 불러올 수 없습니다."
        ) from error


def _save_memory_candidates(
    user_id: int,
    candidates: list[dict[str, str]],
) -> int:
    if not candidates:
        return 0

    with _connect() as connection:
        before_changes = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO user_memories (
                user_id, category, content, status
            )
            VALUES (?, ?, ?, 'candidate')
            """,
            [
                (user_id, item["category"], item["content"])
                for item in candidates
            ],
        )
        return connection.total_changes - before_changes


async def save_memory_candidates(
    user_id: int,
    candidates: list[dict[str, str]],
) -> int:
    try:
        return await asyncio.to_thread(
            _save_memory_candidates,
            user_id,
            candidates,
        )
    except (KeyError, sqlite3.Error) as error:
        raise ConversationStorageError(
            "장기 기억 후보를 저장할 수 없습니다."
        ) from error


def _approve_user_memory(user_id: int, memory_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE user_memories
            SET status = 'approved', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ? AND status = 'candidate'
            """,
            (memory_id, user_id),
        )
    return cursor.rowcount == 1


async def approve_user_memory(user_id: int, memory_id: int) -> bool:
    try:
        return await asyncio.to_thread(
            _approve_user_memory,
            user_id,
            memory_id,
        )
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "장기 기억을 승인할 수 없습니다."
        ) from error


def _delete_user_memory(user_id: int, memory_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute(
            """
            DELETE FROM user_memories
            WHERE id = ? AND user_id = ?
            """,
            (memory_id, user_id),
        )
    return cursor.rowcount == 1


async def delete_user_memory(user_id: int, memory_id: int) -> bool:
    try:
        return await asyncio.to_thread(
            _delete_user_memory,
            user_id,
            memory_id,
        )
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "장기 기억을 삭제할 수 없습니다."
        ) from error


def _save_exchange(
    session_key: str,
    guild_id: int | None,
    channel_id: int,
    user_id: int,
    user_message: str,
    assistant_message: str,
) -> list[dict[str, object]]:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO conversation_sessions (
                session_key, guild_id, channel_id, user_id, updated_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_key) DO UPDATE SET
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
        rows = connection.execute(
            """
            SELECT id, role, content
            FROM conversation_messages
            WHERE session_key = ?
              AND id NOT IN (
                  SELECT id
                  FROM conversation_messages
                  WHERE session_key = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            ORDER BY id ASC
            LIMIT ?
            """,
            (
                session_key,
                session_key,
                RECENT_MESSAGE_LIMIT,
                SUMMARY_BATCH_MESSAGE_LIMIT,
            ),
        ).fetchall()

    return [
        {"id": message_id, "role": role, "content": content}
        for message_id, role, content in rows
    ]


async def save_exchange(
    session_key: str,
    guild_id: int | None,
    channel_id: int,
    user_id: int,
    user_message: str,
    assistant_message: str,
) -> list[dict[str, object]]:
    try:
        return await asyncio.to_thread(
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


def _get_messages_to_summarize(
    session_key: str,
) -> list[dict[str, object]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, role, content
            FROM conversation_messages
            WHERE session_key = ?
              AND id NOT IN (
                  SELECT id
                  FROM conversation_messages
                  WHERE session_key = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            ORDER BY id ASC
            LIMIT ?
            """,
            (
                session_key,
                session_key,
                RECENT_MESSAGE_LIMIT,
                SUMMARY_BATCH_MESSAGE_LIMIT,
            ),
        ).fetchall()

    return [
        {"id": message_id, "role": role, "content": content}
        for message_id, role, content in rows
    ]


async def get_messages_to_summarize(
    session_key: str,
) -> list[dict[str, object]]:
    try:
        return await asyncio.to_thread(
            _get_messages_to_summarize,
            session_key,
        )
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "요약할 대화를 불러올 수 없습니다."
        ) from error


def _store_summary_and_delete_messages(
    session_key: str,
    summary: str,
    message_ids: list[int],
) -> None:
    if not message_ids:
        return

    placeholders = ", ".join("?" for _ in message_ids)
    with _connect() as connection:
        connection.execute(
            """
            UPDATE conversation_sessions
            SET summary = ?, updated_at = CURRENT_TIMESTAMP
            WHERE session_key = ?
            """,
            (summary, session_key),
        )
        connection.execute(
            f"""
            DELETE FROM conversation_messages
            WHERE session_key = ?
              AND id IN ({placeholders})
            """,
            (session_key, *message_ids),
        )


async def store_summary_and_delete_messages(
    session_key: str,
    summary: str,
    message_ids: list[int],
) -> None:
    try:
        await asyncio.to_thread(
            _store_summary_and_delete_messages,
            session_key,
            summary,
            message_ids,
        )
    except sqlite3.Error as error:
        raise ConversationStorageError(
            "대화 요약을 저장할 수 없습니다."
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
        memory_cursor = connection.execute(
            "DELETE FROM user_memories WHERE user_id = ?",
            (user_id,),
        )
    return (
        message_cursor.rowcount
        + session_cursor.rowcount
        + memory_cursor.rowcount
    )


async def delete_user_conversations(user_id: int) -> int:
    try:
        return await asyncio.to_thread(_delete_user_conversations, user_id)
    except sqlite3.Error as error:
        raise ConversationStorageError("대화를 삭제할 수 없습니다.") from error


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
