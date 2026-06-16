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
from std_msgs.msg import Empty, String

from .config_loader import load_config
from .rfid import RfidReader


class RfidDriverNode(Node):
    def __init__(self):
        super().__init__("rfid_driver_node")
        self._cfg = load_config()
        self._reader = RfidReader(self._cfg)
        self._scanning = False

        self._result_pub = self.create_publisher(String, "rfid/scan_result", 10)
        self.create_subscription(Empty, "rfid/trigger", self._on_trigger, 10)

        # Try to open the serial port up front; non-fatal if absent (the next
        # trigger will report ReaderOffline and the bridge will alarm).
        try:
            self._reader.start()
        except RuntimeError as exc:
            self.get_logger().warn(f"Reader not available at startup: {exc}")

        self.get_logger().info(
            f"rfid_driver_node up — port={self._cfg.rfid_port} "
            f"baud={self._cfg.rfid_baud_rate}")

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
