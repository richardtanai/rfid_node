"""MicroPython firmware for M5Stack — RFID2 WS1850S bridge.

Runs on the M5Stack (ESP32). Polls the WS1850S RFID chip over I2C and emits
newline-delimited JSON events over USB serial (115200 baud) to the host Jetson.

Serial protocol (M5Stack → Jetson):
    {"event":"tag",    "uid":"A1B2C3D4", "ts_ms":12345}
    {"event":"no_tag", "ts_ms":12345}
    {"event":"hb",     "ts_ms":12345}
    {"event":"error",  "msg":"i2c_timeout", "ts_ms":12345}

Serial protocol (Jetson → M5Stack):
    {"cmd":"scan"}   — force an immediate fresh read (bypasses re-emit guard)

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
import uselect

# ── I2C pins — adjust for your M5Stack model ─────────────────────────────────
SDA_PIN = 21
SCL_PIN = 22

RFID2_ADDR          = 0x28  # WS1850S I2C address
POLL_INTERVAL_MS    = 200   # ms between card-present polls
HB_INTERVAL_MS      = 5000  # ms between heartbeat emits
RE_EMIT_INTERVAL_MS = 2000  # re-emit held tag every N ms so host can detect already-present tag
CMD_CHECK           = 0x01
CMD_READ_UID        = 0x02

i2c = machine.I2C(0, sda=machine.Pin(SDA_PIN), scl=machine.Pin(SCL_PIN), freq=100000)

# Non-blocking poll on stdin for incoming commands from Jetson.
_stdin_poll = uselect.poll()
_stdin_poll.register(sys.stdin, uselect.POLLIN)

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


def _poll_serial_input(line_buf):
    """Drain stdin without blocking; return updated line_buf and force_read flag."""
    force_read = False
    while _stdin_poll.poll(0):
        ch = sys.stdin.read(1)
        if ch == "\n":
            line = line_buf.strip()
            if '"scan"' in line:
                force_read = True
            line_buf = ""
        else:
            line_buf += ch
    return line_buf, force_read


def main():
    last_hb_ms   = _now_ms()
    last_poll_ms = _now_ms()
    last_emit_ms = 0
    last_uid     = None
    hb_count     = 0
    tag_count    = 0
    status       = "Ready"
    line_buf     = ""
    force_read   = False

    _emit({"event": "hb", "ts_ms": _now_ms()})  # announce boot
    _display(None, hb_count, tag_count, status)

    while True:
        now = _now_ms()

        # ── Commands from Jetson ───────────────────────────────────────────
        line_buf, new_force = _poll_serial_input(line_buf)
        if new_force:
            force_read = True

        # ── Heartbeat ─────────────────────────────────────────────────────
        if time.ticks_diff(now, last_hb_ms) >= HB_INTERVAL_MS:
            _emit({"event": "hb", "ts_ms": now})
            hb_count  += 1
            last_hb_ms = now
            _display(last_uid, hb_count, tag_count, status)

        # ── Card poll ─────────────────────────────────────────────────────
        # force_read bypasses the poll interval for an immediate fresh read.
        if not force_read and time.ticks_diff(now, last_poll_ms) < POLL_INTERVAL_MS:
            time.sleep_ms(10)
            continue

        last_poll_ms = now
        was_forced   = force_read
        force_read   = False

        try:
            present = _check_card()
        except Exception as exc:
            msg = str(exc)
            _emit({"event": "error", "msg": msg, "ts_ms": _now_ms()})
            status       = "Error: " + msg
            last_uid     = None
            last_emit_ms = 0
            _display(None, hb_count, tag_count, status)
            continue

        if present:
            uid = _read_uid()
            if uid:
                is_new    = uid != last_uid
                due_again = time.ticks_diff(now, last_emit_ms) >= RE_EMIT_INTERVAL_MS
                # Always emit on forced read so the Jetson always gets a fresh
                # event for each SPB cycle, even when the same tag remains.
                if was_forced or is_new or due_again:
                    _emit({"event": "tag", "uid": uid, "ts_ms": now})
                    last_emit_ms = now
                    if is_new:
                        last_uid  = uid
                        tag_count += 1
                        status    = "Tag read OK"
                        _display(last_uid, hb_count, tag_count, status)
        else:
            if was_forced:
                # Jetson is waiting for a response — tell it no card is present.
                _emit({"event": "no_tag", "ts_ms": now})
            if last_uid is not None:
                last_uid     = None
                last_emit_ms = 0  # card removed — reset re-emit timer


main()
