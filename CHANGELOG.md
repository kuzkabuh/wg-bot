# Changelog

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