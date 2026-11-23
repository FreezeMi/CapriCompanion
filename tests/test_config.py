import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from companion import config


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.file = Path(tempfile.mkdtemp()) / "settings.json"
        self.patch = mock.patch.object(config, "SETTINGS_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_missing_file_gives_defaults_and_writes_them(self):
        settings, problems = config.load_settings()
        self.assertEqual(settings, config.Settings())
        self.assertEqual(problems, [])
        self.assertEqual(json.loads(self.file.read_text())["device"], "gpu")

    def test_invalid_values_fall_back(self):
        self.file.write_text(
            json.dumps({"threads": 0, "device": "tpu", "temperature": True, "always_on_top": "yes"})
        )
        settings, problems = config.load_settings()
        self.assertEqual(settings.threads, config.Settings().threads)
        self.assertEqual(settings.device, "gpu")
        self.assertEqual(settings.temperature, 0.7)
        self.assertTrue(settings.always_on_top)
        self.assertEqual(len(problems), 4)

    def test_brain_settings_are_checked(self):
        self.file.write_text(
            json.dumps({"brain": "gpt", "claude_model": "claude-2", "claude_max_searches": 50, "claude_search": "auto"})
        )
        settings, problems = config.load_settings()
        self.assertEqual(settings.brain, "local")
        self.assertEqual(settings.claude_model, "claude-haiku-5-5")
        self.assertEqual(settings.claude_max_searches, 3)
        self.assertEqual(settings.claude_search, "auto")
        self.assertEqual(len(problems), 3)

    def test_history_is_limited_by_context(self):
        self.file.write_text(json.dumps({"context_tokens": 4096, "history_tokens": 9000}))
        settings, _ = config.load_settings()
        self.assertLessEqual(
            settings.history_tokens,
            4096 - settings.max_reply_tokens - config.PROMPT_RESERVE_TOKENS,
        )

    def test_broken_json_is_reported(self):
        self.file.write_text("{not json")
        settings, problems = config.load_settings()
        self.assertEqual(settings, config.Settings())
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
