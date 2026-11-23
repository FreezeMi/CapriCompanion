"""Plays sound through the Windows waveOut API, which is part of Windows.

Used instead of Python's simpler winsound module because waveOut can be stopped
instantly, plays queued sentences back to back without gaps, and reports how far
playback has got (for lip sync). Audio stays in memory; nothing is written to disk.
Use one WaveOut object from a single thread.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_WAVE_MAPPER = 0xFFFFFFFF  # the default output device
_WAVE_FORMAT_PCM = 1
_CALLBACK_NULL = 0
_WHDR_DONE = 0x1
_TIME_SAMPLES = 0x2


class AudioError(OSError):
    pass


class _WaveFormat(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WaveHeader(ctypes.Structure):
    pass


_WaveHeader._fields_ = [
    ("lpData", ctypes.c_void_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", ctypes.c_size_t),
    ("dwFlags", wintypes.DWORD),
    ("dwLoops", wintypes.DWORD),
    ("lpNext", ctypes.POINTER(_WaveHeader)),
    ("reserved", ctypes.c_size_t),
]


class _TimeValue(ctypes.Union):
    _fields_ = [
        ("ms", wintypes.DWORD),
        ("sample", wintypes.DWORD),
        ("cb", wintypes.DWORD),
        ("ticks", wintypes.DWORD),
        ("smpte", ctypes.c_ubyte * 8),
        ("midi", wintypes.DWORD),
    ]


class _Time(ctypes.Structure):
    _fields_ = [("wType", wintypes.UINT), ("u", _TimeValue)]


_winmm = ctypes.WinDLL("winmm")
_HANDLE = wintypes.HANDLE
_winmm.waveOutOpen.argtypes = [
    ctypes.POINTER(_HANDLE),
    wintypes.UINT,
    ctypes.POINTER(_WaveFormat),
    ctypes.c_size_t,
    ctypes.c_size_t,
    wintypes.DWORD,
]
for _name in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
    getattr(_winmm, _name).argtypes = [_HANDLE, ctypes.POINTER(_WaveHeader), wintypes.UINT]
_winmm.waveOutGetPosition.argtypes = [_HANDLE, ctypes.POINTER(_Time), wintypes.UINT]
_winmm.waveOutReset.argtypes = [_HANDLE]
_winmm.waveOutClose.argtypes = [_HANDLE]
for _name in (
    "waveOutOpen",
    "waveOutPrepareHeader",
    "waveOutWrite",
    "waveOutUnprepareHeader",
    "waveOutGetPosition",
    "waveOutReset",
    "waveOutClose",
):
    getattr(_winmm, _name).restype = wintypes.UINT


def _check(result: int, action: str) -> None:
    if result != 0:
        raise AudioError(f"Windows audio error {result} while trying to {action}.")


class WaveOut:
    """16-bit mono sound on the default output device."""

    def __init__(self, sample_rate: int) -> None:
        self._handle = _HANDLE()
        audio_format = _WaveFormat(_WAVE_FORMAT_PCM, 1, sample_rate, sample_rate * 2, 2, 16, 0)
        _check(
            _winmm.waveOutOpen(
                ctypes.byref(self._handle),
                _WAVE_MAPPER,
                ctypes.byref(audio_format),
                0,
                0,
                _CALLBACK_NULL,
            ),
            "open the speakers",
        )
        # Windows reads these buffers while they play, so they must stay alive until done.
        self._queued: list[tuple[_WaveHeader, ctypes.Array]] = []

    def write(self, pcm: bytes) -> None:
        """Queues sound to play right after whatever is already queued."""
        buffer = ctypes.create_string_buffer(pcm, len(pcm))
        header = _WaveHeader()
        header.lpData = ctypes.cast(buffer, ctypes.c_void_p).value
        header.dwBufferLength = len(pcm)
        size = ctypes.sizeof(header)
        _check(_winmm.waveOutPrepareHeader(self._handle, ctypes.byref(header), size), "prepare sound")
        result = _winmm.waveOutWrite(self._handle, ctypes.byref(header), size)
        if result != 0:
            _winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(header), size)
            _check(result, "play sound")
        self._queued.append((header, buffer))

    def release_finished(self) -> int:
        """Frees the buffers that finished playing, oldest first, and returns how many."""
        released = 0
        while self._queued and self._queued[0][0].dwFlags & _WHDR_DONE:
            header, _ = self._queued.pop(0)
            _winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(header), ctypes.sizeof(header))
            released += 1
        return released

    def position(self) -> int:
        """Samples played since the device was opened."""
        time_value = _Time(_TIME_SAMPLES)
        if _winmm.waveOutGetPosition(self._handle, ctypes.byref(time_value), ctypes.sizeof(time_value)):
            return 0
        return time_value.u.sample if time_value.wType == _TIME_SAMPLES else 0

    def close(self) -> None:
        """Stops at once and releases the device."""
        if not self._handle:
            return
        _winmm.waveOutReset(self._handle)  # marks every queued buffer as done
        self.release_finished()
        _winmm.waveOutClose(self._handle)
        self._handle = _HANDLE()
