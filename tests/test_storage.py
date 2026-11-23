import tempfile
import unittest
from pathlib import Path
from unittest import mock

from companion import storage


class EncryptionTest(unittest.TestCase):
    def test_round_trip(self):
        secret = "Hëllo, Capri 😊".encode("utf-8")
        sealed = storage.protect(secret)
        self.assertNotIn(secret, sealed)
        self.assertEqual(storage.unprotect(sealed), secret)

    def test_tampered_data_is_rejected(self):
        sealed = bytearray(storage.protect(b"private"))
        sealed[-5] ^= 0xFF
        with self.assertRaises(OSError):
            storage.unprotect(bytes(sealed))


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(storage, "HISTORY_FILE", self.folder / "history.bin")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_save_and_load(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "[happy] hey"},
        ]
        storage.save_history(messages)
        self.assertNotIn(b"hey", storage.HISTORY_FILE.read_bytes())
        self.assertEqual(storage.load_history(), (messages, None))

    def test_unreadable_history_is_kept_aside(self):
        storage.HISTORY_FILE.write_bytes(b"not encrypted data")
        messages, note = storage.load_history()
        self.assertEqual(messages, [])
        self.assertIsNotNone(note)
        self.assertFalse(storage.HISTORY_FILE.exists())
        self.assertEqual(len(list(self.folder.glob("history-unreadable-*.bin"))), 1)

    def test_lookup_records_are_kept(self):
        messages = [
            {"role": "user", "content": "rain?"},
            {"role": "assistant", "content": "[happy] no", "lookups": "weather for Ghent (Open-Meteo)"},
        ]
        storage.save_history(messages)
        self.assertEqual(storage.load_history(), (messages, None))

    def test_invalid_entries_are_skipped(self):
        storage.save_history([{"role": "system", "content": "x"}, {"role": "user", "content": 5}])
        self.assertEqual(storage.load_history(), ([], None))


class KeyTest(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(storage, "BRAVE_KEY_FILE", folder / "brave-key.bin"),
            mock.patch.object(storage, "CLAUDE_KEY_FILE", folder / "claude-key.bin"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def test_claude_key_round_trip(self):
        self.assertIsNone(storage.load_claude_key())
        storage.save_claude_key("sk-ant-api03-secret")
        self.assertNotIn(b"secret", storage.CLAUDE_KEY_FILE.read_bytes())
        self.assertEqual(storage.load_claude_key(), "sk-ant-api03-secret")
        storage.clear_claude_key()
        self.assertIsNone(storage.load_claude_key())

    def test_keys_are_sealed_separately(self):
        storage.save_brave_key("brave-secret")
        storage.CLAUDE_KEY_FILE.write_bytes(storage.BRAVE_KEY_FILE.read_bytes())
        self.assertIsNone(storage.load_claude_key())  # a copied file doesn't unlock
        self.assertEqual(storage.load_brave_key(), "brave-secret")


if __name__ == "__main__":
    unittest.main()
