"""MicroPython firmware for M5Stack — RFID2 WS1850S bridge.

Runs on the M5Stack (ESP32). Polls the WS1850S RFID chip over I2C and emits
newline-delimited JSON events over USB serial (115200 baud) to the host Jetson.

Serial protocol (M5Stack → Jetson):
    {"event":"tag",   "uid":"A1B2C3D4", "ts_ms":12345}
    {"event":"hb",    "ts_ms":12345}
    {"event":"error", "msg":"i2c_timeout", "ts_ms":12345}

ts_ms is milliseconds since boot (monotonic). The Jetson host replaces it with
wall-clock time on receipt.

WS1850S I2C address: 0x28
Commands:
    0x01 → 1-byte response: 0x01 = card present, 0x00 = absent
    0x02 → 5-byte response: [uid_len, b0, b1, b2, b3]  (uid_len = 4 for ISO 14443A)

I2C pins:
    M5Stack Core / Core2 / Tough:  SDA=21, SCL=22
    M5Stack CoreS3:                SDA=12, SCL=11

Upload to M5Stack via:
    mpremote cp rfid_m5stack.py :main.py
or via M5Stack UIFlow IDE.
"""

import machine
import time
import json
import sys

# ── I2C pins — adjust for your M5Stack model ─────────────────────────────────
SDA_PIN = 21
SCL_PIN = 22

RFID2_ADDR       = 0x28  # WS1850S I2C address
POLL_INTERVAL_MS = 200   # ms between card-present polls
HB_INTERVAL_MS   = 5000  # ms between heartbeat emits
CMD_CHECK        = 0x01
CMD_READ_UID     = 0x02

i2c = machine.I2C(0, sda=machine.Pin(SDA_PIN), scl=machine.Pin(SCL_PIN), freq=100000)

# ── Optional display (M5Stack with built-in screen) ───────────────────────────
try:
    import m5stack
    _lcd = m5stack.lcd
    _HAS_DISPLAY = True
except ImportError:
    _HAS_DISPLAY = False


def _now_ms():
    return time.ticks_ms()


def _emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")


def _display(uid, hb_count, tag_count, status):
    if not _HAS_DISPLAY:
        return
    _lcd.clear()
    _lcd.font(_lcd.FONT_DejaVu18)
    _lcd.textColor(_lcd.WHITE, _lcd.BLACK)
    _lcd.print("RFID Node\n", 0, 0)
    _lcd.print("UID:  " + (uid or "---") + "\n", 0, 30)
    _lcd.print("Tags: " + str(tag_count) + "\n", 0, 60)
    _lcd.print("HB:   " + str(hb_count) + "\n", 0, 90)
    _lcd.print(status, 0, 120)


def _check_card():
    try:
        i2c.writeto(RFID2_ADDR, bytes([CMD_CHECK]))
        time.sleep_ms(20)
        resp = i2c.readfrom(RFID2_ADDR, 1)
        return resp[0] == 0x01
    except OSError:
        return False


def _read_uid():
    try:
        i2c.writeto(RFID2_ADDR, bytes([CMD_READ_UID]))
        time.sleep_ms(20)
        resp = i2c.readfrom(RFID2_ADDR, 5)
        uid_len = resp[0]
        if uid_len == 0 or uid_len > 4:
            return None
        return resp[1:1 + uid_len].hex().upper()
    except OSError:
        return None


def main():
    last_hb_ms = _now_ms()
    last_uid   = None  # debounce — only emit once per card presentation
    hb_count   = 0
    tag_count  = 0
    status     = "Ready"

    _emit({"event": "hb", "ts_ms": _now_ms()})  # announce boot
    _display(None, hb_count, tag_count, status)

    while True:
        now = _now_ms()

        # ── Heartbeat ─────────────────────────────────────────────────────
        if time.ticks_diff(now, last_hb_ms) >= HB_INTERVAL_MS:
            _emit({"event": "hb", "ts_ms": now})
            hb_count  += 1
            last_hb_ms = now
            _display(last_uid, hb_count, tag_count, status)

        # ── Card poll ─────────────────────────────────────────────────────
        try:
            present = _check_card()
        except Exception as exc:
            msg = str(exc)
            _emit({"event": "error", "msg": msg, "ts_ms": _now_ms()})
            status   = "Error: " + msg
            last_uid = None
            _display(None, hb_count, tag_count, status)
            time.sleep_ms(POLL_INTERVAL_MS)
            continue

        if present:
            uid = _read_uid()
            if uid and uid != last_uid:
                _emit({"event": "tag", "uid": uid, "ts_ms": _now_ms()})
                last_uid  = uid
                tag_count += 1
                status    = "Tag read OK"
                _display(last_uid, hb_count, tag_count, status)
        else:
            if last_uid is not None:
                last_uid = None  # card removed — next presentation fires fresh event

        time.sleep_ms(POLL_INTERVAL_MS)


main()
