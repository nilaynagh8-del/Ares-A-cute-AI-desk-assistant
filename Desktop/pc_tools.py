"""PC actions Ares can take (Gemini function-calling backend).

Safe-by-design: it can OPEN apps / websites / files and remember named
shortcuts. It deliberately cannot delete things or run arbitrary shell
commands. Shortcuts persist in shortcuts.json next to this file.
"""
import json
import os
import pathlib
import subprocess
import webbrowser

from google.genai import types

SHORTCUTS_PATH = pathlib.Path(__file__).resolve().parent / "shortcuts.json"

# friendly name -> launch token (Windows resolves most via App Paths / PATH)
KNOWN_APPS = {
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge",
    "firefox": "firefox", "notepad": "notepad", "calculator": "calc",
    "calc": "calc", "paint": "mspaint", "explorer": "explorer",
    "file explorer": "explorer", "files": "explorer", "settings": "ms-settings:",
    "spotify": "spotify", "discord": "discord", "terminal": "wt", "cmd": "cmd",
    "task manager": "taskmgr", "word": "winword", "excel": "excel",
    "powerpoint": "powerpnt", "outlook": "outlook", "vs code": "code",
    "vscode": "code", "code": "code", "steam": "steam", "obs": "obs",
}


# ---- actions -------------------------------------------------------------
def open_app(app):
    token = KNOWN_APPS.get(app.strip().lower(), app)
    try:
        p = pathlib.Path(os.path.expanduser(token))
        if p.exists():
            os.startfile(str(p))
        else:
            subprocess.Popen(f'start "" "{token}"', shell=True)
        return f"Opened {app}."
    except Exception as e:  # noqa: BLE001
        return f"Couldn't open {app}: {e}"


def open_url(url):
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    webbrowser.open(u)
    return f"Opened {u}."


def open_file(name):
    target = os.path.expanduser(name.strip())
    p = pathlib.Path(target)
    if p.exists():
        os.startfile(str(p))
        return f"Opened {p}."
    home = pathlib.Path.home()
    for base in (home / "Desktop", home / "Documents", home / "Downloads", home):
        if not base.exists():
            continue
        for f in base.glob("**/*" + pathlib.Path(name).name + "*"):
            if f.is_file():
                os.startfile(str(f))
                return f"Opened {f}."
    return f"Couldn't find a file named '{name}'."


def _load():
    if SHORTCUTS_PATH.exists():
        try:
            return json.loads(SHORTCUTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(s):
    SHORTCUTS_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")


def remember_shortcut(name, kind, target):
    s = _load()
    s[name.strip().lower()] = {"kind": kind.strip().lower(), "target": target}
    _save(s)
    return f"Got it - '{name}' now opens {target}."


def open_shortcut(name):
    item = _load().get(name.strip().lower())
    if not item:
        return f"I don't have a shortcut called '{name}' yet."
    kind, target = item.get("kind", "app"), item.get("target", "")
    if kind == "url":
        return open_url(target)
    if kind == "file":
        return open_file(target)
    return open_app(target)


def _parse_level(s):
    s = str(s).strip().lower().rstrip("%").strip()
    words = {"mute": 0.0, "muted": 0.0, "min": 0.0, "off": 0.0, "silent": 0.0,
             "max": 1.0, "maximum": 1.0, "full": 1.0,
             "half": 0.5, "halfway": 0.5, "medium": 0.5,
             "quiet": 0.2, "low": 0.25, "loud": 0.9, "high": 0.85}
    if s in words:
        return words[s]
    import re
    m = re.search(r"\d+\.?\d*", s)
    v = float(m.group()) if m else 50.0
    if v > 1:
        v /= 100.0
    return max(0.0, min(1.0, v))


def set_voice_volume(level):
    import audio_io
    v = _parse_level(level)
    audio_io.voice_volume = v
    try:
        import config
        c = config.load()
        c["voice_volume"] = v
        config.save(c)
    except Exception:
        pass
    return f"Okay, my voice is now at {int(round(v * 100))}%."


def set_pc_volume(level):
    v = _parse_level(level)
    try:
        from pycaw.pycaw import AudioUtilities
        AudioUtilities.GetSpeakers().EndpointVolume.SetMasterVolumeLevelScalar(v, None)
        return f"Set the PC volume to {int(round(v * 100))}%."
    except Exception as e:  # noqa: BLE001
        return f"Couldn't change the PC volume: {e}"


def get_current_time(*_):
    import datetime
    now = datetime.datetime.now()
    h = now.hour % 12 or 12
    return f"It's {now.strftime('%A, %B %d, %Y')}, {h}:{now.strftime('%M %p')}."


# ---- Gemini dispatch -----------------------------------------------------
_FUNCS = {
    "open_app": lambda a: open_app(a["app"]),
    "open_url": lambda a: open_url(a["url"]),
    "open_file": lambda a: open_file(a["name"]),
    "remember_shortcut": lambda a: remember_shortcut(a["name"], a.get("kind", "app"), a["target"]),
    "open_shortcut": lambda a: open_shortcut(a["name"]),
    "set_voice_volume": lambda a: set_voice_volume(a["level"]),
    "set_pc_volume": lambda a: set_pc_volume(a["level"]),
    "get_current_time": lambda a: get_current_time(),
}


def execute(name, args):
    fn = _FUNCS.get(name)
    if not fn:
        return {"error": f"unknown function {name}"}
    try:
        return {"result": fn(args or {})}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _obj(props, required):
    return types.Schema(type="OBJECT",
                        properties={k: types.Schema(type="STRING") for k in props},
                        required=required)


DECLARATIONS = [
    types.FunctionDeclaration(
        name="open_app", description="Open a desktop application on the user's PC by name, e.g. chrome, spotify, notepad.",
        parameters=_obj(["app"], ["app"])),
    types.FunctionDeclaration(
        name="open_url", description="Open a website in the default browser.",
        parameters=_obj(["url"], ["url"])),
    types.FunctionDeclaration(
        name="open_file", description="Find and open a file by name (searches Desktop/Documents/Downloads) or full path.",
        parameters=_obj(["name"], ["name"])),
    types.FunctionDeclaration(
        name="remember_shortcut",
        description="Save a named shortcut so the user can later say 'open <name>'. kind must be 'app', 'url', or 'file'; target is the app name, url, or file.",
        parameters=_obj(["name", "kind", "target"], ["name", "kind", "target"])),
    types.FunctionDeclaration(
        name="open_shortcut", description="Open a shortcut the user previously asked you to remember, by its name.",
        parameters=_obj(["name"], ["name"])),
    types.FunctionDeclaration(
        name="set_voice_volume",
        description="Set how loud YOUR OWN voice (Ares) plays. Use when the user says things like 'set your volume to 40' or 'speak up'. level is a percent like '40' or a word like 'half','max','mute'.",
        parameters=_obj(["level"], ["level"])),
    types.FunctionDeclaration(
        name="set_pc_volume",
        description="Set the COMPUTER's system/master volume. Use when the user says 'set the pc volume to 30', 'turn the computer volume up', etc. level is a percent like '30' or a word like 'half','max','mute'.",
        parameters=_obj(["level"], ["level"])),
    types.FunctionDeclaration(
        name="get_current_time",
        description="Get the current local date and time. Use whenever the user asks the time/date, or you need 'now' for scheduling or time math.",
        parameters=_obj([], [])),
]
