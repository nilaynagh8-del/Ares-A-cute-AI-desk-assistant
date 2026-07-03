"""Wake-word detection. Two engines, same feed(pcm_bytes) -> bool interface.

  VoskWake (default):  a small local speech recognizer that listens for "Ares".
                       No signup, no training. First use downloads a ~40 MB model.
  PorcupineWake:       optional - if you have a Picovoice key + custom Ares.ppn.

Both expect 16 kHz / 16-bit / mono PCM (exactly what the robot's mic streams).
"""
import json
import struct

try:
    import pvporcupine
except ImportError:
    pvporcupine = None


class PorcupineWake:
    def __init__(self, access_key, keyword_path, sensitivity=0.6):
        if not pvporcupine:
            raise RuntimeError("pvporcupine not installed")
        self.handle = pvporcupine.create(
            access_key=access_key, keyword_paths=[keyword_path],
            sensitivities=[sensitivity])
        self.frame_length = self.handle.frame_length
        self._fmt = "<%dh" % self.frame_length
        self._buf = bytearray()

    def feed(self, pcm_bytes):
        self._buf.extend(pcm_bytes)
        flen = self.frame_length * 2
        hit = False
        while len(self._buf) >= flen:
            fb = bytes(self._buf[:flen])
            del self._buf[:flen]
            if self.handle.process(struct.unpack(self._fmt, fb)) >= 0:
                hit = True
        return hit

    def close(self):
        if self.handle:
            self.handle.delete()
            self.handle = None


class VoskWake:
    # "Ares" and the ways a recognizer commonly hears it
    TARGETS = {"ares", "aries", "arius", "aris", "arias", "eris", "arrives"}

    def __init__(self):
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        self.model = Model(lang="en-us")
        self.rec = KaldiRecognizer(self.model, 16000)

    def feed(self, pcm_bytes):
        if self.rec.AcceptWaveform(pcm_bytes):
            text = json.loads(self.rec.Result()).get("text", "")
        else:
            text = json.loads(self.rec.PartialResult()).get("partial", "")
        if any(w in self.TARGETS for w in text.lower().split()):
            self.rec.Reset()
            return True
        return False

    def close(self):
        pass


def make_detector(cfg, on_log=lambda m: None):
    key = cfg.get("porcupine_access_key", "").strip()
    kw = cfg.get("porcupine_keyword_path", "").strip()
    if key and kw and pvporcupine:
        on_log("Wake word: Porcupine (custom 'Ares').")
        return PorcupineWake(key, kw)
    on_log("Wake word: local 'Ares' detection (Vosk). First run downloads ~40 MB...")
    return VoskWake()
