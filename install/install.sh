#!/usr/bin/env bash
set -e

# Этот скрипт устанавливает зависимости и готовит systemd-юнит для бота

if [ "$EUID" -ne 0 ]; then
  echo "Запустите как root: sudo bash install.sh"
  exit 1
fi

apt update
apt install -y python3-venv python3-pip wireguard qrencode

# Создадим venv и поставим зависимости
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Создадим директории данных
mkdir -p data/configs data/qrcodes

# Скопируем sudoers (права для wg)
install -m 0440 install/sudoers.d.wg-bot /etc/sudoers.d/wg-bot

# Установим systemd-юнит
install -m 0644 install/wg-bot.service /etc/systemd/system/wg-bot.service
systemctl daemon-reload

echo "Готово. Настройте .env, затем:
systemctl enable wg-bot
systemctl start wg-bot
journalctl -u wg-bot -f
"
