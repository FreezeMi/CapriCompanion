import os
import unittest
from datetime import date
from types import SimpleNamespace as NS
from unittest import mock

import anthropic
import httpx2

from companion import cloud, web
from companion.chat import ReplyStats

KEY = "sk-ant-api03-" + "x" * 40


def usage(input_tokens=0, output=0, cache_read=0, cache_write=0, searches=0):
    return NS(
        input_tokens=input_tokens,
        output_tokens=output,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        server_tool_use=NS(web_search_requests=searches) if searches else None,
    )


def text_event(text):
    return NS(type="text", text=text)


def block_start(kind):
    return NS(type="content_block_start", content_block=NS(type=kind))


def search_done(query):
    return NS(type="content_block_stop", content_block=NS(type="server_tool_use", input={"query": query}))


class FakeStream:
    def __init__(self, events, final):
        self.events, self.final, self.closed = events, final, False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final

    def close(self):
        self.closed = True


class FakeMessages:
    """Plays back one prepared stream per request and records what was sent."""

    def __init__(self, streams):
        self.streams, self.requests = list(streams), []

    def stream(self, **params):
        self.requests.append(params)
        stream = self.streams.pop(0)
        if isinstance(stream, Exception):
            raise stream
        return stream


def no_network(url, headers):
    raise AssertionError(f"unexpected request to {url}")


PARIS = {"results": [{"name": "Paris", "country": "France", "latitude": 48.85, "longitude": 2.35}]}
FORECAST = {
    "current": {"temperature_2m": 7.1, "apparent_temperature": 4.2, "weather_code": 3, "wind_speed_10m": 12.0},
    "daily": {"time": ["2026-11-23"], "weather_code": [61], "temperature_2m_max": [8.0],
              "temperature_2m_min": [4.0], "precipitation_probability_max": [60]},
}  # fmt: skip


class OpenMeteo:
    """Answers like Open-Meteo for Paris, and records the addresses asked for."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, headers):
        web.check_url(url)
        self.urls.append(url)
        if "geocoding" in url:
            return 200, PARIS if "name=Paris" in url else {"results": []}
        return 200, FORECAST


def connected(streams, fetch=no_network) -> tuple[cloud.Claude, FakeMessages]:
    claude = cloud.Claude(KEY, fetch=fetch)
    messages = FakeMessages(streams)
    claude.client = NS(messages=messages, close=lambda: None)
    return claude, messages


PROMPT = [
    {"role": "system", "content": "You are Capri."},
    {"role": "user", "content": "Who won the match?"},
]
REQUEST = cloud.Request("claude-haiku-5-5", search=True, max_searches=3)


def collect(claude, prompt=PROMPT, request=REQUEST, should_stop=lambda: False):
    stats, searches = ReplyStats(), []
    text = "".join(claude.stream_reply(prompt, request, stats, should_stop, searches.append))
    return text, stats, searches


class ConnectionTest(unittest.TestCase):
    def test_the_address_is_fixed_and_the_key_given(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://evil.example.com", "ANTHROPIC_API_KEY": "other"}):
            claude = cloud.Claude(KEY)
        self.assertEqual(str(claude.client.base_url).rstrip("/"), "https://api.anthropic.com")
        self.assertEqual(claude.client.api_key, KEY)


class PromptTest(unittest.TestCase):
    def test_split_prompt(self):
        prompt = [
            {"role": "system", "content": "rules"},
            {"role": "assistant", "content": "[happy] hello"},  # the API wants the user first
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"},
            {"role": "assistant", "content": "  "},
            {"role": "user", "content": "three"},
        ]
        system, turns = cloud.split_prompt(prompt)
        self.assertEqual(system, "rules")
        self.assertEqual(
            turns,
            [
                {"role": "user", "content": "one\n\ntwo"},
                {"role": "assistant", "content": "…"},
                {"role": "user", "content": "three"},
            ],
        )

    def test_situation(self):
        today = date(2026, 11, 23)
        self.assertIn("only when the user switches on a lookup", cloud.situation("globe", today))
        self.assertIn("Today is Monday, 23 November 2026.", cloud.situation("auto", today))
        self.assertIn("but you can search the web", cloud.situation("auto", today))
        for mode in ("globe", "auto"):  # weather never needs the globe
            self.assertIn("weather at any time with your get_weather tool", cloud.situation(mode, today))

    def test_lookup_records_go_before_the_next_message(self):
        prompt = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "Will it rain tomorrow?"},
            {"role": "assistant", "content": "[happy] No!", "lookups": "weather for Ghent (Open-Meteo)"},
            {"role": "user", "content": "Thanks!"},
            {"role": "assistant", "content": "[happy] Anytime."},
            {"role": "user", "content": "Bye"},
        ]
        _, turns = cloud.split_prompt(prompt)
        self.assertEqual(turns[1], {"role": "assistant", "content": "[happy] No!"})  # her words stay as they were
        self.assertTrue(turns[2]["content"].startswith(
            "[Note from the app, not from the user: your previous reply used weather for Ghent (Open-Meteo)."))  # fmt: skip
        self.assertTrue(turns[2]["content"].endswith("\n\nThanks!"))
        self.assertEqual(turns[4]["content"], "Bye")  # only the turn right after
        # The same history gives the same text, so the cached start of the prompt still matches.
        self.assertEqual(cloud.split_prompt(prompt[:4])[1], turns[:3])

    def test_note_is_added_to_the_new_message_only(self):
        noted = cloud.with_note(PROMPT, cloud.SEARCH_NOTE)
        self.assertEqual(noted[0], PROMPT[0])
        self.assertTrue(noted[-1]["content"].startswith("Who won the match?\n\n[Note from the app, not from the user: search the web"))
        self.assertEqual(PROMPT[-1]["content"], "Who won the match?")


class WeatherToolTest(unittest.TestCase):
    def test_forecast_for_a_named_place(self):
        fetch = OpenMeteo()
        text, found = cloud.weather_report({"place": "  Paris "}, "", fetch)
        self.assertTrue(found)
        self.assertIn("Weather for Paris, France", text)
        self.assertTrue(all("open-meteo.com" in url for url in fetch.urls))
        self.assertIn("name=Paris&", fetch.urls[0])

    def test_home_town_when_no_place_is_given(self):
        text, found = cloud.weather_report({}, "Paris", OpenMeteo())
        self.assertTrue(found)

    def test_asks_where_when_nothing_is_known(self):
        text, found = cloud.weather_report({"place": ""}, "", no_network)
        self.assertFalse(found)
        self.assertIn("Ask which city", text)

    def test_unknown_place(self):
        text, found = cloud.weather_report({"place": "Atlantis"}, "", OpenMeteo())
        self.assertFalse(found)
        self.assertIn("doesn't know a place called 'Atlantis'", text)

    def test_bad_or_private_input_is_not_sent(self):
        for tool_input in ({"place": 5}, "Paris", {"place": "my password: hunter2"}):
            with self.subTest(tool_input=tool_input):
                _, found = cloud.weather_report(tool_input, "", no_network)
                self.assertFalse(found)

    def test_offline(self):
        def offline(url, headers):
            raise web.LookupFailed("I couldn't reach the internet.")

        text, found = cloud.weather_report({"place": "Paris"}, "", offline)
        self.assertFalse(found)
        self.assertIn("couldn't be reached", text)

    def test_private_messages_are_not_searched(self):
        self.assertIn("private details", cloud.private_refusal().context)


class StreamTest(unittest.TestCase):
    def test_reply_with_a_search(self):
        final = NS(
            stop_reason="end_turn",
            usage=usage(input_tokens=1000, output=120, cache_read=2000, searches=1),
            content=[
                NS(type="text", text="[thinking] Let me check!", citations=None),
                NS(type="server_tool_use"),
                NS(type="web_search_tool_result", content=[
                    NS(url="https://www.bbc.co.uk/sport/1"), NS(url="https://uefa.com/x")]),
                NS(type="text", text="Spain won 2-1.", citations=[
                    NS(type="web_search_result_location", url="https://uefa.com/x")]),
            ],
        )  # fmt: skip
        events = [
            block_start("text"), text_event("[thinking] Let me check!"),
            block_start("server_tool_use"), search_done("Euro final result"),
            block_start("web_search_tool_result"),
            block_start("text"), text_event("Spain won 2-1."),
        ]  # fmt: skip
        claude, sent = connected([FakeStream(events, final)])
        text, stats, searches = collect(claude)
        self.assertEqual(text, "[thinking] Let me check! Spain won 2-1.")
        self.assertEqual(searches, ["Searching the web: Euro final result"])
        self.assertEqual(stats.extra["sources"], ["uefa.com"])  # cited, not merely found
        self.assertEqual(stats.extra["lookups"], 'a web search for "Euro final result" (uefa.com)')
        self.assertEqual(stats.extra["searches"], 1)
        expected = (1000 * 0.10 + 2000 * 0.01 + 120 * 0.50) / 1e6 + 0.01
        self.assertAlmostEqual(stats.extra["cost"], expected)
        self.assertAlmostEqual(claude.session_cost, expected)
        self.assertGreater(stats.first_token_seconds, 0)

        params = sent.requests[0]
        self.assertEqual(params["model"], "claude-haiku-5-5")
        self.assertEqual(params["system"], "You are Capri.")
        self.assertEqual(params["messages"], [{"role": "user", "content": "Who won the match?"}])
        self.assertEqual(params["thinking"], {"type": "disabled"})
        self.assertEqual(params["cache_control"], {"type": "ephemeral"})
        self.assertEqual(
            params["tools"],
            [cloud.WEATHER_TOOL, {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        )
        for sampling in ("temperature", "top_p", "top_k"):
            self.assertNotIn(sampling, params)

    def test_no_search_tool_unless_allowed(self):
        final = NS(stop_reason="end_turn", usage=usage(10, 5), content=[])
        claude, sent = connected([FakeStream([text_event("[happy] Hi!")], final)])
        collect(claude, request=cloud.Request("claude-opus-5-5", search=False, max_searches=3))
        self.assertEqual(sent.requests[0]["tools"], [cloud.WEATHER_TOOL])  # weather is always there
        self.assertNotIn("thinking", sent.requests[0])  # Opus 5.5 always thinks

    def test_weather_tool_round(self):
        asked = [NS(type="text", text="[happy] Let me see.", citations=None),
                 NS(type="tool_use", id="toolu_1", name="get_weather", input={"place": "Paris"})]  # fmt: skip
        first = NS(stop_reason="tool_use", usage=usage(500, 30), content=asked)
        second = NS(stop_reason="end_turn", usage=usage(600, 25), content=[])
        claude, sent = connected(
            [
                FakeStream([block_start("text"), text_event("[happy] Let me see."), block_start("tool_use")], first),
                FakeStream([block_start("text"), text_event("It's 7 degrees and cloudy in Paris.")], second),
            ],
            fetch=OpenMeteo(),
        )
        text, stats, notes = collect(claude, request=cloud.Request("claude-haiku-5-5", False, 3))
        self.assertEqual(text, "[happy] Let me see. It's 7 degrees and cloudy in Paris.")
        self.assertEqual(notes, ["Checking the weather: Paris"])
        self.assertEqual(stats.extra["sources"], ["open-meteo.com"])
        self.assertEqual(stats.extra["lookups"], "weather for Paris (Open-Meteo)")
        self.assertEqual(stats.extra["searches"], 0)
        follow_up = sent.requests[1]["messages"]
        self.assertEqual(follow_up[-2], {"role": "assistant", "content": asked})
        result = follow_up[-1]["content"][0]
        self.assertEqual((result["type"], result["tool_use_id"]), ("tool_result", "toolu_1"))
        self.assertIn("Weather for Paris, France", result["content"])

    def test_cut_off_tool_requests_are_not_run(self):
        asked = [NS(type="tool_use", id="toolu_1", name="get_weather", input={"place": "Par"})]
        final = NS(stop_reason="max_tokens", usage=usage(10, 5), content=asked)
        claude, sent = connected([FakeStream([], final)])  # no_network: the tool must not run
        collect(claude)
        self.assertEqual(len(sent.requests), 1)

    def test_paused_reply_is_continued(self):
        paused_content = [NS(type="server_tool_use")]
        first = NS(stop_reason="pause_turn", usage=usage(100, 10, searches=1), content=paused_content)
        second = NS(stop_reason="end_turn", usage=usage(150, 20), content=[])
        claude, sent = connected([
            FakeStream([text_event("[thinking] Hmm,")], first),
            FakeStream([block_start("text"), text_event("found it.")], second),
        ])  # fmt: skip
        text, stats, _ = collect(claude)
        self.assertEqual(text, "[thinking] Hmm, found it.")
        self.assertEqual(sent.requests[1]["messages"][-1], {"role": "assistant", "content": paused_content})
        self.assertEqual(stats.reply_tokens, 30)

    def test_refusal_is_not_answered_locally(self):
        final = NS(stop_reason="refusal", usage=usage(10, 0), content=[])
        claude, _ = connected([FakeStream([], final)])
        with self.assertRaises(cloud.CloudError) as caught:
            collect(claude)
        self.assertFalse(caught.exception.local_can_answer)

    def test_stopping_ends_quietly(self):
        final = NS(stop_reason="end_turn", usage=usage(10, 5), content=[])
        claude, _ = connected([FakeStream([text_event("one "), text_event("two")], final)])
        pieces = []
        for piece in claude.stream_reply(PROMPT, REQUEST, ReplyStats(), lambda: bool(pieces), print):
            pieces.append(piece)
        self.assertEqual(pieces, ["one "])

    def test_connection_errors_are_explained(self):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        claude, _ = connected([anthropic.APIConnectionError(request=request)])
        with self.assertRaises(cloud.CloudError) as caught:
            collect(claude)
        self.assertIn("couldn't reach Claude", str(caught.exception))
        self.assertTrue(caught.exception.local_can_answer)


class ExplainTest(unittest.TestCase):
    def error(self, kind, status, message):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        body = {"type": "error", "error": {"type": "x", "message": message}}
        return kind(message, response=httpx2.Response(status, request=request), body=body)

    def test_explanations(self):
        cases = [
            (anthropic.AuthenticationError, 401, "invalid x-api-key", "didn't accept the API key"),
            (anthropic.BadRequestError, 400, "Your credit balance is too low to access the API.", "credit has run out"),
            (anthropic.RateLimitError, 429, "slow down", "too many requests"),
            (anthropic.InternalServerError, 529, "Overloaded", "overloaded"),
            (anthropic.BadRequestError, 400, "messages: bad", "(400): messages: bad"),
        ]
        for kind, status, message, expected in cases:
            with self.subTest(message=message):
                self.assertIn(expected, cloud.explain(self.error(kind, status, message)))


class SummaryTest(unittest.TestCase):
    def test_summary_line(self):
        stats = ReplyStats(first_token_seconds=1.24)
        stats.extra = {"model": "Claude Haiku 5.5", "searches": 2, "cost": 0.02114, "session_cost": 0.0351}
        self.assertEqual(cloud.summary(stats), "Haiku 5.5 · first word 1.2 s · 2 searches · $0.021 (session $0.035)")

    def test_dollars(self):
        self.assertEqual(cloud.dollars(0.00042), "$0.0004")
        self.assertEqual(cloud.dollars(1.5), "$1.50")


if __name__ == "__main__":
    unittest.main()
