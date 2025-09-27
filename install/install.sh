#!/usr/bin/env bash
#
# This script installs dependencies and prepares a systemd unit for the bot.
# It must be run as root and is intended for Ubuntu/Debian systems.  It
# installs Python packages, creates the virtual environment, configures
# sudoers entries for WireGuard commands and installs a systemd service
# that runs the bot under a dedicated user.

set -e

if [ "$EUID" -ne 0 ]; then
  echo "Запустите как root: sudo bash install.sh"
  exit 1
fi

apt update
apt install -y python3-venv python3-pip wireguard qrencode

# Create venv and install dependencies
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create data directories
mkdir -p data/configs data/qrcodes

# Copy sudoers (permissions for wg commands)
install -m 0440 install/sudoers.d.wg-bot /etc/sudoers.d/wg-bot

# Install systemd unit
install -m 0644 install/wg-bot.service /etc/systemd/system/wg-bot.service
systemctl daemon-reload

echo "Готово. Настройте .env, затем:
systemctl enable wg-bot
systemctl start wg-bot
journalctl -u wg-bot -f"