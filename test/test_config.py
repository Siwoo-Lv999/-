import os
import subprocess
import sys
import unittest


class ConfigTests(unittest.TestCase):
    def test_webhook_can_use_only_repository_channel_mappings(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "DISCORD_TOKEN": "test-token",
                "GITHUB_WEBHOOK_ENABLED": "true",
                "GITHUB_WEBHOOK_SECRET": "test-secret",
                "GITHUB_WEBHOOK_CHANNEL_ID": "",
            }
        )

        result = subprocess.run(
            [sys.executable, "-c", "import config"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
