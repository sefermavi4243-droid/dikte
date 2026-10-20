"""Translation lookup, and the table itself.

The table test is the one that matters for a pull request: a Turkish string
whose placeholder was renamed raises KeyError at the moment the message is
shown, which is exactly when nobody is watching a terminal.
"""

import string
import unittest
from unittest import mock

from dikte import i18n
from tests.support import DikteTest


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class Resolve(unittest.TestCase):
    def test_an_explicit_language_wins(self):
        with mock.patch.dict("os.environ", {"LANG": "tr_TR.UTF-8"}):
            self.assertEqual(i18n.resolve("en"), "en")
            self.assertEqual(i18n.resolve("tr"), "tr")

    def test_auto_reads_the_locale(self):
        with mock.patch.dict("os.environ", {"LANG": "tr_TR.UTF-8"}, clear=True):
            self.assertEqual(i18n.resolve("auto"), "tr")
        with mock.patch.dict("os.environ", {"LANG": "en_GB.UTF-8"}, clear=True):
            self.assertEqual(i18n.resolve("auto"), "en")

    def test_lc_all_outranks_lang(self):
        with mock.patch.dict("os.environ",
                             {"LC_ALL": "tr_TR.UTF-8", "LANG": "en_GB.UTF-8"},
                             clear=True):
            self.assertEqual(i18n.resolve("auto"), "tr")

    def test_no_locale_at_all_falls_back_to_english(self):
        with mock.patch.dict("os.environ", {}, clear=True),                 mock.patch.object(i18n, "_system_language", return_value=""):
            self.assertEqual(i18n.resolve("auto"), "en")

    def test_an_unknown_code_is_not_taken_at_its_word(self):
        with mock.patch.dict("os.environ", {}, clear=True),                 mock.patch.object(i18n, "_system_language", return_value=""):
            self.assertEqual(i18n.resolve("de"), "en")

    def test_windows_display_language_stands_in_for_the_environment(self):
        with mock.patch.dict("os.environ", {}, clear=True),                 mock.patch.object(i18n, "_system_language",
                                  return_value="tr_TR"):
            self.assertEqual(i18n.resolve("auto"), "tr")


class Translate(DikteTest):
    def test_english_returns_the_source_string(self):
        self.assertEqual(i18n.t("Quit"), "Quit")

    def test_turkish_looks_the_string_up(self):
        i18n.set_language("tr")
        self.assertEqual(i18n.t("Quit"), "Çık")

    def test_an_untranslated_string_falls_through(self):
        i18n.set_language("tr")
        self.assertEqual(i18n.t("Nobody translated this"), "Nobody translated this")

    def test_placeholders_are_filled_in_both_languages(self):
        self.assertEqual(i18n.t("Unknown key: {key}", key="f13"), "Unknown key: f13")
        i18n.set_language("tr")
        self.assertIn("f13", i18n.t("Unknown key: {key}", key="f13"))

    def test_a_string_with_no_arguments_is_not_formatted(self):
        # Braces in the text itself must survive when nothing is passed in.
        self.assertEqual(i18n.t("{not a placeholder}"), "{not a placeholder}")

    def test_a_placeholder_may_be_called_anything(self):
        """Including the names of t()'s own parameters, which is why they are
        positional-only: worker.py says {text}, and that has to work."""
        self.assertEqual(i18n.t("Discarded: {text}", text="hello"), "Discarded: hello")
        self.assertEqual(i18n.name("Claude", case="dative"), "Claude")


class Names(DikteTest):
    def test_english_leaves_the_name_alone(self):
        self.assertEqual(i18n.name("Claude", "dative"), "Claude")

    def test_turkish_inflects_by_the_vowels_of_the_name(self):
        i18n.set_language("tr")
        self.assertEqual(i18n.name("Claude", "dative"), "Claude'a")
        self.assertEqual(i18n.name("Codex", "dative"), "Codex'e")
        self.assertEqual(i18n.name("OpenRouter", "accusative"), "OpenRouter'ı")
        self.assertEqual(i18n.name("Requesty", "dative"), "Requesty'ye")

    def test_no_case_asked_for(self):
        i18n.set_language("tr")
        self.assertEqual(i18n.name("Claude"), "Claude")

    def test_an_unlisted_name_or_case_comes_back_unchanged(self):
        i18n.set_language("tr")
        self.assertEqual(i18n.name("Ollama", "dative"), "Ollama")
        self.assertEqual(i18n.name("Claude", "ablative"), "Claude")


class Table(unittest.TestCase):
    """The Turkish table against the English strings it stands in for."""

    def test_every_translation_keeps_the_placeholders_of_its_source(self):
        for source, translated in i18n.TR.items():
            with self.subTest(source=source[:50]):
                self.assertEqual(
                    placeholders(source), placeholders(translated),
                    "the Turkish string does not take the same arguments",
                )

    def test_nothing_is_translated_to_an_empty_string(self):
        for source, translated in i18n.TR.items():
            with self.subTest(source=source[:50]):
                self.assertTrue(translated.strip())

    def test_every_translation_is_formattable(self):
        """Whatever the table holds, .format() must not blow up on it."""
        for source, translated in i18n.TR.items():
            names = placeholders(translated)
            if not names:
                continue
            with self.subTest(source=source[:50]):
                translated.format(**{key: "x" for key in names})


if __name__ == "__main__":
    unittest.main()
