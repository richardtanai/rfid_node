# rfid_node

M5Stack + WS1850S RFID reader node with Sparkplug B (ISA-95) MQTT bridge.

Runs standalone — **no ROS 2 dependency**. Reads RFID tags over USB serial and
publishes results to a SCADA system via MQTT Sparkplug B.

---

## Package layout

```
rfid_node/
├── rfid_spb_node.py         Sparkplug B bridge (headless, main entry point)
├── rfid.py                  RfidReader — serial comms with M5Stack
├── config_loader.py         Loads rfid_params.yaml + rfid_secrets.yaml
├── config/
│   ├── rfid_params.yaml     Main configuration (SPB identity, serial port, timeouts)
│   ├── rfid_secrets.yaml    Gitignored broker credentials (copy from .example)
│   └── rfid_secrets.example.yaml
├── firmware/                M5Stack firmware source
├── systemd/
│   └── rfid_node.service    systemd service unit
├── scripts/
│   └── install_service.sh   One-shot systemd service installer
└── README.md                ← this file
```

---

## Dependencies

Install once on the Jetson:

```bash
# MQTT
pip3 install paho-mqtt

# Sparkplug B
pip3 install tahu

# Serial
pip3 install pyserial
```

Verify the M5Stack is visible:

```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

Give the current user serial port access (once):

```bash
sudo usermod -aG dialout $USER
# log out and back in for it to take effect
```

---

## Configuration

### 1 — Fill in broker credentials

```bash
cp config/rfid_secrets.example.yaml config/rfid_secrets.yaml
nano config/rfid_secrets.yaml
```

`rfid_secrets.yaml` is **gitignored** — never commit credentials. It is
deep-merged on top of `rfid_params.yaml` at load time.

### 2 — Set the serial port

Edit `config/rfid_params.yaml`:

```yaml
rfid:
  port: /dev/ttyUSB0    # adjust to match your device
  baud_rate: 115200
```

### Key parameters

| Key | Default | Meaning |
|-----|---------|---------|
| `rfid.port` | `/dev/ttyUSB0` | USB serial port of the M5Stack |
| `rfid.baud_rate` | `115200` | Serial baud rate |
| `rfid.scan_timeout_s` | `10.0` | Seconds before alarm 9002 (ScanTimeout) fires |
| `rfid.hb_timeout_s` | `15.0` | Seconds without heartbeat before alarm 9001 fires |
| `spb_bridge.broker_type` | `local` | `local` \| `hivemq` |
| `spb_bridge.heartbeat_interval_s` | `0.5` | SCADA heartbeat toggle rate |

---

## Running

```bash
cd ~/agx_arm_ws/src/rfid_node
python3 rfid_spb_node.py

# Override broker
RFID_BROKER_TYPE=hivemq python3 rfid_spb_node.py

# Override config path
RFID_NODE_CONFIG=/path/to/rfid_params.yaml python3 rfid_spb_node.py
```

---

## Environment variables

| Variable | Purpose | Default (if unset) |
|---|---|---|
| `RFID_NODE_CONFIG` | Path to params YAML | `config/rfid_params.yaml` next to the script |
| `RFID_NODE_SECRETS` | Path to secrets overlay YAML | `config/rfid_secrets.yaml` next to the script |
| `RFID_BROKER_TYPE` | `local` or `hivemq`, overrides YAML | Uses `broker_type` from YAML |

---

## PackML state machine

```
             ┌──────┐   Start    ┌─────────┐   tag read    ┌──────────┐
             │ Idle │ ─────────► │ Execute │ ────────────► │ Complete │
             └──────┘            └─────────┘               └──────────┘
                 ▲                    │                          │
                 │ Clear              │ timeout / offline        │ Reset
                 │                    ▼                          │
             ┌─────────┐                                         │
             │ Aborted │ ◄───────────────────────────────────────┘
             └─────────┘
```

---

## Alarm catalogue

| Code | Name | Priority | Cause |
|------|------|----------|-------|
| 9001 | ReaderOffline | Critical | No M5Stack heartbeat within `hb_timeout_s` |
| 9002 | ScanTimeout | High | No tag read within `scan_timeout_s` |
| 9003 | PrimaryHostOffline | Critical | SCADA primary host went offline |

---

## Sparkplug B identity

| Field | Value |
|-------|-------|
| Group ID | `DMATDTS_DLSU_LS_MiniFactory` |
| Edge Node ID | `rfid_node` |
| Device ID | `rfid_reader` |
| DBIRTH topic | `spBv1.0/DMATDTS_DLSU_LS_MiniFactory/DBIRTH/rfid_node/rfid_reader` |
| DDATA topic | `spBv1.0/DMATDTS_DLSU_LS_MiniFactory/DDATA/rfid_node/rfid_reader` |
| DCMD topic | `spBv1.0/DMATDTS_DLSU_LS_MiniFactory/DCMD/rfid_node/rfid_reader` |

### Published metrics (DDATA)

| Metric | Type | Description |
|--------|------|-------------|
| `Status/State/Current/Idle` | Boolean | One-hot state flags |
| `Status/State/Current/Execute` | Boolean | |
| `Status/State/Current/Complete` | Boolean | |
| `Status/State/Current/Aborted` | Boolean | |
| `Status/Heartbeat` | Boolean | Toggles every `heartbeat_interval_s` |
| `Result/Last/TagID` | String | UID of the last scanned tag |
| `Result/Last/TimestampMs` | Int64 | Unix epoch ms of last scan |
| `Alarm/Active/{code}/State` | Int32 | 1 = Normal, 2 = Unacknowledged |
| `Alarm/Active/{code}/Priority` | Int32 | 1 = Critical, 2 = High |
| `Alarm/Active/{code}/Message` | String | Alarm name |
| `Alarm/Summary/ActiveCount` | Int32 | Total active alarms |

### DCMD write tags (SCADA → node)

Write `True` to trigger:

| Metric | Effect |
|--------|--------|
| `Cmd/CntrlCmd/Start` | Begin scan cycle (Idle only) |
| `Cmd/CntrlCmd/Reset` | Complete → Idle |
| `Cmd/CntrlCmd/Stop` | Abort current scan |
| `Cmd/CntrlCmd/Clear` | Clear alarms, Aborted → Idle |

---

## Auto-start on boot (systemd)

```bash
bash ~/agx_arm_ws/src/rfid_node/scripts/install_service.sh
sudo systemctl start rfid_node.service
```

### Service management

```bash
sudo systemctl status  rfid_node.service
sudo systemctl stop    rfid_node.service
sudo systemctl restart rfid_node.service
journalctl -u rfid_node.service -f
sudo systemctl disable rfid_node.service
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `[SPB] ReaderOffline` | M5Stack not connected or wrong port | Check `ls /dev/ttyUSB*`; update `rfid.port` |
| `Permission denied: /dev/ttyUSB0` | User not in `dialout` group | `sudo usermod -aG dialout $USER` then log out/in |
| `[SPB] ScanTimeout` | No tag presented in time | Present tag sooner or increase `scan_timeout_s` |
| `[SPB] Broker rejected connection rc=5` | Wrong credentials | Check `rfid_secrets.yaml` username/password |
| No heartbeat from M5Stack | Firmware not running | Check M5Stack display / reflash firmware |
