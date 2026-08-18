import os
import unittest


os.environ.setdefault("DISCORD_TOKEN", "test-token")

from github_webhook import normalize_repository_name


class GithubWebhookTests(unittest.TestCase):
    def test_repository_name_strips_whitespace_around_parts(self) -> None:
        self.assertEqual(
            normalize_repository_name(" Owner / Repository "),
            "owner/repository",
        )

    def test_repository_name_rejects_extra_path_parts(self) -> None:
        with self.assertRaises(RuntimeError):
            normalize_repository_name("owner/repository/extra")


if __name__ == "__main__":
    unittest.main()
