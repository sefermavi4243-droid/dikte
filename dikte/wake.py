"""Listening for "Claude" without a key press.

The microphone is kept open in the background and cut into utterances by
loudness: an utterance starts when the level rises clearly above the room's
own noise floor and ends after a short silence. Each one is transcribed, and
only one that starts with the wake word goes anywhere:

  "Claude, add a dark mode to the settings page"   the rest is the command
  "Claude Code"                                    listening; the next
                                                   utterance is the command
  "Claude, stop" / "Claude dur"                    the reading or the agent
                                                   is called off

After an answer the listener can stay armed for a few seconds, so a follow-up
needs no wake word: that is the conversation mode. Saying "thanks" or "tamam bu
kadar" in that window ends it.

There is no keyword model here. Speech models already in use transcribe the
name reliably once they are told to expect it (the prompt carries "Claude
Code"), and a dedicated wake-word engine would mean a native dependency and a
trained model per language. The cost is that every utterance in the room is
transcribed; with a local model that is CPU or GPU time, never a bill.

Dictation and the agent's own recording each open the microphone themselves.
The listener is suspended while they do, so a dictation that happens to start
with "Claude" is not taken for a command.
"""

import collections
import os
import queue
import re
import subprocess
import threading
import time
import unicodedata

from PyQt6.QtCore import QObject, pyqtSignal

from . import api
from . import audio
from . import vad
from .i18n import t

CHUNK_SECONDS = audio.CHUNK_FRAMES / audio.RATE

# Utterance edges, in chunks of CHUNK_SECONDS (64 ms).
START_CHUNKS = 3          # this many loud chunks in a row open an utterance
END_CHUNKS = 12           # this many quiet ones close it (~0.8 s)
PREROLL_CHUNKS = 8        # kept from before the start, so the first word is whole
MAX_SECONDS = 20
MIN_VOICED_SECONDS = 0.35
ABSOLUTE_FLOOR_DB = -55   # nothing quieter than this is speech, however quiet the room

# Spelled the ways speech models write the name when it is said in Turkish or
# English. "cloud" alone is too common a word to wake on, so it only counts
# with "code" after it.
_NAME = r"(?:claude|claud|clode|cloude|klod|klot|kloud|klaud|klood|clawed|klot)"
_WAKE = re.compile(
    rf"^(?:(?:hey|hi|ey|hay|okay|ok|tamam|evet)\s+)?"
    rf"(?:{_NAME}(?:\s+(?:code|kod|kot|cod|kodu|codu))?|(?:cloud|clot|klaut)\s+(?:code|kod|kot|cod))"
    rf"\b\s*(?P<rest>.*)$"
)
_STOP = {"dur", "durdur", "sus", "stop", "iptal", "cancel", "yeter", "kes",
         "be quiet", "shut up", "vazgec"}
_DONE = {"tesekkurler", "tesekkur ederim", "sagol", "sag ol", "tamam bu kadar",
         "bu kadar", "tamamdir", "thanks", "thank you", "thats all", "that is all",
         "nothing else", "baska bir sey yok", "gorusuruz"}

PROMPT = "Claude Code, "


def fold(text):
    """Lowercase ASCII words, for matching what a speech model wrote."""
    text = (text or "").lower().replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_wake(text):
    """(True, rest of the sentence) when it starts with the wake word.

    The rest keeps its original spelling and punctuation; only the wake word
    and the comma after it are cut off.
    """
    folded = fold(text)
    found = _WAKE.match(folded)
    if not found:
        return False, ""
    rest_words = len(found.group("rest").split())
    words = re.findall(r"\S+", text or "")
    rest = " ".join(words[len(words) - rest_words:]) if rest_words else ""
    return True, rest.lstrip(" ,.;:!-").strip()


def is_stop(text):
    return fold(text) in _STOP


def is_done(text):
    return fold(text) in _DONE


class Segmenter:
    """Cuts a stream of chunks into utterances by loudness.

    feed() returns (pcm, rms values) once an utterance has ended, else None.
    The noise floor follows the room: it drifts towards every quiet chunk, and
    rises only slowly, so a long utterance does not lift it into the speech.
    """

    def __init__(self, margin_db=12.0):
        self.margin_db = margin_db
        self.noise_db = None
        self._preroll = collections.deque(maxlen=PREROLL_CHUNKS)
        self._loud_run = []
        self._speech = None
        self._rms = []
        self._quiet = 0
        self._max_chunks = int(MAX_SECONDS / CHUNK_SECONDS)

    @property
    def in_speech(self):
        return self._speech is not None

    def reset(self):
        self._preroll.clear()
        self._loud_run = []
        self._speech = None
        self._rms = []
        self._quiet = 0

    def _update_noise(self, db, loud):
        if self.noise_db is None:
            self.noise_db = db
        elif db < self.noise_db:
            self.noise_db += (db - self.noise_db) * 0.3
        elif not loud:
            self.noise_db += (db - self.noise_db) * 0.02

    def feed(self, chunk, rms):
        db = vad.to_db(rms)
        gate = max((self.noise_db if self.noise_db is not None else db)
                   + self.margin_db, ABSOLUTE_FLOOR_DB)
        loud = db >= gate
        self._update_noise(db, loud or self._speech is not None)

        if self._speech is None:
            if loud:
                self._loud_run.append((chunk, rms))
                if len(self._loud_run) >= START_CHUNKS:
                    self._speech = bytearray(b"".join(c for c, _ in self._preroll))
                    self._rms = [r for _, r in self._preroll]
                    for c, r in self._loud_run:
                        self._speech.extend(c)
                        self._rms.append(r)
                    self._loud_run = []
                    self._quiet = 0
                return None
            # A blip shorter than START_CHUNKS goes back into the preroll.
            for item in self._loud_run:
                self._preroll.append(item)
            self._loud_run = []
            self._preroll.append((chunk, rms))
            return None

        self._speech.extend(chunk)
        self._rms.append(rms)
        self._quiet = 0 if db >= gate - 3 else self._quiet + 1
        if self._quiet >= END_CHUNKS or len(self._rms) >= self._max_chunks:
            pcm, values = bytes(self._speech), self._rms
            self.reset()
            voiced = sum(1 for v in values if vad.to_db(v) >= gate) * CHUNK_SECONDS
            if voiced < MIN_VOICED_SECONDS:
                return None
            return pcm, values
        return None


class WakeListener(QObject):
    heard = pyqtSignal(str, str, float)  # command, wav path, duration
    woke = pyqtSignal()                  # the wake word alone: listening now
    stop_requested = pyqtSignal()
    conversation_ended = pyqtSignal()
    armed_changed = pyqtSignal(bool)
    level = pyqtSignal(float)
    failed = pyqtSignal(str)

    def __init__(self, conf, parent=None):
        super().__init__(parent)
        self.conf = conf
        self._proc = None
        self._thread = None
        self._worker = None
        self._jobs = queue.Queue(maxsize=3)
        self._running = False
        self._suspended = False
        self._armed_until = 0.0
        self._armed_flag = False
        self.speaking = lambda: False  # replaced by the app: is an answer being read?

    @property
    def running(self):
        return self._running

    @property
    def armed(self):
        return time.monotonic() < self._armed_until

    def arm(self, seconds):
        """Take the next utterance as a command, wake word or not."""
        if seconds <= 0:
            return
        self._armed_until = time.monotonic() + float(seconds)
        if not self._armed_flag:
            self._armed_flag = True
            self.armed_changed.emit(True)

    def disarm(self):
        self._armed_until = 0.0
        self._expired()

    def _expired(self):
        if self._armed_flag:
            self._armed_flag = False
            self.armed_changed.emit(False)

    def suspend(self, value=True):
        """Ignore the microphone while something else is recording from it."""
        self._suspended = bool(value)

    def start(self):
        if self._running:
            return
        try:
            cmd = audio.recording_command(self.conf["mic_target"])
        except audio.AudioDeviceError as exc:
            self.failed.emit(str(exc))
            return
        if not cmd:
            self.failed.emit(audio.sound().missing)
            return
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, bufsize=0, creationflags=audio.NO_WINDOW,
            )
        except OSError as exc:
            self.failed.emit(str(exc))
            return
        self._running = True
        self._thread = threading.Thread(target=self._pump, daemon=True,
                                        args=(self._proc,))
        self._thread.start()
        self._worker = threading.Thread(target=self._transcribe_loop, daemon=True)
        self._worker.start()

    def stop(self):
        self._running = False
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass
        self.disarm()

    # ---- capture ---------------------------------------------------------

    def _pump(self, proc):
        segmenter = Segmenter(self.conf["wake_margin_db"])
        was_suspended = False
        while self._running and proc is self._proc:
            chunk = audio._read_exact(proc.stdout, audio.CHUNK_BYTES)
            if not chunk:
                break
            if self._suspended:
                if not was_suspended:
                    segmenter.reset()
                was_suspended = True
                continue
            was_suspended = False
            peak, rms = audio.chunk_levels(chunk)
            if self.armed:
                self.level.emit(peak)
                if segmenter.in_speech:
                    # Still talking when the window closes is still the answer.
                    self._armed_until = max(self._armed_until,
                                            time.monotonic() + 0.5)
            elif self._armed_flag and not segmenter.in_speech:
                self._expired()
            found = segmenter.feed(chunk, rms)
            if found is None:
                continue
            pcm, values = found
            try:
                self._jobs.put_nowait((pcm, values, self.armed))
            except queue.Full:
                pass  # falling behind; the newest speech waits for the next gap
        if self._running and proc is self._proc:
            self._running = False
            self.failed.emit(t("The background microphone stopped."))

    # ---- transcription -----------------------------------------------------

    def _transcribe_loop(self):
        while True:
            job = self._jobs.get()
            if job is None or not self._running:
                return
            pcm, values, was_armed = job
            try:
                self._handle(pcm, values, was_armed)
            except Exception as exc:  # noqa: BLE001 - keep listening whatever one clip does
                print(f"dikte: wake listener: {exc}")

    def _handle(self, pcm, values, was_armed):
        if self._suspended:
            return
        duration = len(pcm) / (audio.RATE * audio.SAMPLE_WIDTH)
        path = audio.write_wav(pcm)
        keep = False
        try:
            language = self.conf["language"]
            prompt = PROMPT + (self.conf["transcribe_prompt"] or "")
            try:
                text = api.transcribe(self.conf.transcribe_target(), path,
                                      language="" if language == "auto" else language,
                                      prompt=prompt.strip(), timeout=60)
            except api.ApiError:
                return
            if vad.looks_like_hallucination(text, duration):
                return

            woke, rest = split_wake(text)
            armed = was_armed or self.armed
            if woke and (is_stop(rest) or not rest and self.speaking()):
                self.disarm()
                self.stop_requested.emit()
                return
            if armed and not woke:
                if is_done(text):
                    self.disarm()
                    self.conversation_ended.emit()
                    return
                if is_stop(text):
                    self.disarm()
                    self.stop_requested.emit()
                    return
                if self.speaking():
                    return  # most likely the answer itself, heard back
                rest = text.strip()
            elif not woke:
                return
            elif not rest:
                self.arm(self.conf["wake_follow_seconds"])
                self.woke.emit()
                return
            self.disarm()
            keep = True
            self.heard.emit(rest, path, duration)
        finally:
            if not keep:
                try:
                    os.unlink(path)
                except OSError:
                    pass

