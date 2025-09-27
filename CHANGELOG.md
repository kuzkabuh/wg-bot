## 0.1.5 — 2025-09-27

### Added
- Админ видит **то же пользовательское меню**, что и обычный пользователь (reply-клавиатура), и свой «Профиль» (тариф/лимит/свои подключения).
- Новые команды для работы с любыми пирами:
  - `/peer <pid>` — карточка пира (владелец, адреса) и действия.
  - `/link <pid> <tg_id>` — привязка пира к пользователю (актуально для пиров, созданных в WGDashboard).

### Changed
- Админ-панель: списки («Пользователи», «Пиры», «Логи») приходят **одним сообщением**, без дублей и «нарастания».
- HTML полностью экранируется; `<pid>/<tg_id>` и прочие значения **не ломают разметку**.
- Больше **никаких «пустых» сообщений** для прикрепления клавиатуры — клавиатура прикрепляется к реальным текстовым сообщениям.

### Fixed
- `/resync`: устранена ошибка `TelegramBadRequest: text must be non-empty`; теперь сообщение о синхронизации всегда несёт текст и клавиатуру.
- Исключены ошибки вида `can't parse entities: Unsupported start tag`.

### Integration
- WGDashboard: синхронизация внешних пиров через `/resync`, возможность **привязки пиров** к пользователям по `tg_id` командой `/link`.

# Changelog

## 0.1.5 — Исправления админ‑меню и навигации

- **Работа админ‑кнопки**: упорядочена регистрация роутеров — теперь
  обработчики администратора подключаются первыми, поэтому
  нажатие на кнопку «🛡 Админ‑панель» корректно открывает
  админ‑меню вместо возврата на главный экран.
- **Скрытие служебных сообщений**: убран текст «Навигация всегда здесь👇».
  Вместо него бот отправляет невидимый символ (ноль‑ширины), чтобы
  привязать постоянную клавиатуру без лишнего сообщения.
- **Изменено отображение клавиатуры**: настройки навигации и reply
  клавиатура теперь не дублируются и не мешают сообщению пользователя.
- **Версия обновлена до 0.1.5**.

## 0.1.4 — Интеграция с WGDashboard и улучшения конфигурации

- **Поддержка WGDashboard**: добавлен локальный HTTP‑сервер, принимающий веб‑хуки
  от WGDashboard.  Настройка осуществляется через новые переменные
  `WGD_WEBHOOK_ENABLED`, `WGD_WEBHOOK_HOST`, `WGD_WEBHOOK_PORT` и
  `WGD_WEBHOOK_SECRET` в `.env`.  При получении уведомлений бот вызывает
  синхронизацию базы данных с текущим состоянием WireGuard, чтобы peers,
  созданные через веб‑интерфейс, появлялись в боте.
- **Расширены настройки клиента**: добавлены переменные `WG_MTU` и
  `WG_KEEPALIVE` для тонкой настройки MTU и интервала keepalive в
  сгенерированных конфигурациях.  Эти значения по умолчанию равны 1420 и
  21, но могут быть изменены в `.env`.
- **Команда `/resync` для админа**: администраторы теперь могут вручную
  синхронизировать базу данных с живым состоянием WireGuard командой
  `/resync`.  Для удобства список доступных инструментов описан в
  админ‑панели.
- **Документация и пример конфигурации**: обновлены `README.md` и
  `.env.example`, добавлены разделы про интеграцию с WGDashboard и описание
  новых переменных.
- **Версия поднята до 0.1.4**.

## 0.2.3 — Navigation fixes & dynamic AllowedIPs

- **Dynamic AllowedIPs**: client configuration now honours the
  `WG_ALLOWED_IPS` environment variable (falling back to `0.0.0.0/0`) and
  includes the preshared key if one was generated.  This resolves issues
  where the `.conf` file could not be imported by WireGuard or QR
  connections failed due to missing preshared keys.
- **Menu improvements**: the peers list menu now uses a dynamic row
  pattern so that each device appears on its own row, avoiding layout
  glitches when there are multiple devices.  Inline and reply keyboard
  functions have been fully documented.
- **Comprehensive documentation**: every function and module now
  includes a short docstring describing its purpose, parameters and
  return values.  This helps maintainers understand the code and
  satisfies the requirement for descriptive comments.
- **Admin and user handlers**: admin and user handler modules have
  been refactored with clear structure and docstrings.  The admin panel
  remains functionally identical but easier to extend.

## 0.2.2 — Preshared key support & docs

- **Client configuration improvements**: the generated client `.conf` now includes the preshared key and respects the `WG_ALLOWED_IPS` environment variable when setting the `AllowedIPs` field.  This ensures the configuration imports correctly into the WireGuard mobile application and that connections succeed when a preshared key is used.
- **Preshared key parameter**: added an optional `preshared_key` argument to `build_client_conf()` so callers can embed a preshared key into the client configuration.  Call sites in the user handlers have been updated to pass the preshared key stored in the database.
- **Docstrings**: all functions across the codebase now include short docstrings describing their purpose and expected arguments.  This helps developers understand the codebase and satisfies the requirement to explain what each function does.
- **Minor menu tweaks**: some of the reply/inline keyboards have been clarified to prevent duplicate navigation messages.

## 0.2.1 — Navigation & WG sync
- Минимальная постоянная клавиатура (Профиль / +Админ у админа).
- «Профиль → Мои устройства»: Добавить / Статистика / Конфиг/QR/Удалить.
- Клиентский конфиг собирается из живого состояния сервера (public key, listen-port), шаблон:
  MTU=1420, DNS=1.1.1.1, AllowedIPs=0.0.0.0/0, Keepalive=21.
- Дедуп IP: учитываем и рантайм, и /etc/wireguard/wg0.conf.
- Вебхук WGDashboard + автосинк БД, стартовая resync при запуске.
- Улучшен bot.py: aiogram v3 DefaultBotProperties, uvloop, логирование.