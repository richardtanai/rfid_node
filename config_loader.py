"""Load rfid_params.yaml for the RFID node.

Env vars (in priority order):
  RFID_NODE_CONFIG   — path to the params YAML
  RFID_NODE_SECRETS  — path to a secrets overlay YAML
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "spb_node_common"))

from config_base import (  # noqa: E402
    BrokerConfig, NodeConfigBase, deep_get, load_yaml_with_secrets,
)

_HERE = Path(__file__).parent


class RfidConfig(NodeConfigBase):
    """Typed view over rfid_params.yaml."""

    # ── RFID reader ───────────────────────────────────────────────────────

    @property
    def rfid_port(self) -> str:
        return deep_get(self._d, "rfid", "port", default="/dev/ttyUSB0")

    @property
    def rfid_baud_rate(self) -> int:
        return int(deep_get(self._d, "rfid", "baud_rate", default=115200))

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
            anchor_dir=_HERE,
        )
        _cached = RfidConfig(data)
    return _cached
