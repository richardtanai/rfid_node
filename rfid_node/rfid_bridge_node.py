"""RFID bridge node — PackML + OMAC authority + Sparkplug B.

Subclasses RosSpbBridgeBase. Owns no hardware: a cycle is run by triggering
the rfid_driver_node over ROS (`rfid/trigger`) and awaiting its
`rfid/scan_result`. Maps driver errors to alarms, publishes the tag UID on
`rfid/tag` for the panel, and bridges to Sparkplug B for SCADA/AUTO mode.
"""

import rclpy
from std_msgs.msg import String

from spb_node_common.ros_bridge_base import (
    RosSpbBridgeBase, MetricDataType, addMetric,
)
from .config_loader import load_config

ALARM_DEFINITIONS = {
    9001: (1, "ReaderOffline"),
    9002: (2, "ScanTimeout"),
    9003: (1, "PrimaryHostOffline"),
}


class RfidBridgeNode(RosSpbBridgeBase):
    ALARM_DEFINITIONS = ALARM_DEFINITIONS
    ROS_NS          = "rfid"
    RESULT_TOPIC    = "rfid/tag"
    DETECTION_TOPIC = "rfid/scan_result"
    TIMEOUT_ALARM   = 9002                       # ScanTimeout
    ERROR_ALARMS    = {"ReaderOffline": 9001, "ScanTimeout": 9002}

    def __init__(self):
        cfg = load_config()
        super().__init__("rfid_bridge_node", cfg)
        self._last_tag = ""

    def _cycle_timeout_s(self) -> float:
        # Slightly longer than the reader's own scan timeout so the driver's
        # ScanTimeout error arrives before this safety net fires.
        return float(self._cfg.scan_timeout_s) + 5.0

    def _handle_result(self, data: dict):
        tag = str(data.get("tag_id", ""))
        ts  = int(data.get("ts_ms", 0))
        self._last_tag = tag
        m = String(); m.data = tag
        self._result_pub.publish(m)
        self._publish_ddata({
            self._m("Result/Last/TagID"):       (MetricDataType.String, tag),
            self._m("Result/Last/TimestampMs"): (MetricDataType.Int64,  ts),
        })

    def _publish_extra_birth_metrics(self, payload):
        addMetric(payload, self._m("Result/Last/TagID"),       None, MetricDataType.String, "")
        addMetric(payload, self._m("Result/Last/TimestampMs"), None, MetricDataType.Int64,  0)


def main(args=None):
    rclpy.init(args=args)
    node = RfidBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
