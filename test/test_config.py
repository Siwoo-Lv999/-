import os
import subprocess
import sys
import unittest


class ConfigTests(unittest.TestCase):
    def run_config(self, settings: dict[str, str]) -> subprocess.CompletedProcess[str]:
        environment = {
            name: value for name, value in os.environ.items()
            if name != "LLM_PROVIDER" and not name.startswith(("OPENAI_", "OLLAMA_"))
        }
        environment.update({"DISCORD_TOKEN": "test-token", "GITHUB_WEBHOOK_ENABLED": "false"})
        environment.update(settings)
        # 실제 .env의 키와 설정을 사용하지 않도록 파일 로딩을 막는다.
        script = (
            "from unittest.mock import patch\n"
            "with patch('dotenv.load_dotenv'):\n"
            "    import config\n"
            "    print(config.LLM_PROVIDER, config.LLM_MODEL, config.OLLAMA_WARMUP_ON_START)\n"
        )
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-c", script],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=environment,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def test_auto_selects_provider_using_only_api_key(self) -> None:
        for settings, expected in (
            ({}, "ollama gemma3:4b True"),
            ({"OPENAI_API_KEY": "   "}, "ollama gemma3:4b True"),
            ({"OPENAI_API_KEY": "test-key"}, "openai gpt-6-luna False"),
            ({"LLM_PROVIDER": "auto", "OPENAI_API_KEY": "test-key"}, "openai gpt-6-luna False"),
        ):
            with self.subTest(settings=settings):
                result = self.run_config(settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_explicit_ollama_keeps_key_and_local_model(self) -> None:
        result = self.run_config({
            "LLM_PROVIDER": "ollama",
            "OPENAI_API_KEY": "test-key",
            "OLLAMA_MODEL": "existing-local-model",
            "OLLAMA_WARMUP_ON_START": "false",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ollama existing-local-model False")

    def test_explicit_openai_requires_key(self) -> None:
        result = self.run_config({"LLM_PROVIDER": "openai"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPENAI_API_KEY", result.stderr)

    def test_rejects_unknown_provider(self) -> None:
        result = self.run_config({"LLM_PROVIDER": "unknown"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LLM_PROVIDER", result.stderr)

    def test_inactive_provider_settings_do_not_block_startup(self) -> None:
        for settings, expected in (
            ({
                "LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-key",
                "OLLAMA_MODEL": "", "OLLAMA_NUM_CTX": "invalid",
                "OLLAMA_TIMEOUT_SECONDS": "invalid", "OLLAMA_KEEP_ALIVE": "",
                "OLLAMA_WARMUP_ON_START": "invalid",
            }, "openai gpt-6-luna False"),
            ({
                "LLM_PROVIDER": "ollama", "OPENAI_MODEL": "",
                "OPENAI_TIMEOUT_SECONDS": "invalid", "OPENAI_MAX_OUTPUT_TOKENS": "invalid",
                "OPENAI_REASONING_EFFORT": "invalid",
            }, "ollama gemma3:4b True"),
        ):
            with self.subTest(provider=settings["LLM_PROVIDER"]):
                result = self.run_config(settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_rejects_invalid_active_openai_settings(self) -> None:
        for name, value in (
            ("OPENAI_MODEL", ""), ("OPENAI_TIMEOUT_SECONDS", "0"),
            ("OPENAI_MAX_OUTPUT_TOKENS", "-1"), ("OPENAI_MAX_OUTPUT_TOKENS", "invalid"),
            ("OPENAI_REASONING_EFFORT", "invalid"),
        ):
            with self.subTest(name=name, value=value):
                result = self.run_config({"OPENAI_API_KEY": "test-key", name: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_webhook_can_use_only_repository_channel_mappings(self) -> None:
        result = self.run_config(
            {
                "DISCORD_TOKEN": "test-token",
                "GITHUB_WEBHOOK_ENABLED": "true",
                "GITHUB_WEBHOOK_SECRET": "test-secret",
                "GITHUB_WEBHOOK_CHANNEL_ID": "",
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
