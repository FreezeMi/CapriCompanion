"""The optional online brain: Claude, through the official Anthropic SDK.

It's only used when you switch her brain to Claude (right-click → Brain). Then each
message goes to Anthropic's API (api.anthropic.com, over HTTPS) together with her
personality and the recent conversation. Claude has two tools: Anthropic's own web
search, which runs on Anthropic's servers, and a weather tool that this app runs by
sending only a place name to Open-Meteo. As with the local model, replies are shown
as plain text and never acted on.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterator

from . import web
from .chat import ReplyStats

# Fixed, so an ANTHROPIC_BASE_URL environment variable can't send the key anywhere else.
API_ADDRESS = "https://api.anthropic.com"


@dataclass(frozen=True)
class Model:
    label: str
    input: float  # dollars per million tokens (prompts up to 100K tokens)
    output: float
    cache_read: float
    cache_write: float  # 5-minute cache
    thinking: dict | None  # how to skip thinking for a quick reply; None leaves it to the model


# Prices checked on 2026-11-23: https://platform.claude.com/docs/en/about-claude/pricing
MODELS = {
    "claude-haiku-5-5": Model("Claude Haiku 5.5", 0.10, 0.50, 0.01, 0.125, {"type": "disabled"}),
    "claude-sonnet-5-5": Model("Claude Sonnet 5.5", 2.00, 10.00, 0.10, 2.50, {"type": "between_tools"}),
    # Opus 5.5 always thinks; the low effort below keeps that short.
    "claude-opus-5-5": Model("Claude Opus 5.5", 4.00, 20.00, 0.20, 5.00, None),
}
SEARCH_PRICE = 0.01  # dollars per web search ($10 per 1,000)
EFFORT = "low"  # quick, short replies
MAX_TOKENS = 2048  # room for a short reply, plus Opus's thinking
MAX_ROUNDS = 5  # requests per reply: a weather check or a paused search each takes one more
MAX_SOURCES = 4

WEATHER = (
    "You can check the weather at any time with your get_weather tool, without a lookup: use it "
    "for any question about the weather, temperature, rain or what to wear outside."
)
WHEN_ASKED = (
    f"You can't open apps, read files or see the screen. {WEATHER} You can search the web only "
    "when the user switches on a lookup for a message (the globe button next to the text box, or "
    "Ctrl+Enter); their message then says so. Say where information came from. Even after "
    "searching, answer in a few spoken sentences without links. If the user wants other current "
    "information without a lookup, suggest the globe button."
)
ANY_TIME = (
    f"You can't open apps, read files or see the screen, but you can search the web. {WEATHER} "
    "Search when a question needs current or specific facts you aren't sure of, not for small "
    "talk, and say where information came from. Even after searching, answer in a few spoken "
    "sentences without links."
)
SEARCH_NOTE = (
    "[Note from the app, not from the user: search the web before you answer this, then answer "
    "in two or three spoken sentences in your own words.]"
)
# The conversation keeps only the text of earlier replies, not the lookups behind them.
EARLIER = (
    "Forecasts and search results in your earlier replies came from real lookups that aren't "
    "shown again here, so stand by them; don't doubt them or apologize for them unless the user "
    "says something was wrong."
)

WEATHER_TOOL = {
    "name": "get_weather",
    "description": (
        "Current weather and a three-day forecast from Open-Meteo. Use it for any question about "
        "the weather, temperature, rain, snow or what to wear outside. Leave place empty for the "
        "user's home town."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "place": {
                "type": "string",
                "description": 'A city or town, like "Ghent" or "New York". Empty for the home town.',
            }
        },
    },
    "eager_input_streaming": True,
}


class CloudError(Exception):
    """A problem with the online brain, worded for the user."""

    def __init__(self, message: str, *, local_can_answer: bool = True) -> None:
        super().__init__(message)
        self.local_can_answer = local_can_answer


@dataclass
class Request:
    model: str
    search: bool  # offer Claude the web search tool for this reply
    max_searches: int
    home: str = ""  # the user's town, for weather questions that don't name a place


def situation(search: str, today: date | None = None) -> str:
    """The last line of her rules when Claude is her brain."""
    today = today or date.today()
    rules = ANY_TIME if search == "auto" else WHEN_ASKED
    return f"{rules} {EARLIER} Today is {today:%A}, {today.day} {today:%B %Y}."


def split_prompt(prompt: list[dict]) -> tuple[str, list[dict]]:
    """The chat prompt in the API's shape: the system text apart, then turns that
    alternate, start with the user and end with the user's new message.

    Only the text of earlier replies is kept, not the lookups behind them, so Claude
    could decide it made those facts up. A reply's record of its lookups is therefore
    put in front of the user's next message. It sits at the same place in every later
    request, so Anthropic's cache still covers it.
    """
    system = "\n\n".join(m["content"] for m in prompt if m["role"] == "system")
    turns: list[dict] = []
    for role, text in _noted(prompt):
        if role == "assistant" and not turns:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1] = {"role": role, "content": turns[-1]["content"] + "\n\n" + text}
        else:
            turns.append({"role": role, "content": text})
    while turns and turns[-1]["role"] != "user":
        turns.pop()
    return system, turns


def _noted(prompt: list[dict]) -> Iterator[tuple[str, str]]:
    """(role, text) for each turn, with lookup notes put in front of the user's next message."""
    note = ""
    for message in prompt:
        role = message["role"]
        if role not in ("user", "assistant"):
            continue
        text = message["content"].strip() or "…"
        if role == "assistant":
            note = lookup_note(message.get("lookups", ""))
        elif note:
            text, note = f"{note}\n\n{text}", ""
        yield role, text


def lookup_note(lookups: str) -> str:
    """Tells Claude that its previous reply was based on real lookups."""
    if not lookups:
        return ""
    return (
        f"[Note from the app, not from the user: your previous reply used {lookups}. That was a "
        "real lookup whose results aren't shown again, so stand by it. Don't mention this note.]"
    )


def with_note(prompt: list[dict], note: str) -> list[dict]:
    """The prompt with a note added to the user's new message, for this reply only."""
    last = prompt[-1]
    return [*prompt[:-1], {**last, "content": f"{last['content']}\n\n{note}"}]


def private_refusal() -> web.Found:
    """What she's told when a message with the globe on looks private: it isn't searched."""
    return web.failure_context(
        "That message looks like it contains private details (like a password or an email "
        "address), so I didn't search the web for it."
    )


def weather_report(tool_input: object, home: str, fetch: web.Fetch = web.fetch_json) -> tuple[str, bool]:
    """Runs the weather tool. Only a place name is sent, and only to Open-Meteo.
    Returns the text for Claude, and whether it holds a forecast."""
    place = tool_input.get("place", "") if isinstance(tool_input, dict) else None
    if not isinstance(place, str):
        return "Invalid input: give the place as text.", False
    named = " ".join(place.split())[:80]
    place = named or home.strip()
    if not place:
        return "No place was given and the user hasn't set a home town. Ask which city they mean.", False
    if web.looks_private(place):
        return "That doesn't look like a place name, so it wasn't looked up.", False
    try:
        found = web.weather(place, fetch)
    except web.LookupFailed as problem:
        return f"The weather service couldn't be reached: {problem}", False
    if found is None:
        return f"Open-Meteo doesn't know a place called {place!r}. Ask the user to check the name.", False
    if not named:
        return f"No place was named, so this is for the user's home town from their settings.\n{found.context}", True
    return found.context, True


def search_tool(max_uses: int) -> dict:
    # The basic search: quicker than the version that filters results with code, and
    # enough for a short spoken answer. No location is sent with it.
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}


def dollars(amount: float) -> str:
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.3f}" if amount < 1 else f"${amount:.2f}"


def summary(stats: ReplyStats) -> str:
    """The small line under a reply: model, speed, searches and what it cost."""
    extra = stats.extra
    parts = [extra["model"].removeprefix("Claude "), f"first word {stats.first_token_seconds:.1f} s"]
    if extra["searches"]:
        parts.append(f"{extra['searches']} search" + ("es" if extra["searches"] != 1 else ""))
    parts.append(f"{dollars(extra['cost'])} (session {dollars(extra['session_cost'])})")
    return " · ".join(parts)


_SEARCH_PROBLEMS = {
    "too_many_requests": "Claude's web search is busy right now.",
    "unavailable": "Claude's web search isn't available right now.",
    "query_too_long": "That was too long for Claude's web search.",
}


@dataclass
class _Reply:
    """What one reply has produced so far, across the requests it took."""

    text: str = ""
    first_text_at: float = 0.0  # time.monotonic() when the first words arrived
    gap: bool = False  # a search came between two parts of the text
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0
    searches: int = 0
    cited: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    problem: str = ""
    weather: list[str] = field(default_factory=list)  # places the weather tool found forecasts for
    queries: list[str] = field(default_factory=list)  # what Claude searched the web for
    stop_reason: str | None = None

    def handle(self, event, on_status: Callable[[str], None]) -> str:
        """Returns new text to show, if the event has any."""
        if event.type == "content_block_start":
            if event.content_block.type in ("server_tool_use", "web_search_tool_result", "tool_use"):
                self.gap = True
        elif event.type == "content_block_stop" and event.content_block.type == "server_tool_use":
            query = (event.content_block.input or {}).get("query")
            if isinstance(query, str) and query.strip():
                self.queries.append(" ".join(query.split())[:100])
                on_status("Searching the web: " + self.queries[-1])
        elif event.type == "text" and event.text:
            text = event.text
            if self.gap and self.text and not self.text[-1].isspace() and not text[0].isspace():
                text = " " + text  # text blocks before and after a search join without a space
            self.gap = False
            self.text += text
            self.first_text_at = self.first_text_at or time.monotonic()
            return text
        return ""

    def add(self, message) -> None:
        """Counts a finished response: tokens, searches and the sites it used."""
        usage = message.usage
        self.input += usage.input_tokens or 0
        self.cache_write += usage.cache_creation_input_tokens or 0
        self.cache_read += usage.cache_read_input_tokens or 0
        self.output += usage.output_tokens or 0
        if usage.server_tool_use is not None:
            self.searches += usage.server_tool_use.web_search_requests or 0
        for block in message.content:
            if block.type == "text":
                self._cited(block.citations or [])
            elif block.type == "web_search_tool_result":
                self._searched(block.content)
        self.stop_reason = message.stop_reason

    def add_stopped(self, stream, characters: int) -> None:
        """Counts a response that was stopped: Anthropic reported what it read when it
        started; what it wrote is estimated from the text (about 3 characters a token)."""
        try:
            usage = stream.current_message_snapshot.usage
        except Exception:  # stopped before the response even started
            return
        self.input += usage.input_tokens or 0
        self.cache_write += usage.cache_creation_input_tokens or 0
        self.cache_read += usage.cache_read_input_tokens or 0
        self.output += max(usage.output_tokens or 0, characters // 3)

    def _cited(self, citations: list) -> None:
        self.cited.extend(web.site(c.url) for c in citations if c.type == "web_search_result_location")

    def _searched(self, content) -> None:
        if isinstance(content, list):
            self.results.extend(web.site(result.url) for result in content)
        elif content.error_code in _SEARCH_PROBLEMS:
            self.problem = _SEARCH_PROBLEMS[content.error_code]

    def cost(self, model: Model) -> float:
        tokens = (
            self.input * model.input
            + self.cache_write * model.cache_write
            + self.cache_read * model.cache_read
            + self.output * model.output
        )
        return tokens / 1_000_000 + self.searches * SEARCH_PRICE

    def sources(self) -> list[str]:
        """Open-Meteo if it was asked, then the sites Claude cited, or else the ones its searches found."""
        found = self._sites()
        if self.weather:
            found = ["open-meteo.com", *found]
        return found[:MAX_SOURCES]

    def _sites(self) -> list[str]:
        return [s for s in dict.fromkeys(self.cited) if s] or [s for s in dict.fromkeys(self.results) if s]

    def lookups(self) -> str:
        """What this reply looked up, kept with it in the history (see lookups_note)."""
        parts = [f"weather for {place} (Open-Meteo)" for place in dict.fromkeys(self.weather)]
        if self.queries:
            sites = ", ".join(self._sites()[:MAX_SOURCES])
            searched = ", ".join(f'"{query}"' for query in dict.fromkeys(self.queries))
            parts.append(f"a web search for {searched}" + (f" ({sites})" if sites else ""))
        return "; ".join(parts)


def explain(error: Exception) -> str:
    """A short explanation of an SDK error for the speech bubble."""
    import anthropic

    detail = _detail(error)
    checks = [
        (anthropic.AuthenticationError, "Claude didn't accept the API key. You can set it again with right-click → Brain."),
        (anthropic.PaymentRequiredError, "The Claude API credit has run out. You can add credit in the Claude Console."),
        ((anthropic.EnforcedSpendLimitReachedError, anthropic.ContractSpendLimitReachedError),
         "The spending limit of your Claude API account has been reached."),
        (anthropic.RateLimitError, "Claude is getting too many requests right now. Try again in a minute."),
        (anthropic.PermissionDeniedError, "This Claude API key isn't allowed to do that."),
        (anthropic.NotFoundError, "Claude doesn't know that model. Pick another one under right-click → Brain."),
        (anthropic.InternalServerError, "Claude is overloaded or having problems right now."),
        (anthropic.APITimeoutError, "Claude took too long to answer."),
        (anthropic.APIConnectionError, "I couldn't reach Claude. Is the internet connected?"),
    ]  # fmt: skip
    if "credit balance" in detail.lower():
        return checks[1][1]
    for kinds, text in checks:
        if isinstance(error, kinds):
            return text
    if isinstance(error, anthropic.APIStatusError):
        return f"Claude refused the request ({error.status_code}): {detail}"
    return detail or type(error).__name__


def _detail(error: Exception) -> str:
    body = getattr(error, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        message = body["error"].get("message")
        if isinstance(message, str):
            return message[:200]
    return str(error)[:200]


class Claude:
    """The connection to Anthropic, kept for the whole session so replies start sooner."""

    def __init__(self, key: str, fetch: web.Fetch = web.fetch_json) -> None:
        import anthropic  # loaded only for the online brain; it takes a few seconds

        self.client = anthropic.Anthropic(
            api_key=key,  # given explicitly, so no environment variable is used instead
            base_url=API_ADDRESS,
            max_retries=1,
            timeout=anthropic.Timeout(60.0, connect=10.0),
        )
        self.fetch = fetch  # for the weather tool: Open-Meteo only
        self.session_cost = 0.0
        self._lock = threading.Lock()
        self._stream = None

    def check(self, model: str) -> None:
        """A free request that confirms the key works, and opens the connection."""
        try:
            self.client.models.retrieve(model, timeout=15.0)
        except Exception as error:
            raise CloudError(explain(error)) from error

    def abort(self) -> None:
        """Ends the reply that is being written, even while it waits for a search."""
        with self._lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.close()
            except Exception:  # it may already be closing on its own
                pass

    def close(self) -> None:
        self.abort()
        try:
            self.client.close()
        except Exception:
            pass

    def stream_reply(
        self,
        prompt: list[dict],
        request: Request,
        stats: ReplyStats,
        should_stop: Callable[[], bool],
        on_status: Callable[[str], None],
    ) -> Iterator[str]:
        """Yields the reply text in pieces as Claude writes it. `on_status` gets short
        notes like "Searching the web: …" while Claude uses a tool."""
        model = MODELS[request.model]
        system, messages = split_prompt(prompt)
        params = request_params(request, model, system)
        reply = _Reply()
        started = time.monotonic()
        try:
            for _ in range(MAX_ROUNDS):
                final = yield from self._response(params, messages, reply, should_stop, on_status)
                if final is None:
                    return  # stopped
                reply.add(final)
                turn = self._next_turn(final, request.home, reply, on_status)
                if turn is None or should_stop():
                    break
                messages = [*messages, {"role": "assistant", "content": final.content}, *turn]
                reply.gap = True
        except Exception as error:
            if should_stop():
                return  # stopping closes the connection, which ends the request with an error
            raise CloudError(explain(error)) from error
        finally:
            with self._lock:
                self._stream = None
            self._record(reply, model, stats, started)
        if reply.stop_reason == "refusal" and not reply.text.strip():
            raise CloudError("Claude declined to answer that one.", local_can_answer=False)

    def _response(self, params: dict, messages: list[dict], reply: _Reply, should_stop, on_status):
        """Streams one response; returns the finished message, or None if stopped."""
        written = len(reply.text)
        with self.client.messages.stream(**params, messages=messages) as stream:
            with self._lock:
                self._stream = stream
            try:
                for event in stream:
                    if should_stop():
                        break
                    text = reply.handle(event, on_status)
                    if text:
                        yield text
            except Exception:
                if not should_stop():
                    raise  # otherwise stopping closed the connection
            if should_stop():
                reply.add_stopped(stream, len(reply.text) - written)
                return None
            return stream.get_final_message()

    def _next_turn(self, final, home: str, reply: _Reply, on_status) -> list[dict] | None:
        """What to send next for a reply that isn't finished, or None when it is.

        A paused reply (a long search) is sent back as it is, so Claude continues. A
        weather request is answered with the forecast. A reply cut off by max_tokens or
        a refusal is never continued, even if it asked for a tool.
        """
        if final.stop_reason == "pause_turn":
            return []
        if final.stop_reason != "tool_use":
            return None
        results = [self._use_tool(b, home, reply, on_status) for b in final.content if b.type == "tool_use"]
        return [{"role": "user", "content": results}] if results else None

    def _use_tool(self, block, home: str, reply: _Reply, on_status) -> dict:
        if block.name != WEATHER_TOOL["name"]:
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": "There is no such tool."}
        place = block.input.get("place") if isinstance(block.input, dict) else None
        named = " ".join(place.split())[:60] if isinstance(place, str) else ""
        on_status(f"Checking the weather: {named or home}" if named or home else "Checking the weather…")
        text, found = weather_report(block.input, home, self.fetch)
        if found:
            reply.weather.append(named or f"{home} (the user's home town)")
        return {"type": "tool_result", "tool_use_id": block.id, "content": text}

    def _record(self, reply: _Reply, model: Model, stats: ReplyStats, started: float) -> None:
        cost = reply.cost(model)
        self.session_cost += cost
        stats.total_seconds = time.monotonic() - started
        stats.first_token_seconds = reply.first_text_at - started if reply.first_text_at else 0.0
        stats.reply_tokens = reply.output
        stats.prompt_tokens = reply.input + reply.cache_write + reply.cache_read
        stats.cached_tokens = reply.cache_read  # read from Anthropic's cache, far cheaper
        stats.extra = {
            "brain": "claude",
            "model": model.label,
            "searches": reply.searches,
            "cost": cost,
            "session_cost": self.session_cost,
            "sources": reply.sources(),
            "lookups": reply.lookups(),
            "note": reply.problem if not reply.results else "",
        }


def request_params(request: Request, model: Model, system: str) -> dict:
    params: dict = {
        "model": request.model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "cache_control": {"type": "ephemeral"},  # the personality and history are reused
        "output_config": {"effort": EFFORT},
    }
    if model.thinking is not None:
        params["thinking"] = model.thinking
    # The weather tool is always offered and comes first, so the cached start stays the same.
    params["tools"] = [WEATHER_TOOL] + ([search_tool(request.max_searches)] if request.search else [])
    return params
