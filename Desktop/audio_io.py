"""Microphone capture and speaker playback via sounddevice (PortAudio).

Gemini Live wants 16 kHz / 16-bit / mono PCM in, and sends 24 kHz / 16-bit /
mono PCM out. These helpers expose that as simple byte streams plus device
enumeration so the UI can let the user pick input/output devices (including
Bluetooth headphones/speakers paired to the PC).
"""
import threading

import numpy as np
import sounddevice as sd

IN_RATE = 16000
OUT_RATE = 24000

voice_volume = 1.0      # 0..1 scale on Ares's speech playback (set by a tool)


def input_devices():
    """[(index, name), ...] of devices that can record."""
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append((i, d["name"]))
    return out


def output_devices():
    """[(index, name), ...] of devices that can play."""
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            out.append((i, d["name"]))
    return out


class MicCapture:
    """Streams raw 16 kHz int16 mono bytes to on_chunk(bytes)."""

    def __init__(self, device=None, on_chunk=None, blocksize=1600):
        self.device = device
        self.on_chunk = on_chunk
        self.blocksize = blocksize  # 1600 frames = 100 ms
        self.stream = None

    def _cb(self, indata, frames, time_info, status):
        if self.on_chunk:
            self.on_chunk(bytes(indata))

    def start(self):
        self.stream = sd.RawInputStream(
            samplerate=IN_RATE, channels=1, dtype="int16",
            device=self.device, blocksize=self.blocksize, callback=self._cb,
        )
        self.stream.start()

    def stop(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None


class SpeakerPlayer:
    """Buffered 24 kHz int16 mono playback. write() is thread-safe."""

    def __init__(self, device=None):
        self.device = device
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.stream = None

    def _cb(self, outdata, frames, time_info, status):
        need = frames * 2  # int16 mono -> 2 bytes/frame
        with self.lock:
            n = min(need, len(self.buf))
            chunk = bytes(self.buf[:n]) if n else b""
            if n:
                del self.buf[:n]
        if n and voice_volume != 1.0:
            a = np.frombuffer(chunk, dtype="<i2").astype(np.float32) * voice_volume
            np.clip(a, -32768, 32767, out=a)
            chunk = a.astype("<i2").tobytes()
        if n:
            outdata[:n] = chunk
        if n < need:
            outdata[n:] = b"\x00" * (need - n)

    def start(self):
        self.stream = sd.RawOutputStream(
            samplerate=OUT_RATE, channels=1, dtype="int16",
            device=self.device, callback=self._cb,
        )
        self.stream.start()

    def write(self, data):
        with self.lock:
            self.buf.extend(data)

    def pending_bytes(self):
        with self.lock:
            return len(self.buf)

    def flush(self):
        """Drop queued audio - used when the user interrupts the model."""
        with self.lock:
            self.buf.clear()

    def stop(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
