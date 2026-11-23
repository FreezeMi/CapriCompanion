"""The conversation: prompt building, streamed replies and mood tags.

The model can only produce text. It has no tools, so nothing it writes is ever
executed or acted on; replies are shown as plain text.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .config import Settings
from .engine import Engine, EngineError

# Each mood has a matching character image. The hints help a small model choose.
MOOD_HINTS = {
    "happy": "cheerful",
    "excited": "thrilled",
    "laughing": "something is funny",
    "smirk": "playful or teasing",
    "flirty": "affectionate",
    "surprised": "caught off guard",
    "thinking": "working something out",
    "focused": "concentrating on a task",
    "attentive": "curious and listening",
    "concerned": "worried or caring",
    "sad": "sad or sympathetic",
    "embarrassed": "flattered or shy",
    "annoyed": "mildly irritated",
    "skeptical": "doubtful",
    "sleepy": "only when you're tired",
    "neutral": "calm",
}
MOODS = tuple(MOOD_HINTS)
# Words small models sometimes use instead of the listed moods.
SYNONYMS = {
    "joy": "happy",
    "glad": "happy",
    "cheerful": "happy",
    "playful": "smirk",
    "teasing": "smirk",
    "mischievous": "smirk",
    "laugh": "laughing",
    "amused": "laughing",
    "curious": "attentive",
    "interested": "attentive",
    "shy": "embarrassed",
    "blush": "embarrassed",
    "angry": "annoyed",
    "irritated": "annoyed",
    "worried": "concerned",
    "nervous": "concerned",
    "tired": "sleepy",
    "calm": "neutral",
    "doubtful": "skeptical",
    "shocked": "surprised",
    "pensive": "thinking",
    "upset": "sad",
    "sympathetic": "concerned",
    "caring": "concerned",
    "supportive": "concerned",
    "empathetic": "concerned",
    "grateful": "happy",
    "proud": "happy",
    "silly": "laughing",
    "confused": "skeptical",
    "relieved": "happy",
    "warm": "happy",
    "cozy": "happy",
    "welcoming": "happy",
    "encouraging": "happy",
    "comforting": "concerned",
    "gentle": "concerned",
    "apologetic": "embarrassed",
    "playful_teasing": "smirk",
    "mischief": "smirk",
}

RULES = """\
How you reply:
- Begin every reply with one mood tag in square brackets that fits how you feel about this particular message, then what you say. The mood usually changes from reply to reply. Example: [happy] Oh, that's great news!
- Moods: {moods}.
- Write only what you'd say out loud, as plain text. No markdown, headings or lists unless the user asks for them.
- Always write in English, whatever language the user writes in, because your voice can only speak English. Don't mention this; just answer in English.
- {situation}"""

# Where she runs and what she can do there (the online brain has its own, in cloud.py).
LOCAL_SITUATION = (
    "You run on the user's computer. You can't open apps, read files or see the screen, and you "
    "can't go online by yourself. When the user asks about the weather, a forecast is added to "
    "their message; when they switch on a lookup, results from the internet are. Use them and say "
    "where the information came from. If the user wants something else current without a lookup, "
    "suggest the globe button next to the text box (or Ctrl+Enter)."
)

MAX_USER_CHARACTERS = 2000

# Her voice only speaks English, but models answer in the language they're written to,
# even against a rule in the system prompt. A note on the message itself works.
ENGLISH_NOTE = (
    "[Note from the app, not from the user: answer in English only, because your voice can't "
    "speak other languages. Don't mention this note.]"
)
_WORD = re.compile(r"[a-zà-öø-ÿ']+")
_ENGLISH = frozenset(
    "i you he she it we they me my your our the a an and or but if is are was were be been am "
    "do does did have has had will would can could should what who how why when where which "
    "this that these those there here not no yes to of for with about from on at by so just "
    "hey hi hello thanks please today tonight tomorrow good bad really".split()
)
_OTHER = frozenset(  # common short words in Dutch, French, German and Spanish
    "ik je jij u het een de en van wat niet zijn ben bent dat die maar ook nog heb hebt heeft "
    "hoe waarom waar wie vandaag morgen goed dank bedankt zullen gaan kan wil moet met voor "
    "naar mijn jouw er nu echt wel geen veel ja nee hoi doen vanavond moe "
    "le la les un une des et est je tu il elle nous vous ils pas que qui quoi pourquoi "
    "bonjour merci oui avec pour dans sur mon ton très suis sommes ça va comment aujourd'hui "
    "ich du er sie es wir ihr der die das und ist nicht ein eine was wie warum heute danke "
    "ja nein mit für auf mein dein sehr "
    "el los las y es yo tú usted qué por porque hoy gracias sí con para mi muy".split()
) - _ENGLISH


def looks_foreign(text: str) -> bool:
    """True when a message is clearly not in English (by its common little words)."""
    words = _WORD.findall(text.lower())
    other = sum(word in _OTHER for word in words)
    return len(words) >= 2 and other >= 2 and other > sum(word in _ENGLISH for word in words)


def system_prompt(personality: str, situation: str = LOCAL_SITUATION) -> str:
    moods = ", ".join(f"[{mood}] {hint}" for mood, hint in MOOD_HINTS.items())
    return personality.strip() + "\n\n" + RULES.format(moods=moods, situation=situation)


def estimate_tokens(text: str) -> int:
    """A rough, deliberately high estimate (about 3 characters per token)."""
    return len(text) // 3 + 4


class Conversation:
    """Messages plus the point where the model's view of them starts.

    When the history gets too long, old turns are dropped in one large step rather
    than one by one. That keeps the start of the prompt identical for many turns,
    so the engine can reuse the work it already did instead of re-reading it all.
    """

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages: list[dict] = list(messages or [])
        self.start = 0

    def add(self, role: str, content: str, lookups: str = "") -> None:
        """`lookups` says what a reply looked up, like "weather for Ghent (Open-Meteo)"."""
        message = {"role": role, "content": content}
        if lookups:
            message["lookups"] = lookups
        self.messages.append(message)

    def clear(self) -> None:
        self.messages.clear()
        self.start = 0

    def prompt(
        self, personality: str, budget: int, situation: str = LOCAL_SITUATION, lookups: bool = False
    ) -> list[dict]:
        """The system prompt plus the recent turns. With `lookups`, replies keep their
        record of what they looked up (the online brain uses it; see cloud.split_prompt)."""
        self.start = min(self.start, len(self.messages))
        visible = self.messages[self.start :]
        total = sum(estimate_tokens(m["content"]) for m in visible)
        if total > budget:
            target = budget * 0.6
            while self.start < len(self.messages) - 1 and total > target:
                total -= estimate_tokens(self.messages[self.start]["content"])
                self.start += 1
        # The model's view must begin with something the user said.
        while self.start < len(self.messages) - 1 and self.messages[self.start]["role"] != "user":
            self.start += 1
        keep = ("role", "content", "lookups") if lookups else ("role", "content")
        turns = [{k: m[k] for k in keep if k in m} for m in self.messages[self.start :]]
        for turn in turns:  # the same text every time, so the prompt cache still matches
            if turn["role"] == "user" and looks_foreign(turn["content"]):
                turn["content"] += "\n\n" + ENGLISH_NOTE
        return [{"role": "system", "content": system_prompt(personality, situation)}, *turns]


_TAG = re.compile(r"\s*[*_\"']*\[([A-Za-z][A-Za-z _-]{0,23})\][*_\"']*[ \t]*\n?")
_ANY_TAG = re.compile(r"\[(?:" + "|".join(MOODS) + r")\]\s?", re.IGNORECASE)
# Stage directions a small model sometimes adds mid-reply, like "[glares]". A bracket
# right after a name or another bracket is code (items[index]) and is left alone.
_STAGE = re.compile(r"(?<![\w\])])\[[a-z][a-z _-]{1,23}\]\s?")


def mood_name(word: str) -> str | None:
    key = word.strip().lower().replace(" ", "_").replace("-", "_")
    if key in MOODS:
        return key
    return SYNONYMS.get(key)


class MoodFilter:
    """Takes the leading [mood] tag out of a streamed reply."""

    def __init__(self) -> None:
        self.mood: str | None = None
        self._buffer = ""
        self._done = False
        self._trim = False  # drop the space that follows a tag, even if it arrives later

    def feed(self, chunk: str) -> str:
        """Returns the text that can be shown so far."""
        if self._done:
            if self._trim:
                chunk = chunk.lstrip()
                self._trim = not chunk
            return chunk
        self._buffer += chunk
        head = self._buffer.lstrip()
        if not head:
            return ""
        if head[0] not in "[*_\"'":
            return self._release()
        if "]" not in head:
            return "" if len(head) < 40 else self._release()
        match = _TAG.match(self._buffer)
        if not match:
            return self._release()
        self.mood = mood_name(match.group(1))
        self._done = True
        rest = self._buffer[match.end() :].lstrip()
        self._trim = not rest
        self._buffer = ""
        return rest

    def flush(self) -> str:
        if self._done:
            return ""
        if self._buffer.lstrip().startswith("["):  # a tag that was cut off, like "[happ"
            self._done, self._buffer = True, ""
            return ""
        return self._release()

    def _release(self) -> str:
        self._done = True
        text, self._buffer = self._buffer, ""
        return text


def visible_text(raw: str, finished: bool = True) -> str:
    """What the user sees: the reply without any mood tags. While a reply is still
    coming in (`finished=False`), a tag that has only partly arrived stays hidden."""
    mood_filter = MoodFilter()
    text = mood_filter.feed(raw)
    if finished or not raw.lstrip().startswith("["):
        text += mood_filter.flush()
    return _STAGE.sub("", _ANY_TAG.sub("", text)).strip()


def leading_mood(raw: str) -> str | None:
    mood_filter = MoodFilter()
    mood_filter.feed(raw)
    mood_filter.flush()
    return mood_filter.mood


@dataclass
class ReplyStats:
    first_token_seconds: float = 0.0
    total_seconds: float = 0.0
    prompt_tokens: int = 0  # tokens the engine had to read for this reply
    cached_tokens: int = 0  # tokens it could reuse from the previous reply
    reply_tokens: int = 0
    tokens_per_second: float = 0.0
    extra: dict = field(default_factory=dict)


def request_body(messages: list[dict], settings: Settings, max_tokens: int) -> dict:
    return {
        "model": "companion",
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": settings.temperature,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def stream_reply(
    engine: Engine,
    messages: list[dict],
    settings: Settings,
    stats: ReplyStats,
    should_stop: Callable[[], bool] = lambda: False,
    max_tokens: int | None = None,
) -> Iterator[str]:
    """Yields the reply text in pieces as the model writes it."""
    started = time.monotonic()
    body = request_body(
        messages, settings, settings.max_reply_tokens if max_tokens is None else max_tokens
    )
    conn = engine.connection(timeout=600)
    try:
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(body).encode("utf-8"),
            headers={**engine.headers(), "Accept": "text/event-stream"},
        )
        response = conn.getresponse()
        _check_status(response)
        for event in _events(response, should_stop):
            if isinstance(event.get("timings"), dict):
                _record_timings(stats, event["timings"])
            for text in _texts(event):
                if not stats.first_token_seconds:
                    stats.first_token_seconds = time.monotonic() - started
                yield text
    finally:
        conn.close()
        stats.total_seconds = time.monotonic() - started


def _check_status(response) -> None:
    if response.status == 200:
        return
    detail = response.read(4000).decode("utf-8", "replace")
    try:
        detail = json.loads(detail)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        pass
    raise EngineError(f"The AI engine refused the request ({response.status}): {detail}")


def _events(response, should_stop: Callable[[], bool]) -> Iterator[dict]:
    """Reads the server-sent event stream as JSON objects."""
    for raw_line in response:
        if should_stop():
            return
        line = raw_line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            return
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def _texts(event: dict) -> Iterator[str]:
    for choice in event.get("choices") or []:
        text = (choice.get("delta") or {}).get("content")
        if text:
            yield text


def _record_timings(stats: ReplyStats, timings: dict) -> None:
    stats.prompt_tokens = int(timings.get("prompt_n", 0) or 0)
    stats.cached_tokens = int(timings.get("cache_n", 0) or 0)
    stats.reply_tokens = int(timings.get("predicted_n", 0) or 0)
    stats.tokens_per_second = float(timings.get("predicted_per_second", 0) or 0)
