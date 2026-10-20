import unittest

from dikte import config as cfg
from dikte import i18n
from dikte import speech

from tests.support import DikteTest


class ForSpeech(unittest.TestCase):
    def test_markdown_and_links_are_taken_out(self):
        text = "**Done.** I edited `app.py`. See [the docs](http://x.io) or https://y.io"
        self.assertEqual(speech.for_speech(text),
                         "Done. I edited app.py. See the docs or")

    def test_a_code_block_is_named_rather_than_read(self):
        text = "Here:\n```python\nprint(1)\n```\nThat is all."
        self.assertEqual(speech.for_speech(text, code_word="kod bloğu"),
                         "Here:\nkod bloğu.\nThat is all.")

    def test_list_markers_and_headings_go(self):
        self.assertEqual(speech.for_speech("# Title\n- one\n2. two"),
                         "Title\none\ntwo")

    def test_a_long_answer_is_cut_at_a_sentence(self):
        text = "First sentence here. Second one is longer than the cap allows."
        self.assertEqual(speech.for_speech(text, max_chars=30),
                         "First sentence here.")

    def test_without_a_sentence_end_it_is_cut_at_a_word(self):
        self.assertEqual(speech.for_speech("one two three four", max_chars=10),
                         "one two…")

    def test_no_cap_keeps_everything(self):
        text = "a" * 2000
        self.assertEqual(speech.for_speech(text, max_chars=0), text)


class SpokenPrompt(DikteTest):
    def test_the_agent_is_told_its_answer_is_heard(self):
        conf = cfg.Config()
        conf["speak_answers"] = True
        i18n.set_language("en")
        self.assertIn("read out loud", conf.assistant_prompt())

    def test_nothing_is_added_when_answers_are_not_spoken(self):
        conf = cfg.Config()
        conf["speak_answers"] = False
        self.assertEqual(conf.assistant_prompt(), cfg.default_assistant_prompt())


if __name__ == "__main__":
    unittest.main()
