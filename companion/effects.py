"""Voice effects that make Capri sound synthetic and robotic.

Classic sound-design techniques, applied to each spoken sentence with numpy. They
work on mono float32 audio at 24 kHz, keep its length (except shift_pitch), and
take a few milliseconds per sentence. The speaker evens out the loudness afterwards.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 24000

EFFECTS = {  # setting value -> menu label
    "off": "No effect",
    "light": "Light robotic",
    "robotic": "Robotic",
    "full": "Full robot",
}
PITCHES = {  # menu label -> pitch factor
    "Lower": 0.92,
    "Normal": 1.0,
    "Higher": 1.08,
    "Much higher": 1.15,
}


def _mix(dry: np.ndarray, wet: np.ndarray, amount: float) -> np.ndarray:
    return ((1.0 - amount) * dry + amount * wet).astype(np.float32)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def _convolve(x: np.ndarray, response: np.ndarray) -> np.ndarray:
    size = 1 << (len(x) + len(response) - 2).bit_length()
    spectrum = np.fft.rfft(x, size) * np.fft.rfft(response, size)
    return np.fft.irfft(spectrum, size)[: len(x)].astype(np.float32)


def metallic(x: np.ndarray, delay_ms: float = 5.0, feedback: float = 0.6, amount: float = 0.4):
    """A short ringing echo (a comb filter): a hollow, metallic resonance."""
    delay = max(1, round(SAMPLE_RATE * delay_ms / 1000))
    echoes = max(1, int(np.ceil(np.log(0.01) / np.log(feedback))))
    response = np.zeros(delay * echoes + 1, dtype=np.float32)
    response[::delay] = feedback ** np.arange(echoes + 1, dtype=np.float32)
    response *= 1.0 - feedback  # keeps the overall loudness about the same
    return _mix(x, _convolve(x, response), amount)


def ring(x: np.ndarray, frequency: float = 50.0, amount: float = 0.3):
    """Ring modulation: the classic science-fiction robot buzz."""
    time = np.arange(len(x), dtype=np.float32) / SAMPLE_RATE
    return _mix(x, x * np.sin(2 * np.pi * frequency * time), amount)


def chorus(x: np.ndarray, delay_ms: float = 16.0, depth_ms: float = 2.5, rate_hz: float = 0.7, amount: float = 0.3):
    """A slightly delayed, wavering copy, like a second synthetic voice underneath."""
    index = np.arange(len(x), dtype=np.float32)
    delay = (delay_ms + depth_ms * np.sin(2 * np.pi * rate_hz * index / SAMPLE_RATE)) * SAMPLE_RATE / 1000
    return _mix(x, np.interp(index - delay, index, x, left=0.0, right=0.0), amount)


def crush(x: np.ndarray, bits: int = 12, hold: int = 1):
    """Fewer, coarser sample steps: a slightly digital, lo-fi grain."""
    if hold > 1:
        x = np.repeat(x[::hold], hold)[: len(x)]
    steps = 2 ** (bits - 1)
    return (np.round(x * steps) / steps).astype(np.float32)


def _smooth(magnitude: np.ndarray, width: int) -> np.ndarray:
    """Moving average across frequency bins (width must be odd)."""
    half = width // 2
    padded = np.pad(magnitude, ((0, 0), (half + 1, half)), mode="edge")
    total = np.cumsum(padded, axis=1)
    return (total[:, width:] - total[:, :-width]) / width


def vocoder(x: np.ndarray, pitch_hz: float = 190.0, amount: float = 0.5, noise: float = 0.25):
    """Says the words on one synthetic note: the monotone machine voice.

    The shape of the speech spectrum is laid over a buzzing carrier tone, with a
    little noise mixed into the carrier so s, f and t sounds stay clear.
    """
    size, hop, width = 512, 128, 5
    if len(x) < size:
        return x
    window = np.hanning(size).astype(np.float32)
    padded = np.pad(x, (size, size))
    frames = 1 + (len(padded) - size) // hop
    starts = hop * np.arange(frames)
    index = starts[:, None] + np.arange(size)[None, :]
    speech = np.fft.rfft(padded[index] * window, axis=1)
    time = np.arange(len(padded), dtype=np.float32) / SAMPLE_RATE
    buzz = 2.0 * ((time * pitch_hz) % 1.0) - 1.0
    hiss = np.random.default_rng(7).standard_normal(len(padded)).astype(np.float32)
    carrier = np.fft.rfft((buzz + noise * hiss)[index] * window, axis=1)
    shaped = carrier / (_smooth(np.abs(carrier), width) + 1e-6) * _smooth(np.abs(speech), width)
    pieces = np.fft.irfft(shaped, size, axis=1).astype(np.float32) * window
    output = np.zeros(len(padded), dtype=np.float32)
    weight = np.zeros(len(padded), dtype=np.float32)
    for start, piece in zip(starts, pieces):
        output[start : start + size] += piece
        weight[start : start + size] += window * window
    robot = (output / np.maximum(weight, 1e-3))[size : size + len(x)]
    robot *= _rms(x) / (_rms(robot) + 1e-9)
    return _mix(x, robot, amount)


def shift_pitch(x: np.ndarray, factor: float) -> np.ndarray:
    """Plays the sound `factor` times faster: higher pitch, shorter sound.

    The speaker asks Kokoro for proportionally slower speech first, so the
    speaking speed ends up unchanged and only the pitch moves.
    """
    if abs(factor - 1.0) < 1e-3 or len(x) < 2:
        return x
    positions = np.arange(int(len(x) / factor), dtype=np.float64) * factor
    return np.interp(positions, np.arange(len(x)), x).astype(np.float32)


def apply(x: np.ndarray, effect: str) -> np.ndarray:
    """One of the EFFECTS presets."""
    x = np.asarray(x, dtype=np.float32)
    if effect == "light":
        x = chorus(x, amount=0.25)
        x = metallic(x, delay_ms=4.2, feedback=0.45, amount=0.3)
    elif effect == "robotic":
        x = vocoder(x, pitch_hz=200.0, amount=0.35)
        x = ring(x, frequency=50.0, amount=0.25)
        x = metallic(x, delay_ms=5.0, feedback=0.6, amount=0.35)
        x = crush(x, bits=12)
    elif effect == "full":
        x = vocoder(x, pitch_hz=185.0, amount=0.85, noise=0.3)
        x = ring(x, frequency=60.0, amount=0.4)
        x = metallic(x, delay_ms=6.0, feedback=0.7, amount=0.4)
        x = crush(x, bits=9, hold=2)
    return x
