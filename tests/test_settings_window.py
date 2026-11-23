import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from companion import config  # noqa: E402
from companion.settings_window import SettingsChange, SettingsWindow  # noqa: E402

app = QApplication.instance() or QApplication([])


def window(settings=None, claude_key=False, brave_key=False):
    return SettingsWindow(settings or config.Settings(), claude_key, brave_key, on_top=False)


class SettingsWindowTest(unittest.TestCase):
    def test_shows_the_settings_it_was_given(self):
        settings = config.Settings(
            brain="claude",
            device="cpu",
            voice="af_bella",
            voice_pitch=1.15,
            temperature=0.9,
            web_location="Herk-de-Stad",
            character_height=400,
        )
        shown = window(settings, claude_key=True)
        self.assertEqual(shown.values(), settings)
        self.assertEqual(shown.change().changed, set())
        self.assertFalse(shown.save_button.isEnabled())

    def test_values_that_are_not_in_the_lists_are_kept(self):
        settings = config.Settings(voice="af_sky+af_nicole", context_tokens=12000, voice_pitch=1.153)
        shown = window(settings)
        self.assertEqual(shown.fields["voice"].get(), "af_sky+af_nicole")
        self.assertEqual(shown.fields["context_tokens"].get(), 12000)
        # A value the slider can't show exactly is only changed when you move the slider.
        self.assertEqual(shown.change().changed, set())

    def test_changes_are_collected(self):
        shown = window()
        shown.fields["device"].set("cpu")
        shown.fields["voice_volume"].set(0.5)
        change = shown.change()
        self.assertEqual(change.changed, {"device", "voice_volume"})
        self.assertEqual(change.settings.voice_volume, 0.5)
        self.assertIsInstance(change.settings.voice_volume, float)
        self.assertTrue(shown.save_button.isEnabled())

    def test_conversation_memory_fits_the_working_memory(self):
        shown = window()
        shown.fields["history_tokens"].set(6000)
        shown.fields["context_tokens"].set(4096)
        limit = config.history_limit(4096, config.Settings().max_reply_tokens)
        self.assertEqual(shown.fields["history_tokens"].get(), limit)

    def test_claude_needs_a_key(self):
        shown = window()
        shown.fields["brain"].set("claude")
        self.assertIsNotNone(shown._problem())
        shown.claude_key.edit.setText("not-a-key")
        self.assertIsNotNone(shown._problem())
        shown.claude_key.edit.setText("sk-ant-" + "a" * 40)
        self.assertIsNone(shown._problem())
        self.assertEqual(shown.change().claude_key, "sk-ant-" + "a" * 40)

    def test_forgetting_a_saved_key(self):
        shown = window(brave_key=True)
        self.assertIsNone(shown.change().brave_key)
        shown.brave_key.forget.setChecked(True)
        self.assertEqual(shown.change().brave_key, "")

    def test_saving_sends_the_change(self):
        shown = window()
        received = []
        shown.saved.connect(received.append)
        shown.fields["bubble_seconds"].set(60)
        shown._save()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].changed, {"bubble_seconds"})

    def test_defaults_button(self):
        shown = window(config.Settings(voice_volume=0.3, always_on_top=False))
        shown._restore_defaults()
        self.assertEqual(shown.change().changed, {"voice_volume", "always_on_top"})


class ReloadsTest(unittest.TestCase):
    def change(self, brain="local", changed=(), claude_key=None):
        settings = replace(config.Settings(), brain=brain)
        return SettingsChange(settings, set(changed), claude_key)

    def test_what_saving_reloads(self):
        self.assertEqual(self.change(changed={"bubble_seconds"}).reloads(), [])
        self.assertIn("local brain", self.change(changed={"device"}).reloads()[0])
        self.assertEqual(self.change("claude", {"device"}).reloads(), [])
        self.assertIn("reconnects", self.change("claude", {"claude_model"}).reloads()[0])
        self.assertIn("reconnects", self.change("claude", claude_key="sk-ant-x").reloads()[0])
        self.assertIn("switches", self.change("claude", {"brain", "device"}).reloads()[0])
        self.assertIn("voice", self.change(changed={"voice_threads"}).reloads()[0])


class HistoryLimitTest(unittest.TestCase):
    def test_limit(self):
        self.assertEqual(config.history_limit(8192, 320), 8192 - 320 - config.PROMPT_RESERVE_TOKENS)
        self.assertEqual(config.history_limit(2048, 4096), config.LIMITS["history_tokens"][0])

    def test_changed_settings(self):
        old = config.Settings()
        self.assertEqual(config.changed_settings(old, replace(old, voice="af_bella", threads=4)), {"voice", "threads"})


if __name__ == "__main__":
    unittest.main()
