import array
import math
import unittest

from dikte import audio
from dikte import wake


def chunk(level):
    """One CHUNK_FRAMES block of a sine at `level` (0..1) of full scale."""
    amplitude = int(32767 * level)
    samples = array.array("h", (
        int(amplitude * math.sin(2 * math.pi * 220 * i / audio.RATE))
        for i in range(audio.CHUNK_FRAMES)))
    return samples.tobytes()


def feed(segmenter, levels):
    found = []
    for level in levels:
        data = chunk(level)
        _peak, rms = audio.chunk_levels(data)
        result = segmenter.feed(data, rms)
        if result is not None:
            found.append(result)
    return found


class SplitWake(unittest.TestCase):
    def test_the_name_and_the_comma_are_cut_off(self):
        self.assertEqual(wake.split_wake("Claude, add a dark mode."),
                         (True, "add a dark mode."))

    def test_turkish_spellings_wake(self):
        for spoken in ("Klod, testleri çalıştır", "Claude Code testleri çalıştır",
                       "klod kod testleri çalıştır", "Hey Claude testleri çalıştır"):
            with self.subTest(spoken=spoken):
                self.assertEqual(wake.split_wake(spoken),
                                 (True, "testleri çalıştır"))

    def test_the_name_alone_wakes_with_nothing_after_it(self):
        self.assertEqual(wake.split_wake("Claude Code."), (True, ""))

    def test_cloud_alone_is_a_word_not_a_name(self):
        self.assertEqual(wake.split_wake("Cloud sunucusu kur"), (False, ""))
        self.assertEqual(wake.split_wake("cloud code deploy et"), (True, "deploy et"))

    def test_the_name_later_in_the_sentence_does_not_wake(self):
        self.assertEqual(wake.split_wake("bunu claude yapsın"), (False, ""))

    def test_stop_and_done_words(self):
        self.assertTrue(wake.is_stop("Dur."))
        self.assertTrue(wake.is_stop("stop"))
        self.assertTrue(wake.is_done("Tamam, bu kadar."))
        self.assertTrue(wake.is_done("Teşekkürler!"))
        self.assertFalse(wake.is_done("teşekkürler, şimdi testleri yaz"))


class Segmenter(unittest.TestCase):
    def test_an_utterance_between_silences_comes_out_once(self):
        segmenter = wake.Segmenter()
        found = feed(segmenter, [0.001] * 20 + [0.3] * 15 + [0.001] * 20)
        self.assertEqual(len(found), 1)
        pcm, values = found[0]
        # Preroll, the loud part and the closing silence, in whole chunks.
        self.assertEqual(len(pcm) % audio.CHUNK_BYTES, 0)
        self.assertGreaterEqual(len(values), 15 + wake.PREROLL_CHUNKS)

    def test_a_click_is_not_speech(self):
        segmenter = wake.Segmenter()
        self.assertEqual(feed(segmenter, [0.001] * 20 + [0.5] * 2 + [0.001] * 20), [])

    def test_a_quiet_room_never_opens_one(self):
        segmenter = wake.Segmenter()
        self.assertEqual(feed(segmenter, [0.0005] * 200), [])

    def test_two_utterances_come_out_separately(self):
        segmenter = wake.Segmenter()
        quiet, loud = [0.001] * 20, [0.3] * 12
        self.assertEqual(len(feed(segmenter, quiet + loud + quiet + loud + quiet)), 2)

    def test_a_long_utterance_is_cut_at_the_limit(self):
        segmenter = wake.Segmenter()
        limit = int(wake.MAX_SECONDS / wake.CHUNK_SECONDS)
        found = feed(segmenter, [0.001] * 20 + [0.3] * (limit + 30))
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
