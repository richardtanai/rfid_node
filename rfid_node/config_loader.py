"""Load rfid_params.yaml for the RFID node.

Resolution priority (see spb_node_common.config_base.locate_config_file):
  1. RFID_NODE_CONFIG / RFID_NODE_SECRETS environment variables
  2. the installed package share dir: share/rfid_node/config/rfid_params.yaml
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from spb_node_common.config_base import (
    BrokerConfig, NodeConfigBase, deep_get, load_yaml_with_secrets,
)


def _share_dir() -> Path:
    return Path(get_package_share_directory("rfid_node"))


class RfidConfig(NodeConfigBase):
    """Typed view over rfid_params.yaml."""

    @property
    def rfid_port(self) -> str:
        return deep_get(self._d, "rfid", "port", default="/dev/ttyUSB0")

    @property
    def rfid_baud_rate(self) -> int:
        return int(deep_get(self._d, "rfid", "baud_rate", default=115200))

    @property
    def expected_firmware_version(self) -> str:
        return str(deep_get(self._d, "rfid", "expected_firmware_version", default=""))

    @property
    def scan_timeout_s(self) -> float:
        return float(deep_get(self._d, "rfid", "scan_timeout_s", default=10.0))

    @property
    def hb_timeout_s(self) -> float:
        return float(deep_get(self._d, "rfid", "hb_timeout_s", default=15.0))


_cached: RfidConfig | None = None


def load_config(reload: bool = False) -> RfidConfig:
    global _cached
    if _cached is None or reload:
        data = load_yaml_with_secrets(
            config_env_var="RFID_NODE_CONFIG",
            secrets_env_var="RFID_NODE_SECRETS",
            default_config_filename="rfid_params.yaml",
            default_secrets_filename="rfid_secrets.yaml",
            anchor_dir=_share_dir(),
        )
        _cached = RfidConfig(data)
    return _cached
