import time
import html
from datetime import datetime
from aiogram.types import CallbackQuery, ReplyKeyboardRemove

from app.context import DBH
from app.menus import user_menu_for
from typing import Optional

def H(s: object) -> str:
    return html.escape(str(s), quote=False)

def months_to_seconds(months: int) -> int:
    return months * 30 * 86400

async def show_tariffs_message(target, header: Optional[str] = None):
    from app.menus import tariffs_inline_menu
    title = header or ("💡 <b>Тарифы</b>\nВыберите подходящий вариант. "
                       "Оплата звёздами сейчас — заглушка, списания нет.")
    await target.answer(title, reply_markup=tariffs_inline_menu())

def _has_room_for_menu(u: dict) -> bool:
    # локальная проверка, чтобы не тащить импорт из handlers (без циклов)
    if u["plan"] == "unlimited":
        return True
    limit = u["devices_limit"]
    if limit < 0:
        return True
    # считаем активные пиры
    return DBH.count_active_peers(u["id"]) < limit

async def _refresh_menu_after_purchase(c: CallbackQuery):
    """Удаляем старую клавиатуру и отправляем новую после оформления тарифа."""
    u = DBH.get_user_by_tg(c.from_user.id)
    await c.message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
    menu = user_menu_for(u, (c.from_user.id in (u.get("admin_ids", []) if isinstance(u, dict) else [])),
                         DBH.count_active_peers(u["id"]), _has_room_for_menu(u))
    # is_admin берём через контекст отдельно — проще так:
    from app.context import SET
    is_admin = c.from_user.id in SET.admin_ids
    menu = user_menu_for(u, is_admin, DBH.count_active_peers(u["id"]), _has_room_for_menu(u))
    await c.message.answer("🏠 Меню обновлено. Выберите действие:", reply_markup=menu)

async def handle_buy_callback(c: CallbackQuery):
    # buy:<tier>:<months>:<stars>
    try:
        _, tier, months_s, stars_s = c.data.split(":")
        months = int(months_s)
    except Exception:
        await c.answer("Некорректный параметр", show_alert=True); return

    u = DBH.get_user_by_tg(c.from_user.id)
    now = int(time.time())

    if tier == "trial":
        DBH.set_plan(u["id"], plan="trial", devices_limit=1, expires_at=now + 7*86400)
        await c.message.answer("✅ Активирован бесплатный триал: 7 дней, 1 устройство. Можно создавать устройство!")
        await _refresh_menu_after_purchase(c)
        await c.answer(); return

    devices_limit = 1 if tier == "one" else -1
    plan = "paid"
    start = u["expires_at"] if (u["expires_at"] and u["plan"] == "paid" and u["expires_at"] > now) else now
    expires = start + months_to_seconds(months)

    DBH.set_plan(u["id"], plan=plan, devices_limit=devices_limit, expires_at=expires)

    human = "1 устройство" if devices_limit == 1 else "безлимит устройств"
    await c.message.answer(
        f"✅ Тариф оформлен: <b>{months} мес</b>, <b>{human}</b>.\n"
        f"Срок до: <b>{datetime.utcfromtimestamp(expires).strftime('%Y-%m-%d %H:%M UTC')}</b>\n"
        f"Создайте или откройте устройство в «👤 Профиль» → «🔌 Мои подключения»."
    )
    await _refresh_menu_after_purchase(c)
    await c.answer()
