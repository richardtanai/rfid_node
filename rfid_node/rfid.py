"""RFID reader core for the RFID node.

Reads newline-delimited JSON events from an M5Stack running rfid_m5stack.py
over USB serial and exposes a clean Python API.

Hardware chain:
    WS1850S (13.56 MHz RFID)
        ↕  I2C (0x28)
    M5Stack (ESP32, MicroPython)
        ↕  USB serial — /dev/ttyUSB0
    RfidReader (this module)

Serial events consumed (M5Stack → Jetson):
    {"event":"tag",    "uid":"A1B2C3D4", "ts_ms":...}
    {"event":"no_tag", "ts_ms":...}
    {"event":"hb",     "ts_ms":...}
    {"event":"error",  "msg":"...",       "ts_ms":...}

Serial commands sent (Jetson → M5Stack):
    {"cmd":"scan"}   — request an immediate fresh read from the firmware

Usage:
    reader = RfidReader()
    reader.start()                 # opens serial port, starts background listener
    result = reader.read_one_tag() # blocks until tag or scan_timeout_s
    print(result.tag_id, result.timestamp_ms)
    reader.stop()
"""

import json
import queue as _queue
import threading
import time
from typing import Callable, NamedTuple, Optional

import serial

from .config_loader import load_config

_SCAN_CMD = b'{"cmd":"scan"}\n'


class RfidResult(NamedTuple):
    tag_id: str        # uppercase hex UID string, e.g. "A1B2C3D4"
    timestamp_ms: int  # epoch ms from host wall clock at receipt time


class RfidReader:
    """Persistent serial listener for the M5Stack RFID bridge.

    Keeps the serial port open in a background daemon thread so tag events
    are never missed between scan cycles. Call start() once, then call
    read_one_tag() for each scan cycle.
    """

    def __init__(self, cfg=None):
        if cfg is None:
            cfg = load_config()
        self._port         = cfg.rfid_port
        self._baud         = cfg.rfid_baud_rate
        self._scan_timeout = cfg.scan_timeout_s
        self._hb_timeout   = cfg.hb_timeout_s

        self._tag_queue: _queue.Queue = _queue.Queue(maxsize=8)
        self._stop_event  = threading.Event()
        self._error: Optional[RuntimeError] = None
        self._thread: Optional[threading.Thread] = None

        # Serial reference shared with background thread for command writes.
        self._serial: Optional[serial.Serial] = None
        self._serial_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Open serial port and start background listener thread.

        Raises RuntimeError immediately if the port cannot be opened.
        Safe to call multiple times — no-op if already running.
        """
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._error = None
        # Probe the port before launching the thread so failures are immediate.
        try:
            ser = serial.Serial(self._port, self._baud, timeout=1.0)
        except serial.SerialException as exc:
            raise RuntimeError(f"ReaderOffline: {exc}") from exc

        self._thread = threading.Thread(
            target=self._run, args=(ser,), daemon=True, name="rfid-serial"
        )
        self._thread.start()
        print(f"[RFID] Serial listener started on {self._port}")

    def stop(self):
        """Stop the background listener and close the serial port."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._thread = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_one_tag(self) -> RfidResult:
        """Trigger a fresh scan and block until a tag is confirmed.

        Sends {"cmd":"scan"} to the firmware so the M5Stack performs an
        immediate read regardless of re-emit timers or last-seen UID.  Any
        tag events queued from a previous cycle are discarded first, so the
        returned result is always post-command.

        The background listener must be running (call start() first).

        Raises:
            RuntimeError("ReaderOffline")  — serial port lost or heartbeat timeout.
            RuntimeError("ScanTimeout")    — no tag within scan_timeout_s.
        """
        if self._error is not None:
            raise self._error

        # Discard all queued events from the previous cycle.
        while not self._tag_queue.empty():
            try:
                self._tag_queue.get_nowait()
            except _queue.Empty:
                break

        # Ask the firmware for an immediate fresh read.
        self._send_scan_command()

        # Wait for the firmware's tag response.
        deadline = time.monotonic() + self._scan_timeout
        while True:
            if self._error is not None:
                raise self._error

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("ScanTimeout")

            try:
                result = self._tag_queue.get(timeout=min(remaining, 1.0))
                return result
            except _queue.Empty:
                continue

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_scan_command(self):
        with self._serial_lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.write(_SCAN_CMD)
                except serial.SerialException:
                    pass

    # ------------------------------------------------------------------
    # Internal background loop
    # ------------------------------------------------------------------

    def _run(self, ser: serial.Serial):
        with self._serial_lock:
            self._serial = ser

        last_hb = time.monotonic()
        try:
            while not self._stop_event.is_set():
                if time.monotonic() - last_hb > self._hb_timeout:
                    self._error = RuntimeError("ReaderOffline: M5Stack heartbeat lost")
                    print("[RFID] Heartbeat lost — reader offline")
                    return

                raw = ser.readline()
                if not raw:
                    continue

                try:
                    obj = json.loads(raw.decode("utf-8", errors="replace").strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                event = obj.get("event")

                if event == "hb":
                    last_hb = time.monotonic()

                elif event == "tag":
                    uid = obj.get("uid", "")
                    if uid:
                        result = RfidResult(
                            tag_id=uid.upper(),
                            timestamp_ms=int(time.time() * 1000),
                        )
                        try:
                            self._tag_queue.put_nowait(result)
                        except _queue.Full:
                            pass
                        print(f"[RFID] Tag detected: {uid.upper()}")

                elif event == "no_tag":
                    # Firmware answered the scan command but found no card.
                    # Leave the queue empty so read_one_tag() keeps waiting.
                    print("[RFID] No tag present (scan command response)")

                elif event == "error":
                    print(f"[RFID] M5Stack error: {obj.get('msg', '')}")

        finally:
            with self._serial_lock:
                self._serial = None
            try:
                ser.close()
            except Exception:
                pass
            print("[RFID] Serial listener stopped")
