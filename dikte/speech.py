"""Reading the agent's answer out loud.

Two engines, because neither covers everyone:

  windows   The OneCore voices Windows ships, reached through WinRT from a
            short PowerShell script. Offline, and present on every Windows 10
            and 11 machine with the language pack installed (Turkish brings
            "Tolga"). The older SAPI voices that System.Speech lists are a
            separate, smaller set, which is why WinRT is asked instead.
  edge      Microsoft's neural voices through the `edge-tts` package. Much more
            natural, needs the network and `pip install edge-tts`.

On macOS and Linux the first engine falls back to `say` and `spd-say`, so the
setting means something everywhere even though Windows is what it was made for.

The text is shaped for the ear before it is spoken: code blocks, links and
markdown read badly or not at all, and an answer that runs to a page is cut at
a sentence boundary instead of read for a minute.
"""

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

from PyQt6.QtCore import QObject, pyqtSignal

from . import paths

NO_WINDOW = paths.NO_WINDOW

ENGINES = ("windows", "edge")

# Neural voices picked per speech language when none is chosen. Edge has many
# more; these are the ones that read technical Turkish and English best.
EDGE_VOICES = {"tr": "tr-TR-EmelNeural", "en": "en-US-AriaNeural"}

_WINRT_SCRIPT = r"""
$textFile, $out, $voice, $rate = $env:DIKTE_TTS_TEXT, $env:DIKTE_TTS_OUT, $env:DIKTE_TTS_VOICE, $env:DIKTE_TTS_RATE
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader,Windows.Storage.Streams,ContentType=WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
$text = [IO.File]::ReadAllText($textFile, [Text.Encoding]::UTF8)
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$chosen = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object {
    $_.DisplayName -eq $voice -or $_.Language -like "$voice*" } | Select-Object -First 1
if ($chosen) { $synth.Voice = $chosen }
$synth.Options.SpeakingRate = [double]$rate
$stream = Await ($synth.SynthesizeTextToStreamAsync($text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
$reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
$size = [uint32]$stream.Size
$null = Await ($reader.LoadAsync($size)) ([uint32])
$bytes = New-Object byte[] $size
$reader.ReadBytes($bytes)
[IO.File]::WriteAllBytes($out, $bytes)
"""

_LIST_SCRIPT = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
[Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | ForEach-Object {
    $_.DisplayName + "`t" + $_.Language }
"""

_FENCE = re.compile(r"```.*?(```|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_MARKS = re.compile(r"^\s{0,3}(#{1,6}|[-*+]|\d+[.)])\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_SPACES = re.compile(r"[ \t]+")
_SENTENCE_END = re.compile(r"[.!?…](\s|$)")


def for_speech(text, max_chars=600, code_word="code block"):
    """Markdown and links out, length capped at a sentence boundary."""
    text = _FENCE.sub(f" {code_word}. ", text or "")
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _MARKS.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        ends = list(_SENTENCE_END.finditer(cut))
        text = cut[:ends[-1].end()].strip() if ends else cut.rsplit(" ", 1)[0] + "…"
    return text


def _powershell(script):
    """A PowerShell command line carrying the script itself.

    Handed over encoded rather than written to a file: the Microsoft Store build
    of Python redirects its writes under AppData into a private copy, and the
    PowerShell it starts would look for the file in the real folder.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


def windows_voices():
    """[(display name, language)] for every OneCore voice, or [] off Windows."""
    if sys.platform != "win32":
        return []
    try:
        result = subprocess.run(
            _powershell(_LIST_SCRIPT), capture_output=True, timeout=15, check=False, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    voices = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        name, _, language = line.strip().partition("\t")
        if name:
            voices.append((name, language))
    return voices


def edge_available():
    return shutil.which("edge-tts") is not None or _edge_module()


def _edge_module():
    try:
        import importlib.util
        return importlib.util.find_spec("edge_tts") is not None
    except (ImportError, ValueError):
        return False


class Speaker(QObject):
    """One answer spoken at a time; a new one or stop() cuts the last short."""

    started = pyqtSignal()
    finished = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._generation = 0
        self._proc = None
        self._speaking = False

    @property
    def speaking(self):
        return self._speaking

    def speak(self, text, engine="windows", voice="", language="tr", rate=1.0):
        text = (text or "").strip()
        if not text:
            return
        self.stop()
        with self._lock:
            self._generation += 1
            generation = self._generation
        self._speaking = True
        threading.Thread(
            target=self._run, daemon=True,
            args=(generation, text, engine, voice, language, rate),
        ).start()

    def stop(self):
        with self._lock:
            self._generation += 1
            proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        if self._speaking:
            self._speaking = False
            self.finished.emit()

    def _current(self, generation):
        return generation == self._generation

    def _spawn(self, generation, cmd, env=None):
        """Run one step of the job; None once a newer job has taken over."""
        with self._lock:
            if not self._current(generation):
                return None
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=NO_WINDOW,
                env=None if env is None else {**os.environ, **env},
            )
            proc = self._proc
        _, err = proc.communicate()
        if proc.returncode != 0 and self._current(generation):
            raise RuntimeError(err.decode("utf-8", "replace").strip().splitlines()[-1:]
                               or f"exit code {proc.returncode}")
        return proc

    def _run(self, generation, text, engine, voice, language, rate):
        folder = tempfile.mkdtemp(prefix="dikte-tts-")
        try:
            if self._current(generation):
                self.started.emit()
            if engine == "edge":
                self._speak_edge(generation, text, voice, language, rate, folder)
            elif sys.platform == "win32":
                self._speak_windows(generation, text, voice, language, rate, folder)
            else:
                self._speak_native(generation, text, rate)
        except Exception as exc:  # noqa: BLE001 - the thread must report, not die
            if self._current(generation):
                detail = exc.args[0] if exc.args else exc
                if isinstance(detail, list):
                    detail = " ".join(detail)
                self.failed.emit(str(detail))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            if self._current(generation) and self._speaking:
                self._speaking = False
                self.finished.emit()

    def _speak_windows(self, generation, text, voice, language, rate, folder):
        import winsound
        text_file = os.path.join(folder, "text.txt")
        wav = os.path.join(folder, "speech.wav")
        with open(text_file, "w", encoding="utf-8") as fh:
            fh.write(text)
        if self._spawn(generation, _powershell(_WINRT_SCRIPT), env={
            "DIKTE_TTS_TEXT": text_file, "DIKTE_TTS_OUT": wav,
            "DIKTE_TTS_VOICE": voice or language, "DIKTE_TTS_RATE": str(rate),
        }) is None or not self._current(generation):
            return
        with wave.open(wav, "rb") as fh:
            seconds = fh.getnframes() / float(fh.getframerate() or 1)
        # Played asynchronously and waited out here: a synchronous PlaySound
        # cannot be purged from another thread, so stop() would not cut it.
        winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_NODEFAULT
                           | winsound.SND_ASYNC)
        end = time.monotonic() + seconds + 0.2
        while time.monotonic() < end and self._current(generation):
            time.sleep(0.05)

    def _speak_edge(self, generation, text, voice, language, rate, folder):
        mp3 = os.path.join(folder, "speech.mp3")
        text_file = os.path.join(folder, "text.txt")
        with open(text_file, "w", encoding="utf-8") as fh:
            fh.write(text)
        voice = voice or EDGE_VOICES.get(language, EDGE_VOICES["en"])
        percent = f"{round((float(rate) - 1) * 100):+d}%"
        base = (["edge-tts"] if shutil.which("edge-tts")
                else [sys.executable, "-m", "edge_tts"])
        if self._spawn(generation, base + [
            "--voice", voice, "--rate", percent, "-f", text_file,
            "--write-media", mp3,
        ]) is None:
            return
        player = shutil.which("ffplay")
        if not player:
            raise RuntimeError("ffplay not found. Install ffmpeg.")
        self._spawn(generation, [player, "-nodisp", "-autoexit",
                                 "-loglevel", "error", mp3])

    def _speak_native(self, generation, text, rate):
        if sys.platform == "darwin":
            cmd = ["say", "-r", str(round(180 * float(rate))), text]
        elif shutil.which("spd-say"):
            cmd = ["spd-say", "-w", "-r", str(round((float(rate) - 1) * 100)), text]
        else:
            raise RuntimeError("No speech synthesizer found (spd-say).")
        self._spawn(generation, cmd)
