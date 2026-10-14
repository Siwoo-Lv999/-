import os
import unittest
from unittest.mock import patch


os.environ.setdefault("DISCORD_TOKEN", "test-token")

import main


class MainCliTests(unittest.TestCase):
    def test_reset_clears_database_without_starting_bot(self) -> None:
        with (
            patch.object(main, "initialize_database", return_value=7) as reset,
            patch.object(main, "initialize_auto_roles") as initialize_roles,
            patch.object(main.client, "run") as run_client,
            patch.object(main.os, "execv") as restart,
        ):
            main.run_application(["reset"])

        reset.assert_called_once_with(clear_conversations=True)
        initialize_roles.assert_not_called()
        run_client.assert_not_called()
        restart.assert_not_called()

    def test_normal_start_clears_database_before_starting_bot(self) -> None:
        with (
            patch.object(main, "initialize_database", return_value=0) as reset,
            patch.object(main, "initialize_auto_roles") as initialize_roles,
            patch.object(main.client, "run") as run_client,
            patch.object(main.os, "execv") as restart,
        ):
            main.run_application([])

        reset.assert_called_once_with(clear_conversations=True)
        initialize_roles.assert_called_once_with()
        run_client.assert_called_once_with(main.DISCORD_TOKEN)
        restart.assert_not_called()

    def test_scheduled_restart_resets_after_bot_stops_and_preserves_options(
        self,
    ) -> None:
        events: list[str] = []

        def reset_database(*, clear_conversations: bool = False) -> int:
            self.assertTrue(clear_conversations)
            events.append("reset")
            return 0

        def run_client(token: str) -> None:
            events.append("run")
            main.client.restart_requested = True

        def restart_process(executable: str, arguments: list[str]) -> None:
            events.append("restart")
            self.assertEqual(executable, main.sys.executable)
            self.assertEqual(
                arguments,
                [main.sys.executable, "-u", "-X", "utf8", "main.py"],
            )

        with (
            patch.object(main, "initialize_database", side_effect=reset_database),
            patch.object(main, "initialize_auto_roles"),
            patch.object(main.client, "restart_requested", False),
            patch.object(main.client, "run", side_effect=run_client),
            patch.object(main.os, "execv", side_effect=restart_process),
            patch.object(
                main.sys, "orig_argv", ["python", "-u", "-X", "utf8", "main.py"]
            ),
            patch.object(main.sys.stdout, "flush") as flush_stdout,
            patch.object(main.sys.stderr, "flush") as flush_stderr,
        ):
            main.run_application([])

        self.assertEqual(events, ["reset", "run", "reset", "restart"])
        flush_stdout.assert_called_once_with()
        flush_stderr.assert_called_once_with()

    def test_failed_reset_does_not_launch_replacement_process(self) -> None:
        def run_client(token: str) -> None:
            main.client.restart_requested = True

        with (
            patch.object(
                main,
                "initialize_database",
                side_effect=[0, main.ConversationStorageError("reset failed")],
            ),
            patch.object(main, "initialize_auto_roles"),
            patch.object(main.client, "restart_requested", False),
            patch.object(main.client, "run", side_effect=run_client),
            patch.object(main.os, "execv") as restart,
        ):
            with self.assertRaises(main.ConversationStorageError):
                main.run_application([])

        restart.assert_not_called()

    def test_unknown_command_does_not_start_bot(self) -> None:
        with patch.object(main.client, "run") as run_client:
            with self.assertRaisesRegex(SystemExit, "사용법"):
                main.run_application(["unknown"])

        run_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
