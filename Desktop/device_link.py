"""USB-serial link to the ESP32 robot.

Sends newline-delimited JSON commands the firmware can act on:
    {"cmd": "state",      "value": "listening"}   # eye animation mode
    {"cmd": "brightness", "value": 0.6}            # 0..1
    {"cmd": "lowpower",   "value": true}

The current eyes firmware doesn't parse these yet - that's the next milestone.
Opening the port here also lets us watch the device's debug log. Note: while
this app holds the COM port, arduino-cli can't flash; close the connection
first to re-upload firmware.
"""
import json
import threading

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pyserial not installed yet
    serial = None
    list_ports = None


def available_ports():
    if not list_ports:
        return []
    return [(p.device, p.description) for p in list_ports.comports()]


class DeviceLink:
    def __init__(self, on_log=None):
        self.on_log = on_log or (lambda m: None)
        self.ser = None
        self._reader = None
        self._stop = threading.Event()

    @property
    def connected(self):
        return self.ser is not None and self.ser.is_open

    def connect(self, port, baud=115200):
        if not serial:
            self.on_log("pyserial not installed (pip install pyserial)")
            return False
        try:
            # dsrdtr=False avoids yanking the ESP32 into reset on open
            self.ser = serial.Serial(port, baud, timeout=0.2, dsrdtr=False)
            self._stop.clear()
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            self.on_log(f"Device connected on {port}")
            return True
        except Exception as e:  # noqa: BLE001
            self.on_log(f"Device connect failed: {e}")
            self.ser = None
            return False

    def _read_loop(self):
        while not self._stop.is_set() and self.connected:
            try:
                line = self.ser.readline().decode("utf-8", "replace").strip()
                if line:
                    self.on_log(f"[device] {line}")
            except Exception:
                break

    def _send(self, obj):
        if not self.connected:
            return
        try:
            self.ser.write((json.dumps(obj) + "\n").encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.on_log(f"Device send failed: {e}")

    def set_state(self, state):
        self._send({"cmd": "state", "value": state})

    def set_brightness(self, frac):
        self._send({"cmd": "brightness", "value": round(float(frac), 3)})

    def set_lowpower(self, on):
        self._send({"cmd": "lowpower", "value": bool(on)})

    def set_wifi(self, ssid, password):
        """Provision the robot's WiFi over USB so it can join your network."""
        self._send({"cmd": "wifi", "ssid": ssid, "pass": password})

    def disconnect(self):
        self._stop.set()
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None
