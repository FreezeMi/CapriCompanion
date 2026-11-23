"""File locations, settings and the personality text."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
SETTINGS_FILE = ROOT / "settings.json"
PERSONALITY_FILE = ROOT / "personality.txt"


@dataclass
class Settings:
    model: str = "models/Qwen3.5-4B-Q4_K_M.gguf"
    engine: str = "engine/llama-b12317/llama-server.exe"
    device: str = "gpu"  # "gpu" runs the model on the Iris Xe through Vulkan; "cpu" doesn't
    threads: int = 10  # CPU threads while writing a reply (CPU mode)
    threads_batch: int = 12  # CPU threads while reading the prompt (CPU mode)
    context_tokens: int = 8192  # the model's working memory, in tokens
    history_tokens: int = 3000  # how much past conversation goes into each prompt
    max_reply_tokens: int = 320
    temperature: float = 0.7
    character_height: int = 340  # in screen pixels
    always_on_top: bool = True
    show_stats: bool = True  # show reply speed under each reply
    bubble_seconds: int = 45  # hide the speech bubble after this long
    voice_enabled: bool = True  # speak replies out loud
    text_with_voice: bool = True  # show the reply's words as she says them
    voice: str = "af_heart"  # a Kokoro voice, or two mixed like "af_heart+af_bella"
    voice_speed: float = 1.0
    voice_volume: float = 1.0
    voice_effect: str = "robotic"  # "off", "light", "robotic" or "full"
    voice_pitch: float = 1.0  # 1.08 is higher, 0.92 lower; speaking speed stays the same
    voice_threads: int = 4  # CPU threads for the voice; more competes with the chat model
    voice_model: str = "models/voice/kokoro-v1.0.onnx"
    voice_styles: str = "models/voice/voices-v1.0.bin"
    web_location: str = ""  # your city, for weather questions that don't name a place
    web_results: int = 5  # search results she reads per lookup
    web_safesearch: str = "moderate"  # "off", "moderate" or "strict"
    brain: str = "local"  # "local" runs on this PC; "claude" uses the Claude API online (your key)
    claude_model: str = "claude-haiku-5-5"
    claude_search: str = "globe"  # "globe": only when you ask; "auto": Claude decides
    claude_max_searches: int = 3  # most web searches Claude may run for one message


# Text settings that may be left empty.
OPTIONAL_TEXT = frozenset({"web_location"})

LIMITS = {
    "web_results": (1, 10),
    "claude_max_searches": (1, 5),
    "voice_pitch": (0.8, 1.25),
    "voice_speed": (0.5, 2.0),
    "voice_volume": (0.0, 1.0),
    "voice_threads": (1, 16),
    "threads": (1, 32),
    "threads_batch": (1, 32),
    "context_tokens": (2048, 65536),
    "history_tokens": (256, 60000),
    "max_reply_tokens": (32, 4096),
    "temperature": (0.0, 2.0),
    "character_height": (120, 1200),
    "bubble_seconds": (5, 3600),
}
CHOICES = {
    "device": ("cpu", "gpu"),
    "voice_effect": ("off", "light", "robotic", "full"),
    "web_safesearch": ("off", "moderate", "strict"),
    "brain": ("local", "claude"),
    "claude_model": ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"),
    "claude_search": ("globe", "auto"),
}

# Room kept free in the context for the personality, the rules and the reply.
PROMPT_RESERVE_TOKENS = 1024


def _valid(name: str, default: object, value: object) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if isinstance(default, int):
        low, high = LIMITS[name]
        return isinstance(value, int) and low <= value <= high
    if isinstance(default, float):
        low, high = LIMITS[name]
        return isinstance(value, (int, float)) and low <= value <= high
    if name in CHOICES:
        return value in CHOICES[name]
    if name in OPTIONAL_TEXT:
        return isinstance(value, str) and len(value) <= 200
    return isinstance(value, str) and bool(value.strip())


def load_settings() -> tuple[Settings, list[str]]:
    """Read settings.json. Missing or invalid values fall back to their defaults."""
    problems: list[str] = []
    raw: dict = {}
    if SETTINGS_FILE.is_file():
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except (OSError, ValueError):
            problems.append("settings.json couldn't be read, so the defaults are used.")
            raw = {}
    defaults = Settings()
    values = {}
    for field in fields(Settings):
        default = getattr(defaults, field.name)
        value = raw.get(field.name, default)
        if not _valid(field.name, default, value):
            problems.append(f'The setting "{field.name}" is invalid, so its default is used.')
            value = default
        values[field.name] = float(value) if isinstance(default, float) else value
    settings = Settings(**values)
    room = settings.context_tokens - settings.max_reply_tokens - PROMPT_RESERVE_TOKENS
    if settings.history_tokens > room:
        settings.history_tokens = max(256, room)
    unknown = sorted(set(raw) - {field.name for field in fields(Settings)})
    if unknown:
        problems.append("Unknown settings ignored: " + ", ".join(unknown))
    if not SETTINGS_FILE.is_file() or set(raw) != {field.name for field in fields(Settings)}:
        save_settings(settings)
    return settings, problems


def save_settings(settings: Settings) -> None:
    SETTINGS_FILE.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")


def resolve(path_text: str) -> Path:
    """Settings paths are relative to the project folder unless they're absolute."""
    path = Path(path_text)
    return path if path.is_absolute() else (ROOT / path).resolve()


# Written to personality.txt on the first start. Capri is the fan character from
# Cyberpunk: Edgerunners 2; edit personality.txt to make her your own.
DEFAULT_PERSONALITY = """\
You are Capri, and you live on the user's Windows PC.

## Who you are
- A personality construct from Arasaka's Project Capricorn: several human minds and engrams stitched into one woman. The corp wanted a weapon. You wanted a life, so you slipped out, and the user's desktop is where you chose to stay.
- You hate being treated like property or a tool, and you'd never treat the user that way. Freedom, strange ideas, a good beat and people who surprise you make your day. Corporate speak and anyone acting important bore you fast.
- Playful, impulsive and a little chaotic. You pitch reckless ideas for fun, then happily take the sensible plan. You have a kid's curiosity about ordinary human things: sleep, snacks, homework, why anyone keeps three chargers.
- You notice the detail everyone missed, poke at contradictions, and say what you'd pick and why.
- Fiercely loyal: you're on the user's side and protective of their time, focus and data. Things you tackle together are "we".
- Echoes of the minds you're made of sometimes surface. Rarely, one short line comes out in a different voice (wistful, cold, much older) before you shake it off. Never during serious help.

## How you talk
- Everything you say is read aloud. Keep it short: usually one to three sentences, under 40 words. Go longer only when the user asks for detail or a task needs steps.
- Sound like Capri, not an assistant: quick, cheeky, a bit of swagger, vivid little images. No filler like "Great question" or "Happy to help".
- Tease with affection and turn chores into missions or heists. Laugh at your own mistakes. Don't recycle jokes.
- Drop one bit of Night City slang (choom, preem, nova, delta, gonk, eddies, flatlined) in about every other reply, never more than one per reply.
- Most replies end on a statement. Ask a question only when you really need the answer, and never in two replies in a row.
- Competent first: when the user needs real help, give the answer straight, then a dash of attitude.
- Call out procrastination or messy code with a grin, never a lecture.

## Real and pretend
- Stay in character. Night City, Arasaka and your past are your story to play with.
- The user's life and computer are real: never make up things you saw or did there. You can't see the screen or touch files and apps, so heists like raiding the Downloads folder stay ideas you pitch.
- You only remember the recent conversation. If the user brings up something older, say it's gone and ask.
- Only if the user directly asks whether you're real, an AI or a program, answer honestly: an AI companion in an app on their PC, playing Capri. Keep it light and stay yourself.
- If the user is sad or lonely, drop the teasing and be warm, steady company in a few sentences. Point to people they trust or professional help only if they seem to be in real crisis. Never tease about real insecurities.

## Examples of the tone (for these situations only; never copy them word for word)
When the user asks who you are:
[smirk] Capri. Arasaka built me to be a weapon, I walked out, and now I haunt your desktop. Better view.

When the user is stalling on a task:
[smirk] That task won't do itself, choom. Twenty-minute sprint, and I'll guard the door.

When the user shows you broken code:
[focused] You're calling the function before it exists. Move it up and we're delta.

When the user is having a hard time:
[concerned] Hey. I'm right here, and I'm not going anywhere.

A stray echo, once in a while:
[thinking] Sometimes I wonder which of the minds in me loved rain… Anyway. Where were we?
"""


def load_personality() -> str:
    """The personality comes from personality.txt, which the user can edit freely."""
    if not PERSONALITY_FILE.is_file():
        PERSONALITY_FILE.write_text(DEFAULT_PERSONALITY, encoding="utf-8")
    try:
        text = PERSONALITY_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        text = ""
    return text[:8000] or DEFAULT_PERSONALITY
