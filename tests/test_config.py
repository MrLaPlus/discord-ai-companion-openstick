import os
import unittest
from unittest.mock import patch

from bot.config import Config


class ConfigTests(unittest.TestCase):
    def load(self, **env):
        with patch.dict(os.environ, env, clear=True), patch("dotenv.load_dotenv"):
            return Config.load(require_secrets=False)

    def test_defaults_use_selected_model_and_hide_secrets_in_repr(self):
        config = self.load(DISCORD_TOKEN="hidden-discord", GROQ_API_KEY="hidden-groq")
        self.assertEqual(config.model, "qwen/qwen3.8-27b")
        self.assertNotIn("hidden-", repr(config))

    def test_reject_insecure_or_credentialed_endpoints(self):
        for url in ["http://example.com", "https://user:password@example.com", "https://example.com?key=abc"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.load(GROQ_BASE_URL=url)

    def test_local_mcp_allows_only_exact_loopback_http_endpoint(self):
        self.assertEqual(self.load(MCP_URL="http://127.0.0.1:8000/mcp").mcp_url, "http://127.0.0.1:8000/mcp")
        for url in (
            "http://127.0.0.1:8001/mcp",
            "http://192.0.2.10:8000/mcp",
            "http://localhost:8000/mcp",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.load(MCP_URL=url)

    def test_additional_guilds_are_bounded_deduplicated_and_require_primary(self):
        config = self.load(DISCORD_GUILD_ID="10", DISCORD_ADDITIONAL_GUILD_IDS="20, 30,20,10")
        self.assertEqual(config.guild_id, 10)
        self.assertEqual(config.additional_guild_ids, (20, 30))
        for env in (
            {"DISCORD_ADDITIONAL_GUILD_IDS": "20"},
            {"DISCORD_GUILD_ID": "10", "DISCORD_ADDITIONAL_GUILD_IDS": "20,,30"},
            {"DISCORD_GUILD_ID": "10", "DISCORD_ADDITIONAL_GUILD_IDS": "-20"},
            {
                "DISCORD_GUILD_ID": "10",
                "DISCORD_ADDITIONAL_GUILD_IDS": ",".join(str(x) for x in range(1, 12)),
            },
        ):
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.load(**env)

    def test_reject_invalid_limits_and_guild(self):
        for env in [
            {"WORKERS": "0"},
            {"DISCORD_GUILD_ID": "-1"},
            {"TOKENS_PER_MINUTE": "512"},
            {"QUEUE_SIZE": "wrong"},
            {"TIMEZONE": "not/a/zone"},
        ]:
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.load(**env)

    def test_missing_credentials_stop_real_startup(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("dotenv.load_dotenv"),
            self.assertRaises(ValueError),
        ):
            Config.load()
