"""Capri's voice: Kokoro text-to-speech on the CPU, played through Windows.

Everything runs on this PC and stays in memory. Replies are spoken one sentence
at a time while the rest is still being written, so she starts talking early.

Two background threads do the work: one turns text into sound, the other plays
it. Signals reach the window on the GUI thread.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from typing import NamedTuple

from PySide6.QtCore import QObject, Signal

from . import effects
from .audio import AudioError, WaveOut
from .config import Settings, resolve

SAMPLE_RATE = 24000
FRAME_SECONDS = 0.05  # lip-sync resolution
FRAME = int(SAMPLE_RATE * FRAME_SECONDS)
PAUSE_SECONDS = 0.18  # silence after each sentence
TARGET_PEAK = 0.85  # sentences are evened out to this loudness before the volume setting

VOICES = {  # menu label -> Kokoro voice, or two voices mixed with "+"
    "Heart": "af_heart",
    "Bella": "af_bella",
    "Nicole (soft)": "af_nicole",
    "Aoede": "af_aoede",
    "Kore": "af_kore",
    "Sarah": "af_sarah",
    "Emma (British)": "bf_emma",
    "Heart and Bella mixed": "af_heart+af_bella",
}
DEFAULT_VOICE = "af_heart"
PREVIEW = "Hi, it's me, Capri! Do you like how I sound?"

# Text cleanup -----------------------------------------------------------------

_BOLD = re.compile(r"\*\*([^*\n]{1,200})\*\*")  # **word** is emphasis: read the word
_ACTION = re.compile(r"\*[^*\n]{1,80}\*")  # *giggles* is shown, not read out
_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿️‍]")
_MARKUP = re.compile(r"[#*_`~<>|\\^]")
_SPACE = re.compile(r"\s+")


def speakable(text: str) -> str:
    """The part of the text that should be said out loud."""
    text = _BOLD.sub(r"\1", text)
    text = _ACTION.sub(" ", text)
    text = _URL.sub(" a link ", text)
    text = _EMOJI.sub(" ", text)
    text = _MARKUP.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*\s+|\n\s*")
_CLAUSE_END = re.compile(r"[,;:—–]\s+")


class Piece(NamedTuple):
    text: str  # what she says
    start: int  # the part of the reply text it belongs to: reply[start:end]
    end: int


class SpeechChunker:
    """Cuts a reply that is still being written into pieces to speak.

    The first piece ends at the first sentence end, or at a comma once there are a
    few words, so she starts talking early. Later pieces are whole sentences, with
    very short ones merged into the next. Each piece remembers its part of the
    reply text; text that isn't read out (emoji, *actions*) joins the next piece.
    """

    MIN_FIRST_WORDS = 3
    MIN_CHARACTERS = 24

    def __init__(self) -> None:
        self.position = 0
        self.count = 0
        self.spoken_until = 0

    def feed(self, text: str) -> list[Piece]:
        """Takes the whole reply so far; returns pieces that are now complete."""
        return self._take(text, final=False)

    def finish(self, text: str) -> list[Piece]:
        """Takes the finished reply; returns everything that's left."""
        return self._take(text, final=True)

    def _take(self, text: str, final: bool) -> list[Piece]:
        self.position = min(self.position, len(text))
        self.spoken_until = min(self.spoken_until, self.position)
        pieces: list[Piece] = []
        while (end := self._boundary(text[self.position :])) is not None:
            self.position += end
            self._add(pieces, text)
        if final:
            self.position = len(text)
            self._add(pieces, text)
        return pieces

    def _add(self, pieces: list[Piece], text: str) -> None:
        spoken = speakable(text[self.spoken_until : self.position])
        if spoken:
            pieces.append(Piece(spoken, self.spoken_until, self.position))
            self.spoken_until = self.position
            self.count += 1

    def _boundary(self, pending: str) -> int | None:
        ends = [match.end() for match in _SENTENCE_END.finditer(pending)]
        if self.count == 0:
            for match in _CLAUSE_END.finditer(pending):
                if len(pending[: match.start()].split()) >= self.MIN_FIRST_WORDS:
                    ends.append(match.end())
                    break
            return min(ends) if ends else None
        return next((end for end in ends if len(pending[:end].strip()) >= self.MIN_CHARACTERS), None)


# Sound helpers ---------------------------------------------------------------


def envelope(samples) -> list[float]:
    """Loudness per lip-sync frame, from 0 to 1."""
    import numpy as np

    count = len(samples) // FRAME
    if count == 0:
        return [0.0]
    frames = np.asarray(samples[: count * FRAME], dtype=np.float32).reshape(count, FRAME)
    loudness = np.sqrt(np.mean(np.square(frames), axis=1))
    loudest = float(loudness.max())
    if loudest > 0:
        loudness = loudness / loudest
    return [round(float(value), 3) for value in loudness]


class Clip(NamedTuple):
    """One spoken piece, queued on the speakers."""

    start: int  # in samples since the speakers were opened
    end: int
    loudness: list[float]
    generation: int
    tag: object  # what the window asked to be told about, e.g. the piece's text span
    spoken: int  # samples of speech before the pause at the end


def level_at(timeline: list[Clip], position: int) -> float:
    """How loud the sound is at a playback position, for the mouth."""
    for clip in timeline:
        if clip.start <= position < clip.end:
            index = (position - clip.start) // FRAME
            return clip.loudness[index] if index < len(clip.loudness) else 0.0
    return 0.0


def reveal_position(text: str, start: int, end: int, fraction: float) -> int:
    """How much of the reply to show while text[start:end] is being spoken.

    Shows up to the end of the word she's saying, never half a word.
    """
    if fraction >= 1.0:
        return end
    target = start + int((end - start) * max(0.0, fraction))
    if target <= start:
        return start
    space = text.find(" ", target, end)
    return end if space == -1 else space


def _drain(items: queue.Queue) -> None:
    while True:
        try:
            items.get_nowait()
        except queue.Empty:
            return


def _load_kokoro(settings: Settings):
    import onnxruntime
    from kokoro_onnx import Kokoro

    model, styles = resolve(settings.voice_model), resolve(settings.voice_styles)
    if not model.is_file() or not styles.is_file():
        raise FileNotFoundError("the voice files aren't downloaded. Run Install.cmd")
    onnxruntime.disable_telemetry_events()
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = settings.voice_threads
    options.inter_op_num_threads = 1
    session = onnxruntime.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
    return Kokoro.from_session(session, str(styles))


class Speaker(QObject):
    speaking = Signal(int)  # generation: sound started
    idle = Signal(int)  # generation: everything queued has been said
    problem = Signal(str)  # the voice failed; the window turns it off
    loaded = Signal(float)  # seconds the voice took to load

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.voice = settings.voice
        self.level = 0.0  # how open her mouth should be right now, from 0 to 1
        self.progress = None  # (generation, tag, fraction) of the piece being spoken
        self.generation = 0
        self._outstanding = 0  # pieces of the current generation not yet fully spoken
        self._lock = threading.Lock()
        self._texts: queue.Queue = queue.Queue()
        self._sounds: queue.Queue = queue.Queue()
        self._hush = threading.Event()
        self._styles: dict[str, object] = {}
        self._threads = [
            threading.Thread(target=self._synthesize_loop, name="voice-synthesis", daemon=True),
            threading.Thread(target=self._play_loop, name="voice-playback", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    # Called from the GUI thread ------------------------------------------------

    def preload(self) -> None:
        self._texts.put(("load", 0, "", None))

    def say(self, text: str, tag: object = None) -> None:
        """Queues text to speak; `tag` comes back in `progress` while it plays."""
        if not text.strip():
            return
        with self._lock:
            self._outstanding += 1
            generation = self.generation
        self._texts.put(("say", generation, text, tag))

    def stop(self) -> int:
        """Goes quiet at once and forgets anything not yet said. Returns the new generation."""
        with self._lock:
            self.generation += 1
            self._outstanding = 0
            generation = self.generation
        _drain(self._texts)
        _drain(self._sounds)
        self._hush.set()
        self.level = 0.0
        self.progress = None
        return generation

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._outstanding > 0

    def close(self) -> None:
        self.stop()
        self._texts.put(None)
        self._sounds.put(None)
        for thread in self._threads:
            thread.join(timeout=5)

    # Background threads --------------------------------------------------------

    def _finished_piece(self, generation: int) -> None:
        with self._lock:
            if generation != self.generation or self._outstanding == 0:
                return
            self._outstanding -= 1
            done = self._outstanding == 0
        if done:
            self.idle.emit(generation)

    def _style(self, kokoro, name: str):
        """A voice name, or the average of several voices joined with '+'."""
        if name not in self._styles:
            parts = [part.strip() for part in name.split("+") if part.strip()]
            known = set(kokoro.get_voices())
            if not parts or any(part not in known for part in parts):
                return self._style(kokoro, DEFAULT_VOICE) if name != DEFAULT_VOICE else DEFAULT_VOICE
            if len(parts) == 1:
                self._styles[name] = parts[0]
            else:
                import numpy as np

                self._styles[name] = np.mean([kokoro.get_voice_style(p) for p in parts], axis=0)
        return self._styles[name]

    def _render(self, kokoro, text: str) -> tuple[bytes, list[float], int, int]:
        """Sound for one piece: (PCM bytes, loudness, total samples, speech samples)."""
        import numpy as np

        name = self.voice
        language = "en-gb" if name.strip().startswith("b") else "en-us"
        pitch = self.settings.voice_pitch
        # Slower speech, then played faster: higher pitch at the chosen speaking speed.
        speed = min(2.0, max(0.5, self.settings.voice_speed / pitch))
        samples, rate = kokoro.create(text, voice=self._style(kokoro, name), speed=speed, lang=language)
        if rate != SAMPLE_RATE:
            raise ValueError(f"unexpected sample rate {rate}")
        samples = effects.shift_pitch(np.asarray(samples, dtype=np.float32), pitch)
        samples = effects.apply(samples, self.settings.voice_effect)
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        if peak > 0:
            samples = samples * min(4.0, TARGET_PEAK / peak)
        speech = len(samples)
        pause = np.zeros(int(SAMPLE_RATE * PAUSE_SECONDS), dtype=np.float32)
        samples = np.concatenate([samples, pause])
        loudness = envelope(samples)  # before the volume, so her mouth moves at any volume
        samples = samples * self.settings.voice_volume
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        return pcm, loudness, len(samples), speech

    def _synthesize_loop(self) -> None:
        kokoro = None
        while (item := self._texts.get()) is not None:
            kind, generation, text, tag = item
            if kind == "say" and generation != self.generation:
                continue  # stopped before it was spoken
            try:
                if kokoro is None:
                    started = time.monotonic()
                    kokoro = _load_kokoro(self.settings)
                    kokoro.create("Hi.", voice=DEFAULT_VOICE, lang="en-us")  # warm-up
                    self.loaded.emit(time.monotonic() - started)
                if kind == "load":
                    continue
                sound = self._render(kokoro, text)
            except Exception as error:  # every failure must reach the window
                self._finished_piece(generation)
                self.problem.emit(f"My voice isn't working: {error}")
                continue
            if generation == self.generation:
                self._sounds.put((generation, *sound, tag))

    def _play_loop(self) -> None:
        playback = _Playback(self)
        while True:
            if self._hush.is_set():
                self._hush.clear()
                playback.hush()
            try:
                # Poll often only while sound plays; otherwise just wait for work.
                item = self._sounds.get(timeout=0.02 if playback.device is not None else 0.5)
            except queue.Empty:
                item = False
            if item is None:
                break
            if item and item[0] == self.generation:
                playback.play(*item)
            playback.update()
        playback.hush()


class _Playback:
    """What the playback thread has queued on the speakers."""

    def __init__(self, speaker: Speaker) -> None:
        self.speaker = speaker
        self.device: WaveOut | None = None
        self.timeline: list[Clip] = []
        self.written = 0  # samples queued since the device was opened

    def hush(self) -> None:
        if self.device is not None:
            self.device.close()
            self.device = None
        self.timeline.clear()
        self.speaker.level = 0.0
        self.speaker.progress = None

    def play(
        self,
        generation: int,
        pcm: bytes,
        loudness: list[float],
        count: int,
        spoken: int,
        tag: object,
    ) -> None:
        try:
            if self.device is None:
                self.device, self.written = WaveOut(SAMPLE_RATE), 0
            self.device.write(pcm)
        except AudioError as error:
            self.speaker._finished_piece(generation)
            self.speaker.problem.emit(f"I can't use the speakers: {error}")
            return
        if not self.timeline:
            self.speaker.speaking.emit(generation)
        clip = Clip(self.written, self.written + count, loudness, generation, tag, spoken)
        self.timeline.append(clip)
        self.written += count

    def update(self) -> None:
        if self.device is None:
            return
        position = self.device.position()
        for _ in range(self.device.release_finished()):
            clip = self.timeline.pop(0)
            # Report the finished piece as complete before announcing the end.
            self.speaker.progress = (clip.generation, clip.tag, 1.0)
            self.speaker._finished_piece(clip.generation)
        if self.timeline and position >= self.timeline[0].start:
            clip = self.timeline[0]
            fraction = min(1.0, (position - clip.start) / max(1, clip.spoken))
            self.speaker.progress = (clip.generation, clip.tag, fraction)
        self.speaker.level = level_at(self.timeline, position)
        if not self.timeline and not self.speaker.busy:
            self.hush()  # nothing left to say: release the speakers
