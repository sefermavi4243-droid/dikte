import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from dikte import paths


class Command(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dikte-shim-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def shim(self, body):
        path = os.path.join(self.root, "tool.cmd")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def resolve(self, cmd, found, node=None):
        def which(name, *args, **kwargs):
            return {"tool": found, "node": node}.get(name)
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(paths.shutil, "which", which):
            return paths.command(cmd)

    def test_an_npm_shim_runs_the_exe_it_names(self):
        exe = os.path.join(self.root, "node_modules", "pkg", "bin", "tool.exe")
        os.makedirs(os.path.dirname(exe))
        open(exe, "w").close()
        shim = self.shim('@ECHO off\n' r'"%dp0%\node_modules\pkg\bin\tool.exe"   %*')
        self.assertEqual(self.resolve(["tool", "-p", "a\nb"], shim),
                         [exe, "-p", "a\nb"])

    def test_a_node_shim_runs_node_with_the_script(self):
        script = os.path.join(self.root, "node_modules", "pkg", "bin", "tool.js")
        os.makedirs(os.path.dirname(script))
        open(script, "w").close()
        shim = self.shim(r'"%_prog%"  "%dp0%\node_modules\pkg\bin\tool.js" %*')
        self.assertEqual(self.resolve(["tool", "x"], shim, node=r"C:\node.exe"),
                         [r"C:\node.exe", script, "x"])

    def test_an_exe_is_run_by_its_full_path(self):
        self.assertEqual(self.resolve(["tool", "x"], r"C:\bin\tool.exe"),
                         [r"C:\bin\tool.exe", "x"])

    def test_an_unknown_program_is_left_alone(self):
        self.assertEqual(self.resolve(["tool", "x"], None), ["tool", "x"])

    def test_other_systems_are_left_alone(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(paths.command(["claude", "-p"]), ["claude", "-p"])


if __name__ == "__main__":
    unittest.main()
