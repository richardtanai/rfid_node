"""RFID driver node — thin ROS wrapper around RfidReader (hardware only).

No PackML, no MQTT. On a `rfid/trigger` (Empty) it performs one blocking
scan and publishes the outcome on `rfid/scan_result` (String JSON):

    {"tag_id": "A1B2C3D4", "ts_ms": 1718...}      on success
    {"error": "ScanTimeout"}                       no tag within scan_timeout_s
    {"error": "ReaderOffline"}                     serial lost / heartbeat lost

The bridge node owns the state machine and maps these to PackML/alarms.
"""

import json
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from std_msgs.msg import Bool, Empty, String

_TRANSIENT_LOCAL_QOS = QoSProfile(
    depth=1,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
    reliability=ReliabilityPolicy.RELIABLE,
)

from .config_loader import load_config
from .rfid import RfidReader


class RfidDriverNode(Node):
    def __init__(self):
        super().__init__("rfid_driver_node")
        self._cfg = load_config()
        self.declare_parameter("port", "")
        port_override = self.get_parameter("port").value.strip()
        if port_override:
            self._cfg._d.setdefault("rfid", {})["port"] = port_override
        self._reader = RfidReader(self._cfg)
        self._scanning = False

        self._result_pub   = self.create_publisher(String, "rfid/scan_result",       10)
        self._tag_pub      = self.create_publisher(String, "rfid/tag",               10)
        self._online_pub   = self.create_publisher(Bool,   "rfid/driver_online",     _TRANSIENT_LOCAL_QOS)
        self._fw_ver_pub   = self.create_publisher(String, "rfid/firmware_version",  _TRANSIENT_LOCAL_QOS)
        self._fw_match_pub = self.create_publisher(Bool,   "rfid/firmware_match",    _TRANSIENT_LOCAL_QOS)
        self.create_subscription(Empty,  "rfid/trigger", self._on_trigger, 10)
        self.create_subscription(String, "rfid/cmd",     self._on_cmd,     10)

        # Try to open the serial port up front; non-fatal if absent.
        # Publish driver_online=True only when serial port opens successfully.
        try:
            self._reader.start()
            self._reader.set_tag_callback(self._on_tag_detected)
            self._check_firmware_version()
            online_msg = Bool(); online_msg.data = True
            self._online_pub.publish(online_msg)
        except RuntimeError as exc:
            self.get_logger().warn(f"Reader not available at startup: {exc}")

        self.get_logger().info(
            f"rfid_driver_node up — port={self._cfg.rfid_port} "
            f"baud={self._cfg.rfid_baud_rate}")

    def _check_firmware_version(self):
        expected = self._cfg.expected_firmware_version
        ver = self._reader.get_version(timeout=3.0)
        ver_str = ver if ver else "UNKNOWN"

        fw_msg = String(); fw_msg.data = ver_str
        self._fw_ver_pub.publish(fw_msg)

        if not ver:
            self.get_logger().warn(
                "[RFID] Firmware version check timed out — "
                "M5Stack may be running old firmware without version support")
            match = False
        elif not expected:
            self.get_logger().info(f"[RFID] Firmware version: {ver} (no expected version configured)")
            match = True
        elif ver == expected:
            self.get_logger().info(f"[RFID] Firmware version: {ver} ✓ matches expected")
            match = True
        else:
            self.get_logger().warn(
                f"[RFID] Firmware MISMATCH: device={ver}, expected={expected} — "
                "update the M5Stack firmware to avoid protocol errors")
            match = False

        match_msg = Bool(); match_msg.data = match
        self._fw_match_pub.publish(match_msg)

    def _on_tag_detected(self, result):
        """Called from the serial background thread on every tag detection."""
        msg = String()
        msg.data = result.tag_id
        self._tag_pub.publish(msg)

    def _on_cmd(self, msg: String):
        """Route rfid/cmd 'start' to a scan trigger (no bridge needed for manual scans)."""
        if msg.data.strip().lower() == 'start':
            self._on_trigger(Empty())

    def _on_trigger(self, _msg: Empty):
        if self._scanning:
            self.get_logger().warn("Scan already in progress — trigger ignored")
            return
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        self._scanning = True
        try:
            try:
                self._reader.start()  # defensive (re)open if the thread died
            except RuntimeError:
                self._publish({"error": "ReaderOffline"})
                return
            try:
                result = self._reader.read_one_tag()
            except RuntimeError as exc:
                err = "ScanTimeout" if "ScanTimeout" in str(exc) else "ReaderOffline"
                self._publish({"error": err})
                return
            self._publish({"tag_id": result.tag_id, "ts_ms": result.timestamp_ms})
        finally:
            self._scanning = False

    def _publish(self, data: dict):
        m = String(); m.data = json.dumps(data)
        self._result_pub.publish(m)
        self.get_logger().info(f"scan_result: {m.data}")

    def destroy_node(self):
        try:
            self._reader.stop()
        except Exception:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RfidDriverNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
