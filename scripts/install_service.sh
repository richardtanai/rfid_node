#!/usr/bin/env bash
# Install (or reinstall) the RFID node systemd service.
#
# Usage:
#   bash install_service.sh
#
# Re-run after editing the service file to reload systemd.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="$(dirname "$SCRIPT_DIR")/systemd"
SERVICE_FILE="$SYSTEMD_DIR/rfid_node.service"
SERVICE_NAME="rfid_node.service"

if [ ! -f "$SERVICE_FILE" ]; then
    echo "ERROR: Service file not found: $SERVICE_FILE"
    exit 1
fi

echo "[install_service] Copying $SERVICE_NAME → /etc/systemd/system/"
sudo cp "$SERVICE_FILE" "/etc/systemd/system/$SERVICE_NAME"

echo "[install_service] Reloading systemd daemon"
sudo systemctl daemon-reload

echo "[install_service] Enabling $SERVICE_NAME (auto-start on boot)"
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "Done. Run the following to start now:"
echo "  sudo systemctl start $SERVICE_NAME"
echo ""
echo "Watch logs:"
echo "  journalctl -u $SERVICE_NAME -f"
