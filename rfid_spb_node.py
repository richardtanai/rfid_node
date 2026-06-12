"""Sparkplug B bridge for the RFID node.

Standalone Python — no ROS dependency. Subclasses SpbBridgeBase to add
RFID scan logic, publishing tag results to MQTT via Sparkplug B.

ISA-95 identity: GID=DMATDTS_DLSU_LS_MiniFactory, Node=rfid_node, Device=rfid_reader

Usage:
    python rfid_spb_node.py
    RFID_NODE_CONFIG=/path/to/rfid_params.yaml python rfid_spb_node.py

GUI / panel integration:
    bridge = RfidSpbBridge(
        on_state_change=lambda s: ...,   # called on every PackML state transition
        on_tag_read=lambda uid: ...,     # called with UID string on Complete
    )
    threading.Thread(target=bridge.run, daemon=True).start()
    bridge.stop()
"""

import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "spb_node_common"))

from bridge_base import (  # noqa: E402
    SpbBridgeBase, STATE_EXECUTE, STATE_COMPLETE, STATE_ABORTED,
    MetricDataType, addMetric,
)
from rfid import RfidReader
from config_loader import load_config

# ---------------------------------------------------------------------------
# Node-specific alarm catalogue
# ---------------------------------------------------------------------------
ALARM_DEFINITIONS = {
    9001: (1, "ReaderOffline"),
    9002: (2, "ScanTimeout"),
    9003: (1, "PrimaryHostOffline"),
}


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class RfidSpbBridge(SpbBridgeBase):
    """SPB bridge that drives RfidReader and publishes tag scan results."""

    ALARM_DEFINITIONS    = ALARM_DEFINITIONS
    STATION_STATUS_TOPIC = "station/rfid/status"
    STATION_CMD_TOPIC    = "station/rfid/cmd"
    NODE_BROKER_TYPE_ENV = "RFID_BROKER_TYPE"

    def __init__(
        self,
        on_state_change: Optional[Callable[[str], None]] = None,
        on_tag_read:     Optional[Callable[[str], None]] = None,
        broker_type:     Optional[str] = None,
        cfg=None,
    ):
        """
        on_state_change: called with the new PackML state string on every transition.
        on_tag_read: called with the scanned tag UID string on cycle Complete.
        broker_type: "local" | "hivemq" — overrides rfid_params.yaml.
        """
        if cfg is None:
            cfg = load_config()
        self._on_tag_read = on_tag_read
        self._last_tag_id: str = ""

        super().__init__(cfg, on_state_change=on_state_change, broker_type=broker_type)

    # ------------------------------------------------------------------
    # DBIRTH extra metrics
    # ------------------------------------------------------------------

    def _publish_extra_birth_metrics(self, payload):
        addMetric(payload, self._m("Result/Last/TagID"),       None, MetricDataType.String, "")
        addMetric(payload, self._m("Result/Last/TimestampMs"), None, MetricDataType.Int64,  0)

    # ------------------------------------------------------------------
    # Station status override (adds last_tag)
    # ------------------------------------------------------------------

    def _station_status_dict(self) -> dict:
        d = super()._station_status_dict()
        if self._last_tag_id:
            d["last_tag"] = self._last_tag_id
        return d

    # ------------------------------------------------------------------
    # Scan cycle
    # ------------------------------------------------------------------

    def _start_cycle(self):
        self._set_state(STATE_EXECUTE)
        self._in_flight = True
        self._scan_stop.clear()
        threading.Thread(target=self._run_scan, daemon=True).start()

    def _run_scan(self):
        try:
            reader = RfidReader(self._cfg)
            result = reader.read_one_tag()
        except RuntimeError as exc:
            msg = str(exc)
            if not self._in_flight:
                return
            self._in_flight = False
            if "ScanTimeout" in msg:
                print("[SPB] ScanTimeout")
                self._set_aborted(9002)
            else:
                print(f"[SPB] ReaderOffline: {exc}")
                self._set_aborted(9001)
            return

        if not self._in_flight:
            return

        self._in_flight   = False
        self._last_tag_id = result.tag_id
        print(f"[SPB] Tag read: uid={result.tag_id}")

        self._publish_ddata({
            self._m("Result/Last/TagID"):       (MetricDataType.String, result.tag_id),
            self._m("Result/Last/TimestampMs"): (MetricDataType.Int64,  result.timestamp_ms),
        })
        self._set_state(STATE_COMPLETE)

        if self._on_tag_read is not None:
            try:
                self._on_tag_read(result.tag_id)
            except Exception as exc:
                print(f"[SPB] on_tag_read callback raised: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    bridge = RfidSpbBridge()
    try:
        bridge.run()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
