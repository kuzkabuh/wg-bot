# wg-bot — Telegram-бот для управления WireGuard

Версия: 0.1.1

- Выдаёт .conf и QR для клиентов
- IPv4/IPv6, сохранение в wg0.conf (видно в WGDashboard)
- Профиль со статистикой, админ-панель
- Компактный интерфейс (панель + автoудаление служебных сообщений)

## Быстрый старт
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# настройте токен и параметры в app/context.py
# systemd сервис: /etc/systemd/system/wg-telegram-bot.service
