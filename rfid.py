"""RFID reader core for the RFID node.

Reads newline-delimited JSON events from an M5Stack running rfid_m5stack.py
over USB serial and exposes a clean Python API.

Hardware chain:
    WS1850S (13.56 MHz RFID)
        ↕  I2C (0x28)
    M5Stack (ESP32, MicroPython)
        ↕  USB serial — /dev/ttyUSB0
    RfidReader (this module)

Serial events consumed:
    {"event":"tag",   "uid":"A1B2C3D4", "ts_ms":...}
    {"event":"hb",    "ts_ms":...}
    {"event":"error", "msg":"...",       "ts_ms":...}

Usage:
    reader = RfidReader()
    result = reader.read_one_tag()   # blocks until tag or scan_timeout_s
    print(result.tag_id, result.timestamp_ms)
"""

import json
import queue as _queue
import threading
import time
from typing import Callable, NamedTuple

import serial

from config_loader import load_config


class RfidResult(NamedTuple):
    tag_id: str        # uppercase hex UID string, e.g. "A1B2C3D4"
    timestamp_ms: int  # epoch ms from host wall clock at receipt time


class RfidReader:
    """Reads RFID tag events from the M5Stack serial bridge.

    read_stream() runs in the calling thread and raises on serial error.
    read_one_tag() spins up an internal daemon thread and re-raises any error.
    """

    def __init__(self, cfg=None):
        if cfg is None:
            cfg = load_config()
        self._port         = cfg.rfid_port
        self._baud         = cfg.rfid_baud_rate
        self._scan_timeout = cfg.scan_timeout_s
        self._hb_timeout   = cfg.hb_timeout_s

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_one_tag(self) -> RfidResult:
        """Block until one tag event arrives or scan_timeout_s elapses.

        Raises:
            RuntimeError("ReaderOffline")  — serial port unavailable or
                                             M5Stack heartbeat lost.
            RuntimeError("ScanTimeout")    — no tag within scan_timeout_s.
        """
        result_q: _queue.Queue = _queue.Queue(maxsize=1)
        stop = threading.Event()

        def _on_tag(r: RfidResult):
            try:
                result_q.put_nowait(r)
            except _queue.Full:
                pass
            stop.set()

        def _target():
            try:
                self._run_stream(_on_tag, stop)
            except RuntimeError as exc:
                try:
                    result_q.put_nowait(exc)
                except _queue.Full:
                    pass
                stop.set()

        t = threading.Thread(target=_target, daemon=True)
        t.start()

        try:
            item = result_q.get(timeout=self._scan_timeout + 2.0)
        except _queue.Empty:
            stop.set()
            raise RuntimeError("ScanTimeout")

        if isinstance(item, Exception):
            raise item
        return item

    def read_stream(
        self,
        callback: Callable[[RfidResult], None],
        stop_event: threading.Event,
    ) -> None:
        """Continuous loop — calls callback(RfidResult) on every tag event.

        Blocks until stop_event is set.

        Raises:
            RuntimeError("ReaderOffline")  — serial port unavailable or
                                             M5Stack heartbeat lost.
        """
        self._run_stream(callback, stop_event)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_stream(
        self,
        callback: Callable[[RfidResult], None],
        stop_event: threading.Event,
    ) -> None:
        """Open serial port and emit RfidResult via callback until stop_event.

        Raises RuntimeError on serial/heartbeat failure.
        """
        try:
            ser = serial.Serial(self._port, self._baud, timeout=1.0)
        except serial.SerialException as exc:
            raise RuntimeError(f"ReaderOffline: {exc}") from exc

        last_hb = time.monotonic()

        try:
            while not stop_event.is_set():
                if time.monotonic() - last_hb > self._hb_timeout:
                    raise RuntimeError("ReaderOffline: M5Stack heartbeat lost")

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
                        callback(RfidResult(
                            tag_id=uid.upper(),
                            timestamp_ms=int(time.time() * 1000),
                        ))

                elif event == "error":
                    print(f"[RFID] M5Stack error: {obj.get('msg', '')}")

        finally:
            try:
                ser.close()
            except Exception:
                pass
