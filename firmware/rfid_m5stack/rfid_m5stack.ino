/**
 * rfid_m5stack.ino -- M5Stack RFID2 (WS1850S) serial bridge
 *
 * Polls the WS1850S RFID reader over I2C and emits newline-delimited JSON
 * events over USB serial (115200 baud) to the host Jetson.
 *
 * Serial protocol (M5Stack -> Jetson):
 *   {"event":"tag",   "uid":"A1B2C3D4", "ts_ms":12345}
 *   {"event":"hb",    "ts_ms":12345}
 *   {"event":"error", "msg":"init_failed", "ts_ms":12345}
 *
 * Serial protocol (Jetson -> M5Stack):
 *   {"cmd":"scan"}  -- clears last_uid so the next detection of any tag
 *                      (including the same one) is treated as is_new=true
 *
 * Card detection uses wakeup() / WUPA rather than detect() / REQA.
 * REQA only wakes cards in IDLE state; WUPA also wakes cards in HALT state.
 * A card that was deactivated (HALTed) by the previous cycle would be missed
 * permanently by REQA but is reliably found by WUPA every 200 ms poll.
 *
 * ts_ms is milliseconds since boot (monotonic). The Jetson replaces it with
 * wall-clock time on receipt.
 *
 * Dependencies (platformio.ini):
 *   - m5stack/M5Unified
 *   - m5stack/M5Unit-RFID  (replaces deprecated M5Unit-RFID2)
 *
 * Compatible boards: M5Stack Core, Core2, CoreS3, Tough
 */

#include <M5Unified.h>
#include <M5UnitUnified.h>
#include <M5UnitUnifiedRFID.h>

// -- Version ------------------------------------------------------------------

static constexpr const char* FW_VERSION = "v1.5.0";

// -- Configuration ------------------------------------------------------------

static constexpr uint32_t SERIAL_BAUD         = 115200;
static constexpr uint32_t POLL_INTERVAL_MS    = 200;
static constexpr uint32_t HB_INTERVAL_MS      = 5000;
static constexpr uint32_t DISP_INTERVAL_MS    = 1000;
static constexpr uint32_t RE_EMIT_INTERVAL_MS = 2000;

// -- Colours (RGB565) ---------------------------------------------------------

static constexpr uint32_t C_BG     = 0x1A1A2E;
static constexpr uint32_t C_HEADER = 0x16213E;
static constexpr uint32_t C_ACCENT = 0x0F3460;
static constexpr uint32_t C_GREEN  = 0x00FF7F;
static constexpr uint32_t C_RED    = 0xFF4444;
static constexpr uint32_t C_YELLOW = 0xFFD700;
static constexpr uint32_t C_WHITE  = 0xFFFFFF;
static constexpr uint32_t C_GREY   = 0x888888;

// -- Globals ------------------------------------------------------------------

m5::unit::UnitRFID2   unit;
m5::unit::UnitUnified Units;
m5::nfc::NFCLayerA    nfc_a{unit};

static uint32_t last_poll_ms = 0;
static uint32_t last_hb_ms   = 0;
static uint32_t last_disp_ms = 0;
static uint32_t last_emit_ms = 0;

static String   last_uid     = "";
static String   display_uid  = "---";
static uint32_t tag_count    = 0;
static uint32_t hb_count     = 0;
static bool     reader_ok    = false;
static String   status_msg   = "Initialising...";
static uint32_t status_color = C_YELLOW;

static String serial_buf = "";

// -- Display layout constants (320 x 240) -------------------------------------

static constexpr int W = 320;
static constexpr int H = 240;

static constexpr int ROW_TITLE  = 5;
static constexpr int ROW_UID_L  = 55;
static constexpr int ROW_UID_V  = 80;
static constexpr int ROW_STATS  = 130;
static constexpr int ROW_STATUS = 195;

// -- Helpers ------------------------------------------------------------------

static String normalise_uid(const std::string& raw) {
    String s = raw.c_str();
    s.replace(":", "");
    s.replace(" ", "");
    s.toUpperCase();
    return s;
}

static String uptime_str() {
    uint32_t s = millis() / 1000;
    uint32_t h = s / 3600; s %= 3600;
    uint32_t m = s / 60;   s %= 60;
    char buf[16];
    snprintf(buf, sizeof(buf), "%02lu:%02lu:%02lu", h, m, s);
    return String(buf);
}

// -- Serial emitters ----------------------------------------------------------

static void emit_tag(const String& uid) {
    Serial.print("{\"event\":\"tag\",\"uid\":\"");
    Serial.print(uid);
    Serial.print("\",\"ts_ms\":");
    Serial.print(millis());
    Serial.println("}");
}

static void emit_hb() {
    Serial.print("{\"event\":\"hb\",\"ts_ms\":");
    Serial.print(millis());
    Serial.println("}");
}

static void emit_error(const char* msg) {
    Serial.print("{\"event\":\"error\",\"msg\":\"");
    Serial.print(msg);
    Serial.print("\",\"ts_ms\":");
    Serial.print(millis());
    Serial.println("}");
}

// -- Serial input (commands from Jetson) --------------------------------------

static void poll_serial_input() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n') {
            serial_buf.trim();
            if (serial_buf.indexOf("\"scan\"") >= 0) {
                // Reset last_uid so the next detection of any tag — including
                // the same one that was just read — is treated as is_new=true.
                last_uid     = "";
                last_emit_ms = 0;
            }
            serial_buf = "";
        } else {
            serial_buf += c;
        }
    }
}

// -- Display ------------------------------------------------------------------

static void draw_static_frame() {
    auto& d = M5.Display;
    d.fillScreen(C_BG);

    d.fillRect(0, 0, W, 45, C_HEADER);
    d.setTextColor(C_WHITE);
    d.setTextSize(2);
    d.setTextDatum(TC_DATUM);
    d.drawString("RFID Node", W / 2, ROW_TITLE + 4);
    d.setTextSize(1);
    d.setTextColor(C_GREY);
    d.drawString(String("WS1850S  |  RFID2  |  ") + FW_VERSION, W / 2, ROW_TITLE + 26);

    d.drawFastHLine(10, 48, W - 20, C_ACCENT);

    d.setTextColor(C_GREY);
    d.setTextDatum(TL_DATUM);
    d.setTextSize(1);
    d.drawString("Last Tag UID", 14, ROW_UID_L);
    d.drawString("Tags",        14,  ROW_STATS);
    d.drawString("Heartbeats", 115,  ROW_STATS);
    d.drawString("Uptime",     230,  ROW_STATS);

    d.drawFastHLine(10, 185, W - 20, C_ACCENT);
    d.setTextColor(C_GREY);
    d.drawString("Status", 14, ROW_STATUS - 14);
}

static void update_uid_area() {
    auto& d = M5.Display;
    d.fillRect(0, ROW_UID_V - 2, W, 45, C_BG);
    d.setTextDatum(TC_DATUM);
    d.setTextSize(3);
    d.setTextColor(display_uid == "---" ? C_GREY : C_GREEN);
    d.drawString(display_uid, W / 2, ROW_UID_V);
}

static void update_stats_area() {
    auto& d = M5.Display;
    d.fillRect(0, ROW_STATS + 14, W, 40, C_BG);
    d.setTextSize(2);
    d.setTextDatum(TL_DATUM);
    d.setTextColor(C_WHITE);
    d.drawString(String(tag_count), 14,  ROW_STATS + 16);
    d.drawString(String(hb_count),  115, ROW_STATS + 16);
    d.setTextColor(C_GREY);
    d.setTextSize(1);
    d.drawString(uptime_str(), 230, ROW_STATS + 22);
}

static void update_status_area() {
    auto& d = M5.Display;
    d.fillRect(0, ROW_STATUS, W, H - ROW_STATUS, C_BG);
    d.setTextSize(2);
    d.setTextDatum(TL_DATUM);
    d.setTextColor(status_color);
    d.drawString(status_msg, 14, ROW_STATUS);
}

static void set_status(const String& msg, uint32_t color) {
    status_msg   = msg;
    status_color = color;
    update_status_area();
}

// -- Setup --------------------------------------------------------------------

void setup() {
    auto cfg = M5.config();
    M5.begin(cfg);
    Serial.begin(SERIAL_BAUD);

    draw_static_frame();
    update_uid_area();
    update_stats_area();
    set_status("Initialising...", C_YELLOW);

    auto pin_sda = M5.getPin(m5::pin_name_t::port_a_sda);
    auto pin_scl = M5.getPin(m5::pin_name_t::port_a_scl);
    Wire.end();
    Wire.begin(pin_sda, pin_scl, 400000U);

    if (Units.add(unit, Wire) && Units.begin()) {
        reader_ok = true;
        set_status("Ready", C_GREEN);
    } else {
        emit_error("init_failed");
        reader_ok = false;
        set_status("Reader offline", C_RED);
    }

    emit_hb();
    last_hb_ms   = millis();
    last_disp_ms = millis();
}

// -- Loop ---------------------------------------------------------------------

void loop() {
    M5.update();
    Units.update();
    uint32_t now = millis();

    // -- Commands from Jetson -------------------------------------------------
    poll_serial_input();

    // -- Heartbeat ------------------------------------------------------------
    if (now - last_hb_ms >= HB_INTERVAL_MS) {
        emit_hb();
        hb_count++;
        last_hb_ms = now;
        update_stats_area();
    }

    // -- Display refresh ------------------------------------------------------
    if (now - last_disp_ms >= DISP_INTERVAL_MS) {
        last_disp_ms = now;
        update_stats_area();
    }

    // -- Card poll (200 ms) ---------------------------------------------------
    if (!reader_ok || (now - last_poll_ms < POLL_INTERVAL_MS)) return;
    last_poll_ms = now;

    // Use wakeup() / WUPA instead of detect() / REQA.
    // REQA only reaches cards in IDLE state.  After deactivate() the card
    // enters HALT and will not respond to REQA until the RF field resets,
    // which never happens during normal 200 ms polling.  WUPA wakes cards in
    // both IDLE and HALT, so the same card is reliably re-detected every poll.
    uint16_t atqa{};
    m5::nfc::a::PICC picc{};
    if (nfc_a.wakeup(atqa)) {
        picc.atqa = atqa;
        if (nfc_a.select(picc)) {
            // UID is available from the anticollision/select step — no need
            // for identify() which would add a redundant WUPA + deactivate.
            String uid = normalise_uid(picc.uidAsString());
            if (!uid.isEmpty()) {
                bool is_new    = uid != last_uid;
                bool due_again = (now - last_emit_ms) >= RE_EMIT_INTERVAL_MS;
                if (is_new || due_again) {
                    emit_tag(uid);
                    last_emit_ms = now;
                    if (is_new) {
                        tag_count++;
                        last_uid    = uid;
                        display_uid = uid;
                        set_status("Tag read OK", C_GREEN);
                        update_uid_area();
                        update_stats_area();
                    }
                }
            }
        }
        // Deactivate (HALT) so the card releases the RF field cleanly.
        // Next poll uses WUPA so HALT state is not a problem.
        nfc_a.deactivate();
    } else {
        if (last_uid != "") {
            last_uid     = "";
            last_emit_ms = 0;
        }
    }
}
