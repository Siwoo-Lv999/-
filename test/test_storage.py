import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("DISCORD_TOKEN", "test-token")

import storage


class InitializeDatabaseTests(unittest.TestCase):
    def test_initialization_migrates_database_without_sessions_table(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "bot.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE conversation_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_key TEXT NOT NULL,
                        guild_id INTEGER,
                        channel_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO conversation_messages (
                        session_key, guild_id, channel_id, user_id,
                        role, content
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "guild:1:channel:2:user:10",
                        1,
                        2,
                        10,
                        "user",
                        "이전 버전 질문",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()

            connection = sqlite3.connect(database_path)
            try:
                migrated_session = connection.execute(
                    """
                    SELECT guild_id, channel_id, user_id
                    FROM conversation_sessions
                    WHERE session_key = ?
                    """,
                    ("guild:1:channel:2:user:10",),
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(migrated_session, (1, 2, 10))

    def test_sessions_are_isolated_by_user_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "bot.db"
            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()
                asyncio.run(
                    storage.save_exchange(
                        "guild:1:channel:2:user:10",
                        1,
                        2,
                        10,
                        "첫 번째 사용자 질문",
                        "첫 번째 사용자 답변",
                    )
                )
                asyncio.run(
                    storage.save_exchange(
                        "guild:1:channel:2:user:20",
                        1,
                        2,
                        20,
                        "두 번째 사용자 질문",
                        "두 번째 사용자 답변",
                    )
                )

                first_record = asyncio.run(
                    storage.get_session_record("guild:1:channel:2:user:10")
                )
                second_record = asyncio.run(
                    storage.get_session_record("guild:1:channel:2:user:20")
                )

        self.assertEqual(
            [item["content"] for item in first_record["messages"]],
            ["첫 번째 사용자 질문", "첫 번째 사용자 답변"],
        )
        self.assertEqual(
            [item["content"] for item in second_record["messages"]],
            ["두 번째 사용자 질문", "두 번째 사용자 답변"],
        )

    def test_rejects_mismatched_session_and_user(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "bot.db"
            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()
                with self.assertRaisesRegex(
                    storage.ConversationStorageError,
                    "사용자 정보가 일치하지 않습니다",
                ):
                    asyncio.run(
                        storage.save_exchange(
                            "guild:1:channel:2:user:10",
                            1,
                            2,
                            20,
                            "질문",
                            "답변",
                        )
                    )

    def test_clear_conversations_removes_messages_and_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "bot.db"
            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()
                asyncio.run(
                    storage.save_exchange(
                        "dm:10:user:20",
                        None,
                        10,
                        20,
                        "이전 질문",
                        "이전 답변",
                    )
                )

                deleted_count = storage.initialize_database(
                    clear_conversations=True
                )

                self.assertEqual(deleted_count, 3)
                record = asyncio.run(
                    storage.get_session_record("dm:10:user:20")
                )
                self.assertEqual(record["messages"], [])

                connection = sqlite3.connect(database_path)
                try:
                    session_count = connection.execute(
                        "SELECT COUNT(*) FROM conversation_sessions"
                    ).fetchone()[0]
                finally:
                    connection.close()
                self.assertEqual(session_count, 0)


if __name__ == "__main__":
    unittest.main()
