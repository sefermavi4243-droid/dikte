import os
import tempfile
import unittest

from dikte import projects


class Conf(dict):
    def __getitem__(self, key):
        return self.get(key, {"projects_roots": "", "assistant_dir": "",
                              "assistant_recent_dirs": []}.get(key))


class Parse(unittest.TestCase):
    def test_turkish_switch_commands(self):
        for spoken in ("Proje snapcode'a geç.", "snapcode projesine geç",
                       "SnapCode projesini aç", "projeyi snapcode yap"):
            with self.subTest(spoken=spoken):
                kind, name = projects.parse(spoken)
                self.assertEqual(kind, "switch")
                self.assertEqual(name.lower(), "snapcode")

    def test_english_switch_commands(self):
        self.assertEqual(projects.parse("Switch to project weather"),
                         ("switch", "weather"))
        self.assertEqual(projects.parse("open the project weather"),
                         ("switch", "weather"))

    def test_create_commands(self):
        self.assertEqual(projects.parse("yeni proje hava durumu oluştur"),
                         ("create", "hava durumu"))
        self.assertEqual(projects.parse("Hava Durumu adında yeni bir proje oluştur"),
                         ("create", "Hava Durumu"))
        self.assertEqual(projects.parse("create a new project called weather app"),
                         ("create", "weather app"))

    def test_an_ordinary_request_is_not_a_command(self):
        self.assertIsNone(projects.parse("bu dosyadaki hatayı düzelt"))
        self.assertIsNone(projects.parse("add a login page to the project"))


class Find(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dikte-projects-")
        for name in ("snapcode", "kurum-asistani", "Koza"):
            os.makedirs(os.path.join(self.root, name))
        self.conf = Conf(projects_roots=self.root)

    def test_spoken_names_match_loosely(self):
        for spoken, folder in (("snap code", "snapcode"), ("SnapCode", "snapcode"),
                               ("kurum asistanı", "kurum-asistani"),
                               ("koza", "Koza"), ("snapcodea", "snapcode")):
            with self.subTest(spoken=spoken):
                self.assertEqual(os.path.basename(projects.find(self.conf, spoken)),
                                 folder)

    def test_nothing_close_finds_nothing(self):
        self.assertEqual(projects.find(self.conf, "weather"), "")

    def test_use_remembers_newest_first_without_duplicates(self):
        a = os.path.join(self.root, "snapcode")
        b = os.path.join(self.root, "Koza")
        projects.use(self.conf, a)
        projects.use(self.conf, b)
        projects.use(self.conf, a)
        self.assertEqual(self.conf["assistant_recent_dirs"], [a, b])
        self.assertEqual(self.conf["assistant_dir"], a)

    def test_create_makes_a_folder_under_the_first_root(self):
        path = projects.create(self.conf, "Hava Durumu")
        self.assertEqual(path, os.path.join(self.root, "hava-durumu"))
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(self.conf["assistant_dir"], path)


if __name__ == "__main__":
    unittest.main()
