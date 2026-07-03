"""Little non-verbal acknowledgment sounds Ares makes when it hears its name.

Synthesized soft hums ("mhm?", "hm?", "mm!") with random pitch + style so it
varies and feels alive. Plays locally (no Gemini), so it works even offline.
"""
import random
import threading

import numpy as np
import sounddevice as sd

SR = 24000


def _env(n, attack=0.018, release=0.10):
    a, r = int(SR * attack), int(SR * release)
    e = np.ones(n)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] = np.linspace(1, 0, r)
    return e


def _hum(f0, f1, dur, bumps=2):
    """A warm hum gliding f0->f1 with `bumps` amplitude humps (the m-hm shape)."""
    n = int(SR * dur)
    pitch = np.linspace(f0, f1, n)
    phase = np.cumsum(2 * np.pi * pitch / SR)
    tone = np.sin(phase) + 0.30 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase)
    hump = 0.12 + 0.88 * np.abs(np.sin(np.linspace(0, np.pi * bumps, n)))
    return tone * hump * _env(n)


# style -> waveform given a pitch multiplier p
_STYLES = [
    lambda p: _hum(285 * p, 248 * p, 0.42, 2),   # "mhm"  affirmative, falls
    lambda p: _hum(250 * p, 322 * p, 0.40, 2),   # "hm?"  questioning, rises
    lambda p: _hum(300 * p, 300 * p, 0.22, 1),   # "mm"   short
    lambda p: _hum(240 * p, 300 * p, 0.55, 3),   # "mhmm" longer, thoughtful
    lambda p: _hum(330 * p, 372 * p, 0.18, 1),   # quick perky blip
    lambda p: _hum(262 * p, 240 * p, 0.30, 1),   # soft low "mm"
]


def make_ack(volume=0.45):
    p = random.uniform(0.9, 1.12)                # pitch varies each time
    w = random.choice(_STYLES)(p)
    w = w / (np.max(np.abs(w)) + 1e-6) * volume
    return w.astype(np.float32)


def play_ack(device=None, volume=0.45):
    def _go():
        try:
            sd.play(make_ack(volume), SR, device=device)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()
