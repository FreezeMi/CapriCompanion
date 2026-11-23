import unittest

from companion.voice import FRAME, Clip, SpeechChunker, envelope, level_at, reveal_position, speakable


def texts(pieces) -> list[str]:
    return [piece.text for piece in pieces]


class SpeakableTest(unittest.TestCase):
    def test_actions_emoji_links_and_markup_are_not_read(self):
        text = "*giggles* That's **so** funny 😂 see https://example.com/x ok"
        self.assertEqual(speakable(text), "That's so funny see a link ok")

    def test_plain_text_is_unchanged(self):
        self.assertEqual(speakable("Hello there, how are you?"), "Hello there, how are you?")


class ChunkerTest(unittest.TestCase):
    REPLY = (
        "Oh, welcome home, you made it back at last! You definitely need some downtime. "
        "Yes! How about some chill music and a warm drink?"
    )

    def test_first_piece_ends_early_at_a_comma(self):
        self.assertEqual(texts(SpeechChunker().feed("Oh, welcome home, you made ")), ["Oh, welcome home,"])

    def test_first_short_sentence_is_spoken_at_once(self):
        self.assertEqual(texts(SpeechChunker().feed("Hi! Then")), ["Hi!"])

    def test_waits_for_the_end_of_a_sentence(self):
        chunker = SpeechChunker()
        chunker.feed("Hi! ")
        self.assertEqual(chunker.feed("Hi! You definitely need"), [])

    def test_whole_reply_in_one_go(self):
        chunker = SpeechChunker()
        pieces = chunker.feed(self.REPLY) + chunker.finish(self.REPLY)
        self.assertEqual(
            texts(pieces),
            [
                "Oh, welcome home,",
                "you made it back at last!",
                "You definitely need some downtime.",
                "Yes! How about some chill music and a warm drink?",
            ],
        )

    def test_streaming_gives_the_same_pieces(self):
        whole = SpeechChunker()
        expected = whole.feed(self.REPLY) + whole.finish(self.REPLY)
        streamed = SpeechChunker()
        pieces = []
        for end in range(1, len(self.REPLY) + 1):
            pieces += streamed.feed(self.REPLY[:end])
        pieces += streamed.finish(self.REPLY)
        self.assertEqual(pieces, expected)

    def test_pieces_cover_the_reply_without_gaps(self):
        chunker = SpeechChunker()
        pieces = chunker.feed(self.REPLY) + chunker.finish(self.REPLY)
        self.assertEqual(pieces[0].start, 0)
        self.assertEqual(pieces[-1].end, len(self.REPLY))
        for previous, piece in zip(pieces, pieces[1:]):
            self.assertEqual(piece.start, previous.end)
        for piece in pieces:
            self.assertEqual(speakable(self.REPLY[piece.start : piece.end]), piece.text)

    def test_unspoken_text_joins_the_next_piece(self):
        reply = "Hi there! 😂😂 How are you doing today?"
        chunker = SpeechChunker()
        pieces = chunker.feed(reply) + chunker.finish(reply)
        self.assertEqual(texts(pieces), ["Hi there!", "How are you doing today?"])
        self.assertIn("😂", reply[pieces[1].start : pieces[1].end])

    def test_numbers_with_decimal_points_are_not_split(self):
        text = "The answer is 3.5 percent, more or less. Okay"
        self.assertEqual(texts(SpeechChunker().feed(text)), ["The answer is 3.5 percent,"])

    def test_short_sentences_are_merged_with_the_next(self):
        chunker = SpeechChunker()
        text = "The answer is 3.5 percent, more or less. Okay"
        chunker.feed(text)
        self.assertEqual(texts(chunker.finish(text)), ["more or less. Okay"])

    def test_nothing_speakable_gives_nothing(self):
        chunker = SpeechChunker()
        self.assertEqual(chunker.feed("😂 ") + chunker.finish("😂 "), [])


class RevealTest(unittest.TestCase):
    TEXT = "Hello there, how are you doing today?"

    def test_nothing_before_she_starts(self):
        self.assertEqual(reveal_position(self.TEXT, 0, len(self.TEXT), 0.0), 0)

    def test_everything_when_the_piece_is_done(self):
        self.assertEqual(reveal_position(self.TEXT, 0, len(self.TEXT), 1.0), len(self.TEXT))

    def test_never_half_a_word(self):
        position = reveal_position(self.TEXT, 0, len(self.TEXT), 0.5)
        self.assertEqual(self.TEXT[:position], "Hello there, how are")

    def test_stays_inside_the_piece(self):
        start, end = 13, len(self.TEXT)  # "how are you doing today?"
        self.assertGreaterEqual(reveal_position(self.TEXT, start, end, 0.01), start)
        self.assertLessEqual(reveal_position(self.TEXT, start, end, 0.99), end)


class LoudnessTest(unittest.TestCase):
    def test_envelope_is_scaled_to_the_loudest_frame(self):
        quiet, loud = [0.1] * FRAME, [0.5] * FRAME
        self.assertEqual(envelope(quiet + loud + [0.0] * FRAME), [0.2, 1.0, 0.0])

    def test_short_sound_has_one_silent_frame(self):
        self.assertEqual(envelope([0.3] * 10), [0.0])

    def test_level_follows_the_playback_position(self):
        timeline = [
            Clip(0, 2 * FRAME, [0.2, 0.9], 1, None, 2 * FRAME),
            Clip(2 * FRAME, 3 * FRAME, [0.5], 1, None, FRAME),
        ]
        self.assertEqual(level_at(timeline, 0), 0.2)
        self.assertEqual(level_at(timeline, FRAME + 1), 0.9)
        self.assertEqual(level_at(timeline, 2 * FRAME), 0.5)
        self.assertEqual(level_at(timeline, 3 * FRAME), 0.0)


if __name__ == "__main__":
    unittest.main()
