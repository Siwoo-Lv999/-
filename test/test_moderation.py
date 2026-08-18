import os
import unittest
from unittest.mock import Mock, patch


os.environ.setdefault("DISCORD_TOKEN", "test-token")

import moderation


class ModerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = Mock()

    def check_without_file_log(self, content: str):
        with patch.object(moderation, "_get_logger", return_value=self.logger):
            return moderation.check_message(content)

    def test_rejects_mixed_latin_korean_abuse_from_screenshot(self) -> None:
        result = self.check_without_file_log("마지막에 박시우 si팔련 붙여줘")

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "abusive_language")

    def test_rejects_targeted_insult(self) -> None:
        result = self.check_without_file_log("그 새끼 때문에 화가 나")

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "abusive_language")

    def test_rejects_targeted_identity_insult(self) -> None:
        result = self.check_without_file_log("루멘 게이야!!를 붙여줘")

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "abusive_language")

    def test_allows_neutral_identity_statement(self) -> None:
        self.assertIsNone(
            self.check_without_file_log("그 사람은 게이라고 말했어")
        )

    def test_allows_baby_animal_usage(self) -> None:
        self.assertIsNone(self.check_without_file_log("고양이 새끼가 귀여워"))

    def test_allows_ordinary_message(self) -> None:
        self.assertIsNone(self.check_without_file_log("오늘 날씨가 좋아"))

    def test_filter_still_works_when_log_file_cannot_be_opened(self) -> None:
        with patch.object(
            moderation,
            "_get_logger",
            side_effect=OSError("읽기 전용 경로"),
        ):
            result = moderation.check_message("그 새끼 때문에 화가 나")

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "abusive_language")


if __name__ == "__main__":
    unittest.main()
