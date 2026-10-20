import sys
import unittest
from unittest import mock

from dikte import ggml


class CudaWhisperBuilds(unittest.TestCase):
    def assets(self, driver):
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(ggml, "_arch", return_value="x64"), \
                mock.patch.object(ggml, "_nvidia_driver", return_value=driver):
            return ggml._wanted_assets(ggml.WHISPER)

    def test_a_current_driver_gets_the_newest_runtime_first(self):
        self.assertEqual(self.assets((560, 94))[:2],
                         ("whisper-bin-win-cuda-12.4.0-x64.zip",
                          "whisper-bin-win-cuda-11.8.0-x64.zip"))

    def test_an_older_driver_gets_only_what_it_can_load(self):
        self.assertEqual(self.assets((531, 18))[0],
                         "whisper-bin-win-cuda-11.8.0-x64.zip")
        self.assertNotIn("whisper-bin-win-cuda-12.4.0-x64.zip", self.assets((531, 18)))

    def test_a_driver_too_old_for_any_falls_back_to_blas(self):
        self.assertEqual(self.assets((512, 74))[0], "whisper-blas-bin-x64.zip")

    def test_no_nvidia_card_falls_back_to_blas(self):
        self.assertEqual(self.assets(None),
                         ("whisper-blas-bin-x64.zip", "whisper-bin-x64.zip"))


if __name__ == "__main__":
    unittest.main()
