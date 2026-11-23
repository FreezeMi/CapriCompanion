"""Saved state: the conversation (encrypted) and the window position.

The conversation is encrypted with Windows DPAPI, which ties it to your Windows
account: other accounts, or someone who copies the file to another PC, can't read
it. Programs running under your own account still could, as with any of your files.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes
from pathlib import Path

from .config import DATA

HISTORY_FILE = DATA / "history.bin"
WINDOW_FILE = DATA / "window.json"
BRAVE_KEY_FILE = DATA / "brave-key.bin"
CLAUDE_KEY_FILE = DATA / "claude-key.bin"
MAX_STORED_MESSAGES = 400
_ENTROPY = b"desktop-companion/history/v1"
_KEY_ENTROPY = b"desktop-companion/brave-key/v1"
_CLAUDE_KEY_ENTROPY = b"desktop-companion/claude-key/v1"
_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
for _name in ("CryptProtectData", "CryptUnprotectData"):
    _function = getattr(_crypt32, _name)
    _function.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    _function.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _call(function, data: bytes, extra: bytes = _ENTROPY) -> bytes:
    source_buffer = ctypes.create_string_buffer(data, len(data))
    entropy_buffer = ctypes.create_string_buffer(extra, len(extra))
    source = _Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_char)))
    entropy = _Blob(len(extra), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    if not function(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, _UI_FORBIDDEN, ctypes.byref(result)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def protect(data: bytes, extra: bytes = _ENTROPY) -> bytes:
    return _call(_crypt32.CryptProtectData, data, extra)


def unprotect(data: bytes, extra: bytes = _ENTROPY) -> bytes:
    return _call(_crypt32.CryptUnprotectData, data, extra)


def _save_secret(path: Path, extra: bytes, key: str) -> None:
    """An API key, encrypted for this Windows account like the history."""
    _write_atomically(path, protect(key.encode("utf-8"), extra))


def _load_secret(path: Path, extra: bytes) -> str | None:
    try:
        return unprotect(path.read_bytes(), extra).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def save_brave_key(key: str) -> None:
    _save_secret(BRAVE_KEY_FILE, _KEY_ENTROPY, key)


def load_brave_key() -> str | None:
    return _load_secret(BRAVE_KEY_FILE, _KEY_ENTROPY)


def clear_brave_key() -> None:
    BRAVE_KEY_FILE.unlink(missing_ok=True)


def save_claude_key(key: str) -> None:
    _save_secret(CLAUDE_KEY_FILE, _CLAUDE_KEY_ENTROPY, key)


def load_claude_key() -> str | None:
    return _load_secret(CLAUDE_KEY_FILE, _CLAUDE_KEY_ENTROPY)


def clear_claude_key() -> None:
    CLAUDE_KEY_FILE.unlink(missing_ok=True)


def _write_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def load_history() -> tuple[list[dict], str | None]:
    """Returns the saved messages, plus a note for the user if they couldn't be read."""
    if not HISTORY_FILE.is_file():
        return [], None
    try:
        value = json.loads(unprotect(HISTORY_FILE.read_bytes()).decode("utf-8"))
        messages = [
            {"role": m["role"], "content": m["content"]}
            | ({"lookups": m["lookups"][:500]} if isinstance(m.get("lookups"), str) and m["lookups"] else {})
            for m in value.get("messages", [])
            if isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]
        return messages[-MAX_STORED_MESSAGES:], None
    except (OSError, ValueError, AttributeError, TypeError):
        # Keep the unreadable file instead of overwriting it with a new conversation.
        kept = HISTORY_FILE.with_name(f"history-unreadable-{time.strftime('%Y%m%d-%H%M%S')}.bin")
        try:
            os.replace(HISTORY_FILE, kept)
        except OSError:
            pass
        return [], "I couldn't open our saved conversation, so this is a fresh start."


def save_history(messages: list[dict]) -> None:
    payload = {"version": 1, "messages": messages[-MAX_STORED_MESSAGES:]}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _write_atomically(HISTORY_FILE, protect(data))


def clear_history() -> None:
    HISTORY_FILE.unlink(missing_ok=True)


def load_window_position() -> tuple[int, int] | None:
    try:
        value = json.loads(WINDOW_FILE.read_text(encoding="utf-8"))
        x, y = value["x"], value["y"]
        if isinstance(x, int) and isinstance(y, int):
            return x, y
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def save_window_position(x: int, y: int) -> None:
    _write_atomically(WINDOW_FILE, json.dumps({"x": x, "y": y}).encode("utf-8"))
