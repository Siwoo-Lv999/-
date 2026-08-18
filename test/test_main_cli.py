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
        ):
            main.run_application(["reset"])

        reset.assert_called_once_with(clear_conversations=True)
        initialize_roles.assert_not_called()
        run_client.assert_not_called()

    def test_normal_start_clears_database_before_starting_bot(self) -> None:
        with (
            patch.object(main, "initialize_database", return_value=0) as reset,
            patch.object(main, "initialize_auto_roles") as initialize_roles,
            patch.object(main.client, "run") as run_client,
        ):
            main.run_application([])

        reset.assert_called_once_with(clear_conversations=True)
        initialize_roles.assert_called_once_with()
        run_client.assert_called_once_with(main.DISCORD_TOKEN)

    def test_unknown_command_does_not_start_bot(self) -> None:
        with patch.object(main.client, "run") as run_client:
            with self.assertRaisesRegex(SystemExit, "사용법"):
                main.run_application(["unknown"])

        run_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
