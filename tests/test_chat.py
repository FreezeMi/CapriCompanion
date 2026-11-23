import unittest

from companion.chat import (
    ENGLISH_NOTE,
    Conversation,
    MoodFilter,
    estimate_tokens,
    leading_mood,
    looks_foreign,
    system_prompt,
    visible_text,
)


def stream(pieces: list[str]) -> tuple[str, str | None]:
    mood_filter = MoodFilter()
    shown = "".join(mood_filter.feed(piece) for piece in pieces) + mood_filter.flush()
    return shown, mood_filter.mood


class MoodTagTest(unittest.TestCase):
    def test_tag_is_removed_and_read(self):
        self.assertEqual(stream(["[happy] Hi there!"]), ("Hi there!", "happy"))

    def test_tag_split_across_pieces(self):
        self.assertEqual(stream(["[", "exc", "ited]", " Yes", "!"]), ("Yes!", "excited"))

    def test_reply_without_tag_is_unchanged(self):
        self.assertEqual(stream(["Hello", " you."]), ("Hello you.", None))

    def test_unknown_tag_is_hidden_without_a_mood(self):
        self.assertEqual(stream(["[giggling] Hehe."]), ("Hehe.", None))

    def test_synonym_maps_to_a_mood(self):
        self.assertEqual(leading_mood("[Curious] Oh?"), "attentive")

    def test_decorated_tag(self):
        self.assertEqual(stream(["**[sad]** Oh no."]), ("Oh no.", "sad"))

    def test_brackets_later_in_text_are_kept(self):
        self.assertEqual(visible_text("[happy] Press [Enter] to send."), "Press [Enter] to send.")

    def test_known_tags_inside_text_are_hidden(self):
        self.assertEqual(visible_text("[happy] Hi! [smirk] Gotcha."), "Hi! Gotcha.")

    def test_long_italic_opening_is_released(self):
        text = "*stretches and yawns loudly before answering the question* Sure."
        self.assertEqual(stream([text]), (text, None))


class PartialTagTest(unittest.TestCase):
    def test_partial_tag_never_shows(self):
        self.assertEqual(visible_text("[happ", finished=False), "")
        self.assertEqual(visible_text("[happ"), "")  # stopped mid-tag: nothing to show
        self.assertEqual(visible_text("[happy] Hey", finished=False), "Hey")

    def test_other_openings_show_while_streaming(self):
        self.assertEqual(visible_text("*waves* Hi", finished=False), "*waves* Hi")
        self.assertEqual(visible_text("Hello th", finished=False), "Hello th")


class LookupRecordTest(unittest.TestCase):
    def test_lookups_are_kept_but_not_sent_as_fields(self):
        conversation = Conversation()
        conversation.add("user", "Will it rain tomorrow?")
        conversation.add("assistant", "[happy] Nope!", "weather for Ghent (Open-Meteo)")
        conversation.add("user", "And Saturday?")
        prompt = conversation.prompt("You are Capri.", 3000)
        self.assertTrue(all(set(m) == {"role", "content"} for m in prompt))  # the local engine gets plain turns
        online = conversation.prompt("You are Capri.", 3000, lookups=True)
        self.assertEqual(online[2]["lookups"], "weather for Ghent (Open-Meteo)")


class StageDirectionTest(unittest.TestCase):
    def test_stage_directions_are_hidden(self):
        self.assertEqual(visible_text("[annoyed] Seriously? [glares] Come on."), "Seriously? Come on.")

    def test_code_and_notes_are_kept(self):
        for text in ("Use items[index] here.", "Try my_list[::-1].", "matrix[i][j] works", "See note [1]."):
            self.assertEqual(visible_text(text), text)


class LanguageTest(unittest.TestCase):
    def test_other_languages_are_noticed(self):
        for text in ("Ik ben echt moe vandaag.", "Wat zullen we vanavond doen?", "Je suis fatigué aujourd'hui",
                     "Wie geht es dir heute?", "¿Qué tal estás hoy?"):  # fmt: skip
            self.assertTrue(looks_foreign(text), text)

    def test_english_is_left_alone(self):
        for text in ("Hey Capri, who are you?", "Explain how black holes form.", "Ghent", "Tell me about De Panne beach",
                     "die hard is a great movie", "Do I need a jacket in Ghent tonight?"):  # fmt: skip
            self.assertFalse(looks_foreign(text), text)

    def test_note_goes_on_those_messages_only(self):
        conversation = Conversation()
        conversation.add("user", "Ik ben echt moe vandaag.")
        conversation.add("assistant", "[sad] Long day, huh.")
        conversation.add("user", "Yeah, it was.")
        prompt = conversation.prompt("You are Capri.", 3000)
        self.assertTrue(prompt[1]["content"].endswith(ENGLISH_NOTE))
        self.assertEqual(prompt[3]["content"], "Yeah, it was.")
        self.assertEqual(conversation.messages[0]["content"], "Ik ben echt moe vandaag.")  # history unchanged


class ConversationTest(unittest.TestCase):
    def make(self, turns: int) -> Conversation:
        conversation = Conversation()
        for index in range(turns):
            conversation.add("user", f"question {index} " + "x" * 300)
            conversation.add("assistant", f"[happy] answer {index} " + "y" * 300)
        return conversation

    def test_prompt_starts_with_system_then_user(self):
        messages = self.make(3).prompt("You are Capri.", budget=100_000)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("You are Capri.", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(len(messages), 7)

    def test_trimming_drops_old_turns_in_one_step(self):
        conversation = self.make(20)
        budget = 2000
        first = conversation.prompt("p", budget)
        start = conversation.start
        self.assertGreater(start, 0)
        self.assertEqual(first[1]["role"], "user")
        kept = sum(estimate_tokens(m["content"]) for m in first[1:])
        self.assertLessEqual(kept, budget * 0.6)
        # A further turn that still fits keeps the same starting point (cache stays valid).
        conversation.add("user", "short")
        conversation.prompt("p", budget)
        self.assertEqual(conversation.start, start)

    def test_latest_message_is_always_kept(self):
        conversation = Conversation()
        conversation.add("user", "z" * 50_000)
        messages = conversation.prompt("p", budget=100)
        self.assertEqual(messages[-1]["content"], "z" * 50_000)

    def test_system_prompt_lists_moods(self):
        self.assertIn("[happy]", system_prompt("p"))


if __name__ == "__main__":
    unittest.main()
