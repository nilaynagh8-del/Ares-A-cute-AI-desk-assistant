"""Hands-free orchestrator: wake word -> conversation -> re-arm on silence.

  STANDBY - run the wake detector on the robot's mic. On "Ares" -> ACTIVE.
  ACTIVE  - stream mic to Gemini (back-and-forth). If the user is silent for
            `wake_timeout_s` after Ares finishes talking -> back to STANDBY.

The robot streams 16 kHz / 16-bit / mono PCM - what the detector and Gemini both
want. Echo is handled by GeminiVoiceSession's half-duplex, and we don't time out
while Ares is still speaking.
"""
import math
import queue
import struct
import threading
import time

import sounds
import wakeword
from gemini_live import GeminiVoiceSession


class Assistant:
    def __init__(self, cfg, stream, on_state=None, on_log=None):
        self.cfg = cfg
        self.stream = stream
        self.on_state = on_state or (lambda s: None)
        self.on_log = on_log or (lambda m: None)
        self.mic_q = queue.Queue(maxsize=200)
        self.gemini_q = None
        self.session = None
        self.wake = None
        self.state = "off"                       # off / standby / active
        self._stop = threading.Event()
        self._worker = None
        self._last_active = 0.0
        self.timeout_s = float(cfg.get("wake_timeout_s", 5.0))
        self.vad_thresh = float(cfg.get("vad_threshold", 600))

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        if not self.stream.connected:
            self.on_log("Connect to the robot first (it's the mic).")
            return False
        try:
            self.wake = wakeword.make_detector(self.cfg, self.on_log)
        except Exception as e:  # noqa: BLE001
            self.on_log(f"Wake word init failed: {e}")
            return False

        self._stop.clear()
        self.stream.set_sink(self.mic_q)
        self._set_state("standby")
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()
        self.on_log("Hands-free on - say \"Ares\".")
        return True

    def stop(self):
        self._stop.set()
        self._end_session()
        if self.wake:
            self.wake.close()
            self.wake = None
        self.stream.set_sink(None)
        self.state = "off"
        self.on_state("idle")

    @property
    def running(self):
        return self.state != "off"

    # ---- internals -------------------------------------------------------
    def _set_state(self, s):
        self.state = s
        if s == "standby":
            self.on_state("idle")

    def _bot_playing(self):
        p = self.session.player if self.session else None
        return bool(p and p.pending_bytes() > 0)

    @staticmethod
    def _rms_bytes(b):
        n = len(b) // 2
        if not n:
            return 0
        s = struct.unpack("<%dh" % n, b[:n * 2])
        return math.sqrt(sum(x * x for x in s) / n)

    def _wake(self):
        self.on_log("Heard \"Ares\" - listening.")
        sounds.play_ack(self.cfg.get("output_device"),
                        self.cfg.get("ack_volume", 0.45))   # little "mhm?"
        self.gemini_q = queue.Queue(maxsize=200)
        self.session = GeminiVoiceSession(
            self.cfg, self.gemini_q, on_state=self.on_state, on_log=self.on_log)
        self.session.start()
        self.state = "active"
        self._last_active = time.time()

    def _end_session(self):
        if self.session:
            self.session.stop()
            self.session = None
        self.gemini_q = None

    def _check_timeout(self):
        if self.state != "active":
            return
        if self._bot_playing():
            self._last_active = time.time()
            return
        if time.time() - self._last_active > self.timeout_s:
            self.on_log("Quiet for a bit - say \"Ares\" to wake me.")
            self._end_session()
            self._set_state("standby")

    def _loop(self):
        while not self._stop.is_set():
            try:
                chunk = self.mic_q.get(timeout=0.1)
            except queue.Empty:
                self._check_timeout()
                continue
            if self.state == "standby":
                try:
                    if self.wake.feed(chunk):
                        self._wake()
                except Exception as e:  # noqa: BLE001
                    self.on_log(f"wake error: {e}")
            elif self.state == "active":
                if self.gemini_q is not None:
                    try:
                        self.gemini_q.put_nowait(chunk)
                    except queue.Full:
                        pass
                if not self._bot_playing() and self._rms_bytes(chunk) > self.vad_thresh:
                    self._last_active = time.time()
            self._check_timeout()
