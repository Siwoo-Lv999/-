import asyncio
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("DISCORD_TOKEN", "test-token")

import discord

import main
import storage


class MainRestartTests(unittest.TestCase):
    def test_restart_drains_database_writes_before_clearing_all_history(
        self,
    ) -> None:
        write_started = threading.Event()
        release_write = threading.Event()
        write_finished = threading.Event()
        pending_writes: list[asyncio.Task[None]] = []
        save_exchange = storage._save_exchange
        bot_client = main.LunaClient(intents=discord.Intents.none())

        def delayed_save(*arguments: object) -> None:
            if arguments[3] == 30:
                write_started.set()
                if not release_write.wait(timeout=5):
                    raise RuntimeError("Pending database write was not released")
            save_exchange(*arguments)
            if arguments[3] == 30:
                write_finished.set()

        async def pending_write() -> None:
            try:
                await storage.save_exchange(
                    "dm:3:user:30", None, 3, 30, "late question", "late reply"
                )
            finally:
                release_write.set()

        async def connected(*arguments: object, **options: object) -> None:
            await storage.save_exchange(
                "guild:1:channel:1:user:10", 1, 1, 10, "question", "reply"
            )
            await storage.save_exchange(
                "dm:2:user:20", None, 2, 20, "DM question", "DM reply"
            )
            pending_writes.append(asyncio.create_task(pending_write()))
            await asyncio.Event().wait()

        async def request_restart(bot: main.LunaClient) -> None:
            started = await asyncio.to_thread(write_started.wait, 5)
            self.assertTrue(started, "Pending database write did not start")
            bot.restart_requested = True

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "bot.db"

            def verify_replacement(*arguments: object) -> None:
                self.assertTrue(write_finished.is_set())
                self.assertTrue(pending_writes[0].cancelled())
                with closing(sqlite3.connect(database_path)) as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM conversation_messages"
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM conversation_sessions"
                        ).fetchone()[0],
                        0,
                    )

            with (
                patch.object(storage, "DATABASE_PATH", database_path),
                patch.object(storage, "_save_exchange", side_effect=delayed_save),
                patch.object(main, "client", bot_client),
                patch.object(main, "initialize_auto_roles"),
                patch.object(main, "run_daily_restart", side_effect=request_restart),
                patch.object(discord.Client, "start", new=connected),
                patch.object(discord.utils, "setup_logging"),
                patch.object(main.os, "execv", side_effect=verify_replacement) as restart,
            ):
                main.run_application([])

        restart.assert_called_once()


if __name__ == "__main__":
    unittest.main()
