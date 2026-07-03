"""Robot Companion - invisible background agent (system-tray, no window).

Auto-connects to the robot over WiFi and runs hands-free ("Ares") with full
capabilities (voice, web search, PC control). You never open a window - it lives
in the system tray and starts at login.

Configure ONCE with the full app first (python app.py):
  * paste your Gemini key,
  * Connect to the robot (saves its IP),
  * pick Voice out.
Then this agent uses those settings forever.

Run:  pythonw agent.py     (or let the Startup shortcut launch it)
"""
import pathlib
import subprocess
import sys
import threading
import time

import assistant as assistant_mod
import config
import device_stream

HERE = pathlib.Path(__file__).resolve().parent
EYE_COLORS = {"idle": (90, 110, 130), "listening": (60, 200, 120),
              "speaking": (40, 150, 210), "offline": (70, 78, 90)}


class Agent:
    def __init__(self):
        self.cfg = config.load()
        self.stream = None
        self.assistant = None
        self.state = "offline"
        self.battery = ""
        self.icon = None
        self._stop = threading.Event()

    # ---- logging / status ------------------------------------------------
    def _log(self, m):
        print("[agent]", m, flush=True)

    def _status(self, d):
        b = d.get("battery", "?")
        tag = "charging" if d.get("charging") else ("charged" if d.get("charged") else "")
        self.battery = f"{b}% {tag}".strip()
        self._refresh_icon()
        if d.get("event") == "charged":
            self._notify("Robot fully charged", self.battery)

    def _on_state(self, s):
        self.state = s
        self._refresh_icon()

    @staticmethod
    def _notify(title, msg):
        try:
            from plyer import notification
            notification.notify(title=title, message=msg,
                                app_name="Ares", timeout=6)
        except Exception:
            pass

    # ---- supervisor: keep connected + hands-free -------------------------
    def supervise(self):
        while not self._stop.is_set():
            ip = self.cfg.get("device_ip", "").strip()
            if not self.cfg.get("gemini_api_key") or not ip:
                self._set_offline("Run the full app once (set key + connect).")
                time.sleep(10)
                self.cfg = config.load()        # pick up new settings
                continue

            self.stream = device_stream.DeviceStream(on_log=self._log, on_status=self._status)
            if not self.stream.connect(ip, 8080):
                self._set_offline(f"Can't reach robot at {ip}, retrying...")
                time.sleep(8)
                continue
            device_stream.sync_personality(ip, self._log)   # pull/seed from SD
            self.assistant = assistant_mod.Assistant(
                self.cfg, self.stream, on_state=self._on_state, on_log=self._log)
            if not self.assistant.start():
                self.stream.close()
                self._set_offline("Hands-free failed to start, retrying...")
                time.sleep(8)
                continue

            self._log("Online - say \"Ares\".")
            while not self._stop.is_set() and self.stream.alive:
                time.sleep(2)

            if self.assistant:
                self.assistant.stop()
                self.assistant = None
            self.stream.close()
            if not self._stop.is_set():
                self._set_offline("Connection lost, reconnecting...")
                time.sleep(4)

    def _set_offline(self, msg):
        self.state = "offline"
        self._refresh_icon()
        self._log(msg)

    # ---- tray icon -------------------------------------------------------
    def _icon_image(self):
        from PIL import Image, ImageDraw
        c = EYE_COLORS.get(self.state, EYE_COLORS["offline"])
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([12, 16, 28, 48], radius=7, fill=c)
        d.rounded_rectangle([36, 16, 52, 48], radius=7, fill=c)
        return img

    def _refresh_icon(self):
        if not self.icon:
            return
        try:
            self.icon.icon = self._icon_image()
            title = f"Ares - {self.state}"
            if self.battery:
                title += f"  ({self.battery})"
            self.icon.title = title
        except Exception:
            pass

    def _open_settings(self, icon=None, item=None):
        pyw = sys.executable
        if pyw.endswith("python.exe"):
            pyw = pyw[:-len("python.exe")] + "pythonw.exe"
        subprocess.Popen([pyw, str(HERE / "app.py")], cwd=str(HERE))

    def _reconnect(self, icon=None, item=None):
        if self.stream:
            self.stream.close()        # supervisor loop will rebuild it

    def _quit(self, icon=None, item=None):
        self._stop.set()
        if self.assistant:
            self.assistant.stop()
        if self.stream:
            self.stream.close()
        if self.icon:
            self.icon.stop()

    def run(self):
        import pystray
        threading.Thread(target=self.supervise, daemon=True).start()
        menu = pystray.Menu(
            pystray.MenuItem("Settings…", self._open_settings),
            pystray.MenuItem("Reconnect", self._reconnect),
            pystray.MenuItem("Quit", self._quit),
        )
        self.icon = pystray.Icon("ares", self._icon_image(), "Ares (starting…)", menu)
        self.icon.run()


if __name__ == "__main__":
    Agent().run()
