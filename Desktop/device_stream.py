"""WiFi link to the robot: mDNS discovery + raw-PCM audio stream + control.

The firmware advertises itself over mDNS as service ``_roboteyes._tcp`` and,
once a TCP client connects to port 8080, streams its onboard mic as raw
16 kHz / 16-bit / mono PCM. The same socket carries newline-delimited JSON
commands back to the device (eye state / brightness / low-power).
"""
import json
import queue
import socket
import threading
import time

try:
    from zeroconf import Zeroconf, ServiceBrowser
except ImportError:
    Zeroconf = None
    ServiceBrowser = None

SERVICE = "_roboteyes._tcp.local."
STATUS_PORT = 8081          # firmware pushes battery JSON here (separate socket)


def discover(timeout=3.0):
    """Browse the LAN for robots. Returns [(name, ip, port), ...]."""
    if not Zeroconf:
        return []
    zc = Zeroconf()
    found = {}

    class _Listener:
        def add_service(self, zc_, type_, name):
            info = zc_.get_service_info(type_, name, timeout=2000)
            if info and info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                found[name] = (name.split(".")[0], ip, info.port)

        def update_service(self, *_a):
            pass

        def remove_service(self, *_a):
            pass

    ServiceBrowser(zc, SERVICE, _Listener())
    time.sleep(timeout)
    zc.close()
    return list(found.values())


def fetch_personality(host, timeout=4):
    """GET system.md/memory.md from the robot's SD over HTTP. Values may be None."""
    import urllib.request
    out = {}
    for name in ("system.md", "memory.md"):
        try:
            with urllib.request.urlopen(f"http://{host}/{name}", timeout=timeout) as r:
                out[name] = r.read().decode("utf-8", "replace")
        except Exception:
            out[name] = None
    return out


def push_personality(host, files, timeout=5):
    import urllib.request
    ok = True
    for name, content in files.items():
        try:
            req = urllib.request.Request(
                f"http://{host}/{name}", data=(content or "").encode("utf-8"),
                headers={"Content-Type": "text/plain"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ok = ok and (getattr(r, "status", 200) == 200)
        except Exception:
            ok = False
    return ok


def sync_personality(host, on_log=lambda m: None):
    """Robot's SD is the source of truth: pull it if present, else seed it."""
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    got = fetch_personality(host)
    if any((got.get(n) or "").strip() for n in ("system.md", "memory.md")):
        for n in ("system.md", "memory.md"):
            c = got.get(n)
            if c and c.strip():
                (here / n).write_text(c, encoding="utf-8")
        on_log("Loaded personality from the robot's SD card.")
        return
    files = {}
    for n in ("system.md", "memory.md"):
        p = here / n
        files[n] = p.read_text(encoding="utf-8") if p.exists() else ""
    if push_personality(host, files):
        on_log("Copied this PC's personality onto the robot's SD card.")
    else:
        on_log("No SD card in the robot — using the app's personality.")


class DeviceStream:
    def __init__(self, on_log=None, on_status=None):
        self.on_log = on_log or (lambda m: None)
        self.on_status = on_status or (lambda d: None)
        self.sock = None
        self.status_sock = None
        self.sink = None            # a queue.Queue to receive PCM, or None
        self.host = None
        self.port = None
        self.alive = False          # True while the audio socket is healthy
        self._stop = threading.Event()
        self._reader = None
        self._status_reader = None

    @property
    def connected(self):
        return self.sock is not None

    def connect(self, host, port):
        try:
            self.sock = socket.create_connection((host, port), timeout=5)
            self.sock.settimeout(0.5)
            self.host, self.port = host, port
            self._stop.clear()
            self.alive = True
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            self.on_log(f"Robot connected over WiFi ({host}:{port})")
        except Exception as e:  # noqa: BLE001
            self.on_log(f"WiFi connect failed: {e}")
            self.sock = None
            return False
        # battery/status channel (best effort - won't fail the connection)
        try:
            self.status_sock = socket.create_connection((host, STATUS_PORT), timeout=5)
            self.status_sock.settimeout(0.5)
            self._status_reader = threading.Thread(target=self._status_loop, daemon=True)
            self._status_reader.start()
        except Exception as e:  # noqa: BLE001
            self.on_log(f"(battery status unavailable: {e})")
            self.status_sock = None
        return True

    def _status_loop(self):
        buf = b""
        while not self._stop.is_set() and self.status_sock:
            try:
                data = self.status_sock.recv(512)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    self.on_status(json.loads(line.decode("utf-8", "replace")))
                except Exception:
                    pass

    def set_sink(self, q):
        """Route incoming mic PCM into queue ``q`` (or None to drop it)."""
        self.sink = q

    def _read_loop(self):
        while not self._stop.is_set() and self.sock:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                break
            q = self.sink
            if q is not None:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass
        self.alive = False
        self.on_log("Robot stream ended")

    def send_command(self, obj):
        if not self.sock:
            return
        try:
            self.sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.on_log(f"Robot send failed: {e}")

    def set_state(self, state):
        self.send_command({"cmd": "state", "value": state})

    def set_brightness(self, frac):
        self.send_command({"cmd": "brightness", "value": round(float(frac), 3)})

    def set_lowpower(self, on):
        self.send_command({"cmd": "lowpower", "value": bool(on)})

    def close(self):
        self._stop.set()
        self.alive = False
        for attr in ("sock", "status_sock"):
            s = getattr(self, attr)
            if s:
                try:
                    s.close()
                except Exception:
                    pass
                setattr(self, attr, None)
