# Changelog

## 0.2.1 — Navigation & WG sync
- Минимальная постоянная клавиатура (Профиль / +Админ у админа).
- «Профиль → Мои устройства»: Добавить / Статистика / Конфиг/QR/Удалить.
- Клиентский конфиг собирается из живого состояния сервера (public key, listen-port), шаблон:
  MTU=1420, DNS=1.1.1.1, AllowedIPs=0.0.0.0/0, Keepalive=21.
- Дедуп IP: учитываем и рантайм, и /etc/wireguard/wg0.conf.
- Вебхук WGDashboard + автосинк БД, стартовая resync при запуске.
- Улучшен bot.py: aiogram v3 DefaultBotProperties, uvloop, логирование.
