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
