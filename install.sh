#!/usr/bin/env bash
set -euo pipefail

# === Параметры ===
APP_DIR="/opt/wg-bot"
APP_USER="wg-bot"
DATA_DIR="/var/lib/wg-bot"

if [[ $EUID -ne 0 ]]; then
  echo "Запустите как root: sudo bash install.sh"
  exit 1
fi

# 1) Зависимости
apt update
apt install -y python3 python3-venv python3-pip wireguard wireguard-tools

# 2) Пользователь и директории
id -u "$APP_USER" &>/dev/null || useradd -r -s /bin/false -d "$APP_DIR" "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR" "$DATA_DIR"

# 3) Копируем проект
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
rsync -a --exclude '.env' "$SRC_DIR/" "$APP_DIR/"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# 4) venv + зависимости
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" bash -lc "$APP_DIR/venv/bin/pip install -r $APP_DIR/requirements.txt"

# 5) .env
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
  echo "⚠️ Заполните $APP_DIR/.env (BOT_TOKEN, ADMIN_IDS и т.д.)"
fi

# 6) sudoers для wg/wg-quick без пароля
cat >/etc/sudoers.d/90-wg-bot <<EOF
$APP_USER ALL=(root) NOPASSWD:/usr/bin/wg,/usr/bin/wg-quick
Defaults:$APP_USER !requiretty
EOF
chmod 440 /etc/sudoers.d/90-wg-bot

# 7) systemd service
cp "$APP_DIR/wg-telegram-bot.service" /etc/systemd/system/wg-telegram-bot.service
systemctl daemon-reload
systemctl enable wg-telegram-bot
echo "✅ Установка завершена."
echo "1) Отредактируйте $APP_DIR/.env"
echo "2) Затем запустите: systemctl start wg-telegram-bot && systemctl status wg-telegram-bot"
