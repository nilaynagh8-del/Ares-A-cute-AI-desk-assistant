"""Configuration load/save for the Robot Companion app.

Settings live in config.json next to this file. The Gemini key can also come
from a .env file or the GEMINI_API_KEY environment variable (env wins).
"""
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
ENV_PATH = BASE / ".env"

DEFAULTS = {
    "gemini_api_key": "",
    "model": "gemini-3.1-flash-live-preview",
    "api_version": "v1beta",
    "voice": "Puck",
    "system_prompt": (
        "You are a small, friendly desk robot with two big glowing eyes. "
        "Keep replies short, warm and natural - you're a companion, not a "
        "search engine. Speak conversationally."
    ),
    "input_device": None,    # sounddevice index, or None for default
    "output_device": None,   # sounddevice index, or None for default
    "device_port": "",       # COM port of the ESP32 (e.g. COM8)
    "device_ip": "",         # robot's WiFi IP (remembered between runs)
    "porcupine_access_key": "",      # from console.picovoice.ai (free)
    "porcupine_keyword_path": "",    # path to your Ares.ppn (Windows) file
    "wake_timeout_s": 5.0,           # re-arm wake word after this much silence
    "vad_threshold": 600,            # mic RMS above this = "user is talking"
    "ack_volume": 0.45,              # volume of the little "mhm?" wake sound (0..1)
    "voice_volume": 1.0,             # Ares's speech playback volume (0..1)
}


def _load_env_file():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def load():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    _load_env_file()
    if os.environ.get("GEMINI_API_KEY"):
        cfg["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
    return cfg


def save(cfg):
    # never persist a key that came from the environment back into the file
    to_save = dict(cfg)
    if os.environ.get("GEMINI_API_KEY") and \
       to_save.get("gemini_api_key") == os.environ["GEMINI_API_KEY"]:
        to_save["gemini_api_key"] = ""
    CONFIG_PATH.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
