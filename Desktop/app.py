"""Robot Companion - desktop app (modern customtkinter UI).

  * Provisions the robot's WiFi over USB, then connects to it on your network.
  * Wake word "Ares" (Porcupine) -> hands-free back-and-forth with Gemini.
  * Streams the robot's onboard mic; plays the reply on a PC output you pick.
  * Web search + personality (system.md / memory.md); battery + eye states.

Run:  python app.py
"""
import queue
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

import assistant
import audio_io
import config
import device_stream
from device_link import DeviceLink, available_ports
from gemini_live import GeminiVoiceSession

ACCENT = "#1a78c2"
ACCENT_HOVER = "#2f93dd"
CARD = "#171d28"
MUTED = "#8aa0b2"
STATE_COLORS = {"idle": "#3a4656", "listening": "#2fae72", "speaking": "#1a8fd0"}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = config.load()
        self.session = None
        self.assistant = None
        self.pc_mic = None
        self.mic_queue = None
        self.serial = DeviceLink(on_log=self._log_threadsafe)
        self.stream = device_stream.DeviceStream(
            on_log=self._log_threadsafe, on_status=self._status_threadsafe)
        self._found = []
        self._found_labels = []
        self._in_devs = []
        self._out_devs = []
        self._ports = []
        self._ui_queue = queue.Queue()

        root.title("Robot Companion")
        root.geometry("560x820")
        root.minsize(520, 640)

        self._build_ui()
        self._refresh_devices()
        self._refresh_ports()
        self.root.after(80, self._drain_ui_queue)

    # ---- small builders --------------------------------------------------
    def _card(self, title):
        c = ctk.CTkFrame(self.scroll, corner_radius=14, fg_color=CARD)
        c.pack(fill="x", padx=2, pady=8)
        ctk.CTkLabel(c, text=title, font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(anchor="w", padx=16, pady=(12, 4))
        return c

    def _row(self, parent, label):
        r = ctk.CTkFrame(parent, fg_color="transparent")
        r.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(r, text=label, text_color=MUTED, width=110, anchor="w"
                     ).pack(side="left")
        return r

    def _menu(self, row, command=None):
        m = ctk.CTkOptionMenu(row, values=["(none)"], command=command,
                              fg_color="#222b3a", button_color="#2c3950",
                              button_hover_color="#37496a", dynamic_resizing=False)
        m.pack(side="left", fill="x", expand=True, padx=8)
        return m

    def _entry(self, row, var, show=None):
        e = ctk.CTkEntry(row, textvariable=var, show=show)
        e.pack(side="left", fill="x", expand=True, padx=8)
        return e

    def _btn(self, parent, text, command, accent=False, **kw):
        return ctk.CTkButton(
            parent, text=text, command=command,
            fg_color=ACCENT if accent else "#27313f",
            hover_color=ACCENT_HOVER if accent else "#323e50", **kw)

    # ---- UI --------------------------------------------------------------
    def _build_ui(self):
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(header, text="Robot Companion",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
        self.state_label = ctk.CTkLabel(header, text="idle", text_color=MUTED)
        self.state_label.pack(side="right")
        self.state_dot = ctk.CTkFrame(header, width=14, height=14, corner_radius=7,
                                      fg_color=STATE_COLORS["idle"])
        self.state_dot.pack(side="right", padx=8)

        self.scroll = ctk.CTkScrollableFrame(self.root, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        # --- Conversation ---
        conv = self._card("Conversation")
        r = self._row(conv, "Gemini key")
        self.key_var = tk.StringVar(value=self.cfg.get("gemini_api_key", ""))
        self._entry(r, self.key_var, show="•")
        self._btn(r, "Save", self._save_key, width=64).pack(side="left", padx=(0, 8))
        r = self._row(conv, "Voice out")
        self.out_menu = self._menu(r)
        self._btn(r, "Refresh", self._refresh_devices, width=80).pack(side="left", padx=(0, 8))
        r = self._row(conv, "Mic (no robot)")
        self.in_menu = self._menu(r)
        self.talk_btn = self._btn(conv, "Start talking", self._toggle_talk,
                                  accent=True, height=42,
                                  font=ctk.CTkFont(size=15, weight="bold"))
        self.talk_btn.pack(fill="x", padx=16, pady=(8, 4))
        self.mic_note = ctk.CTkLabel(conv, text="Using PC mic (connect a robot to use its mic)",
                                     text_color=MUTED, font=ctk.CTkFont(size=11))
        self.mic_note.pack(anchor="w", padx=16, pady=(0, 12))

        # --- WiFi provisioning ---
        prov = self._card("1.  Put the robot on WiFi  (USB cable)")
        r = self._row(prov, "WiFi name")
        self.ssid_var = tk.StringVar()
        self._entry(r, self.ssid_var)
        r = self._row(prov, "WiFi password")
        self.wpass_var = tk.StringVar()
        self._entry(r, self.wpass_var, show="•")
        r = self._row(prov, "USB port")
        self.port_menu = self._menu(r)
        self._btn(r, "Refresh", self._refresh_ports, width=80).pack(side="left", padx=(0, 8))
        self._btn(prov, "Send WiFi to robot over USB", self._provision
                  ).pack(fill="x", padx=16, pady=(6, 12))

        # --- Connect ---
        find = self._card("2.  Connect to the robot  (WiFi)")
        r = self._row(find, "Found")
        self.dev_menu = self._menu(r)
        self._btn(r, "Scan", self._scan, width=64).pack(side="left", padx=(0, 8))
        r = self._row(find, "or IP")
        self.ip_var = tk.StringVar(value=self.cfg.get("device_ip", ""))
        self._entry(r, self.ip_var)
        self.connect_btn = self._btn(find, "Connect to robot", self._toggle_robot, accent=True)
        self.connect_btn.pack(fill="x", padx=16, pady=(6, 6))
        r = self._row(find, "Brightness")
        self.bright_var = tk.DoubleVar(value=0.8)
        ctk.CTkSlider(r, from_=0, to=1, variable=self.bright_var,
                      command=self._on_brightness).pack(side="left", fill="x",
                                                        expand=True, padx=8)
        r = self._row(find, "")
        self.lowpower_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(r, text="Low-power mode", variable=self.lowpower_var,
                      command=self._on_lowpower).pack(side="left")
        self.batt_label = ctk.CTkLabel(find, text="Battery: -",
                                       font=ctk.CTkFont(size=13, weight="bold"))
        self.batt_label.pack(anchor="w", padx=16, pady=(4, 12))

        # --- Hands-free ---
        hf = self._card("3.  Hands-free  (say “Ares”)")
        ctk.CTkLabel(hf, text="Leave both fields blank for built-in “Ares” (no signup). "
                     "First start downloads a small model.",
                     text_color=MUTED, font=ctk.CTkFont(size=11),
                     wraplength=480, justify="left").pack(anchor="w", padx=16, pady=(0, 2))
        r = self._row(hf, "Porcupine key")
        self.pvkey_var = tk.StringVar(value=self.cfg.get("porcupine_access_key", ""))
        self._entry(r, self.pvkey_var, show="•")
        r = self._row(hf, "Ares.ppn")
        self.ppn_var = tk.StringVar(value=self.cfg.get("porcupine_keyword_path", ""))
        self._entry(r, self.ppn_var)
        self._btn(r, "Browse", self._browse_ppn, width=80).pack(side="left", padx=(0, 8))
        self.hf_btn = self._btn(hf, "Start hands-free", self._toggle_handsfree,
                                accent=True, height=42,
                                font=ctk.CTkFont(size=15, weight="bold"))
        self.hf_btn.pack(fill="x", padx=16, pady=(6, 12))

        # --- Log ---
        logc = self._card("Log")
        self.log = ctk.CTkTextbox(logc, height=120, font=ctk.CTkFont(family="Consolas", size=11))
        self.log.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.log.configure(state="disabled")

    # ---- device helpers --------------------------------------------------
    @staticmethod
    def _name_by_idx(idx, devs):
        for i, nm in devs:
            if i == idx:
                return nm
        return None

    @staticmethod
    def _idx_by_name(name, devs):
        for i, nm in devs:
            if nm == name:
                return i
        return None

    @staticmethod
    def _fill(menu, values, prefer=None):
        vals = values or ["(none)"]
        menu.configure(values=vals)
        menu.set(prefer if (prefer in vals) else vals[0])

    def _refresh_devices(self):
        try:
            self._in_devs = audio_io.input_devices()
            self._out_devs = audio_io.output_devices()
        except Exception as e:  # noqa: BLE001
            self._in_devs, self._out_devs = [], []
            self._log(f"Audio device scan failed: {e}")
        self._fill(self.in_menu, [n for _, n in self._in_devs] or ["(default)"],
                   self._name_by_idx(self.cfg.get("input_device"), self._in_devs))
        self._fill(self.out_menu, [n for _, n in self._out_devs] or ["(default)"],
                   self._name_by_idx(self.cfg.get("output_device"), self._out_devs))

    def _refresh_ports(self):
        self._ports = available_ports()
        labels = [f"{d}  -  {desc}" for d, desc in self._ports]
        prefer = next((f"{d}  -  {desc}" for d, desc in self._ports
                       if d == self.cfg.get("device_port", "")), None)
        self._fill(self.port_menu, labels or ["(none found)"], prefer)

    def _selected_output_index(self):
        return self._idx_by_name(self.out_menu.get(), self._out_devs)

    def _selected_input_index(self):
        return self._idx_by_name(self.in_menu.get(), self._in_devs)

    def _selected_port(self):
        t = self.port_menu.get()
        return t.split("  -  ")[0].strip() if t and "-" in t else ""

    def _control_target(self):
        if self.stream.connected:
            return self.stream
        if self.serial.connected:
            return self.serial
        return None

    # ---- actions ---------------------------------------------------------
    def _save_key(self):
        self.cfg["gemini_api_key"] = self.key_var.get().strip()
        config.save(self.cfg)
        self._log("API key saved.")

    def _provision(self):
        port = self._selected_port()
        ssid = self.ssid_var.get().strip()
        if not port or not ssid:
            self._log("Pick the USB port and enter your WiFi name first.")
            return
        if not self.serial.connected and not self.serial.connect(port):
            return
        self.cfg["device_port"] = port
        config.save(self.cfg)
        self.serial.set_wifi(ssid, self.wpass_var.get())
        self._log(f"Sent WiFi '{ssid}'. Watch the log for the robot's IP, then Scan.")

    def _scan(self):
        self._log("Scanning WiFi for the robot...")

        def work():
            self._ui_queue.put(("found", device_stream.discover(3.0)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_found(self, found):
        self._found = found
        if not found:
            self._fill(self.dev_menu, ["(none found)"])
            self._log("No robot found. Use the 'or IP' box with the IP from the log.")
            return
        self._found_labels = [f"{n}  ({ip}:{p})" for n, ip, p in found]
        self._fill(self.dev_menu, self._found_labels)
        self._log(f"Found {len(found)} robot(s).")

    def _toggle_robot(self):
        if self.stream.connected:
            self.stream.close()
            self.connect_btn.configure(text="Connect to robot")
            self.mic_note.configure(text="Using PC mic (connect a robot to use its mic)")
            self._log("Robot disconnected.")
            return
        ip = self.ip_var.get().strip()
        port = 8080
        if not ip:
            sel = self.dev_menu.get()
            if sel in self._found_labels:
                _n, ip, port = self._found[self._found_labels.index(sel)]
            else:
                self._log("Scan and pick a robot, or type its IP.")
                return
        if self.stream.connect(ip, port):
            self.cfg["device_ip"] = ip
            config.save(self.cfg)
            self.connect_btn.configure(text="Disconnect robot")
            self.mic_note.configure(text="Using the robot's onboard mic")
            threading.Thread(                    # pull/seed personality from SD
                target=lambda: device_stream.sync_personality(ip, self._log_threadsafe),
                daemon=True).start()

    def _toggle_talk(self):
        if self.session is None:
            if self.assistant and self.assistant.running:
                self._toggle_handsfree()
            self.cfg["gemini_api_key"] = self.key_var.get().strip()
            self.cfg["output_device"] = self._selected_output_index()
            self.cfg["input_device"] = self._selected_input_index()
            config.save(self.cfg)
            if not self.cfg["gemini_api_key"]:
                self._log("Add your Gemini API key first.")
                return
            self.mic_queue = queue.Queue(maxsize=100)
            if self.stream.connected:
                self.stream.set_sink(self.mic_queue)
                self._log("Talking with the robot's mic.")
            else:
                self.pc_mic = audio_io.MicCapture(
                    device=self.cfg["input_device"], on_chunk=self._feed_mic)
                self.pc_mic.start()
                self._log("Talking with the PC mic.")
            self.session = GeminiVoiceSession(
                self.cfg, self.mic_queue,
                on_state=self._on_state_threadsafe, on_log=self._log_threadsafe)
            self.session.start()
            self.talk_btn.configure(text="Stop")
        else:
            self._stop_session()

    def _feed_mic(self, b):
        try:
            self.mic_queue.put_nowait(b)
        except queue.Full:
            pass

    def _stop_session(self):
        if self.session:
            self.session.stop()
            self.session = None
        if self.pc_mic:
            self.pc_mic.stop()
            self.pc_mic = None
        self.stream.set_sink(None)
        self.talk_btn.configure(text="Start talking")
        self._on_state("idle")

    def _browse_ppn(self):
        p = filedialog.askopenfilename(
            title="Select your Ares.ppn keyword file",
            filetypes=[("Porcupine keyword", "*.ppn"), ("All files", "*.*")])
        if p:
            self.ppn_var.set(p)

    def _toggle_handsfree(self):
        if self.assistant and self.assistant.running:
            self.assistant.stop()
            self.assistant = None
            self.hf_btn.configure(text="Start hands-free")
            return
        if self.session:
            self._stop_session()
        self.cfg["porcupine_access_key"] = self.pvkey_var.get().strip()
        self.cfg["porcupine_keyword_path"] = self.ppn_var.get().strip()
        self.cfg["gemini_api_key"] = self.key_var.get().strip()
        self.cfg["output_device"] = self._selected_output_index()
        config.save(self.cfg)
        if not self.cfg["gemini_api_key"]:
            self._log("Add your Gemini key first.")
            return
        self.assistant = assistant.Assistant(
            self.cfg, self.stream,
            on_state=self._on_state_threadsafe, on_log=self._log_threadsafe)
        if self.assistant.start():
            self.hf_btn.configure(text="Stop hands-free")
        else:
            self.assistant = None

    def _on_brightness(self, _v=None):
        t = self._control_target()
        if t:
            t.set_brightness(self.bright_var.get())

    def _on_lowpower(self):
        t = self._control_target()
        if t:
            t.set_lowpower(self.lowpower_var.get())

    # ---- thread-safe state / log -----------------------------------------
    def _on_state_threadsafe(self, s):
        self._ui_queue.put(("state", s))

    def _log_threadsafe(self, m):
        self._ui_queue.put(("log", m))

    def _status_threadsafe(self, d):
        self._ui_queue.put(("status", d))

    def _drain_ui_queue(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "state":
                    self._on_state(payload)
                elif kind == "log":
                    self._log(payload)
                elif kind == "found":
                    self._apply_found(payload)
                elif kind == "status":
                    self._apply_status(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_ui_queue)

    def _apply_status(self, d):
        pct = d.get("battery", "?")
        v = d.get("volts", 0.0)
        suffix = " - charged" if d.get("charged") else (
            " - charging" if d.get("charging") else "")
        self.batt_label.configure(text=f"Battery: {pct}%  ({v:.2f} V){suffix}")
        if d.get("event") == "charged":
            self._notify("Robot fully charged", f"Battery at {pct}% ({v:.2f} V).")

    def _notify(self, title, msg):
        self._log(f"{title} - {msg}")
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass
        try:
            from plyer import notification
            notification.notify(title=title, message=msg,
                                app_name="Robot Companion", timeout=8)
            return
        except Exception:
            pass
        try:
            self.root.deiconify()
            self.root.focus_force()
        except Exception:
            pass

    def _on_state(self, s):
        self.state_dot.configure(fg_color=STATE_COLORS.get(s, STATE_COLORS["idle"]))
        self.state_label.configure(text=s)
        t = self._control_target()
        if t:
            t.set_state(s)

    def _log(self, m):
        self.log.configure(state="normal")
        self.log.insert("end", m + "\n")
        try:
            self.log.see("end")
        except Exception:
            pass
        self.log.configure(state="disabled")

    def on_close(self):
        if self.assistant:
            self.assistant.stop()
        self._stop_session()
        self.stream.close()
        self.serial.disconnect()
        self.root.destroy()


def _write_crash(text):
    import datetime
    import pathlib
    try:
        with open(pathlib.Path(__file__).resolve().parent / "crash.log", "a",
                  encoding="utf-8") as f:
            f.write(f"\n--- {datetime.datetime.now()} ---\n{text}\n")
    except Exception:
        pass


def main():
    import traceback
    try:
        root = ctk.CTk()

        def report_cb_exc(exc, val, tb):
            _write_crash("".join(traceback.format_exception(exc, val, tb)))
            try:
                from tkinter import messagebox
                messagebox.showerror("Robot Companion - error", str(val))
            except Exception:
                pass

        root.report_callback_exception = report_cb_exc
        app = App(root)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        root.mainloop()
    except Exception:
        _write_crash(traceback.format_exc())
        try:
            from tkinter import messagebox
            messagebox.showerror("Robot Companion crashed",
                                 "Error on startup - details in crash.log next to app.py.")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
