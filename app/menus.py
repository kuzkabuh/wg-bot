import time
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.context import SET
from typing import Optional, List, Tuple

# ------- Витрина тарифов -------
BASE_PRICE_1DEV = 100    # 1 месяц, 1 устройство
BASE_PRICE_UNLIM = 250   # 1 месяц, безлимит
DISCOUNTS = {3: 0.25, 6: 0.50, 12: 0.75}

def price_with_discount(base: int, months: int) -> int:
    return int(base * months * (1.0 - DISCOUNTS.get(months, 0.0)) + 1e-9)

def tariff_catalog() -> List[Tuple[str, str]]:
    items = []
    items.append(("🎁 Бесплатный · 7 дней · 1 устройство", "buy:trial:0:0"))
    items.append((f"⭐ 100 звёзд · 1 месяц · 1 устройство", f"buy:one:1:{BASE_PRICE_1DEV}"))
    items.append((f"⭐ 250 звёзд · 1 месяц · безлимит", f"buy:unlim:1:{BASE_PRICE_UNLIM}"))
    for m in (3, 6, 12):
        p1 = price_with_discount(BASE_PRICE_1DEV, m)
        p2 = price_with_discount(BASE_PRICE_UNLIM, m)
        items.append((f"⭐ {p1} звёзд · {m} мес · 1 устройство (скидка {int(DISCOUNTS[m]*100)}%)",
                      f"buy:one:{m}:{p1}"))
        items.append((f"⭐ {p2} звёзд · {m} мес · безлимит (скидка {int(DISCOUNTS[m]*100)}%)",
                      f"buy:unlim:{m}:{p2}"))
    return items

def tariffs_inline_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for text, cb in tariff_catalog():
        kb.button(text=text, callback_data=cb)
    kb.adjust(1)
    return kb.as_markup()

# ------- Динамическое меню пользователя -------

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def user_menu_for(user_row: Optional[dict], is_admin: bool, used_slots: int, has_room: bool) -> ReplyKeyboardMarkup:
    now = int(time.time())
    plan = user_row.get("plan") if user_row else "none"
    exp = (user_row or {}).get("expires_at")
    active = (plan == "unlimited") or (exp and exp > now)

    rows = []

    if plan == "none":
        rows.append([KeyboardButton(text="🎫 Получить 7 дней"),
                     KeyboardButton(text="💰 Оплатить доступ")])

    elif plan == "trial":
        rows.append([KeyboardButton(text="💰 Оплатить доступ")])

    elif plan == "paid":
        if active:
            rows.append([KeyboardButton(text="🔁 Продлить доступ")])
        else:
            rows.append([KeyboardButton(text="💰 Оплатить доступ")])

    elif plan == "unlimited":
        pass  # никаких оплат/продлений

    if active and has_room:
        rows.append([KeyboardButton(text="➕ Создать устройство")])

    rows.append([KeyboardButton(text="👤 Профиль")])
    rows.append([KeyboardButton(text="❓ Помощь")])
    if is_admin:
        rows.append([KeyboardButton(text="🛠 Админ-панель")])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие…"
    )

# ------- Кнопки устройств -------
def devices_inline_kb(peer_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬇️ .conf", callback_data=f"conf:{peer_id}")
    kb.button(text="🧾 QR", callback_data=f"qr:{peer_id}")
    kb.button(text="✏️ Переименовать", callback_data=f"ren:{peer_id}")
    kb.button(text="🗑 Удалить", callback_data=f"rm:{peer_id}")
    kb.adjust(2,2)
    return kb.as_markup()

# ------- Админ-меню -------
def admin_inline_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Последние пользователи", callback_data="admin:users")
    kb.button(text="📶 Счётчик пиров", callback_data="admin:peers")
    kb.button(text="📋 Пиры (детально)", callback_data="admin:peers_detail")
    kb.button(text="💾 Сохранить wg0.conf", callback_data="admin:saveconf")
    kb.button(text="📄 Пиры из wg0.conf", callback_data="admin:conf_list")
    kb.button(text="🎟 Выдать месяц (paid)", callback_data="admin:grant_paid")
    kb.button(text="♾ Выдать безлимит навсегда", callback_data="admin:grant_unlim")
    kb.button(text="⏳ Продлить (дней)", callback_data="admin:extend")
    kb.button(text="🔢 Лимит устройств", callback_data="admin:setlimit")
    kb.button(text="🧷 Создать устройство", callback_data="admin:mkdevice")
    kb.button(text="🧹 Отозвать все устройства", callback_data="admin:revoke_all")
    kb.adjust(2,2,2,3)
    return kb.as_markup()
