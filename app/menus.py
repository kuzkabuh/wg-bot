from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

# ===== Тексты кнопок ReplyKeyboard (всегда видимых) =====
PROFILE_BTN_TEXT = "👤 Профиль"
ADMIN_BTN_TEXT   = "🛡 Админ-панель"

def persistent_main_kb(is_admin: bool) -> ReplyKeyboardMarkup:
    """
    Постоянная клавиатура, всегда видна пользователю.
    Минимальная: только Профиль (+ Админ для админа).
    """
    rows = [[KeyboardButton(text=PROFILE_BTN_TEXT)]]
    if is_admin:
        rows.append([KeyboardButton(text=ADMIN_BTN_TEXT)])
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        is_persistent=True,
        keyboard=rows
    )

# ===== Inline-меню (контекстные кнопки под сообщениями) =====

def new_user_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🆕 Регистрация", callback_data="reg")
    kb.button(text="ℹ️ О боте", callback_data="about")
    kb.adjust(1)
    return kb.as_markup()

def user_menu(*, is_admin: bool=False) -> InlineKeyboardMarkup:
    """
    Главное действие для авторизованного — зайти в профиль.
    Никаких 'конфиг/добавить/статистика' снаружи.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Профиль", callback_data="profile")
    if is_admin:
        kb.button(text="🛡 Админ-панель", callback_data="admin_panel")
    kb.adjust(1, 1)
    return kb.as_markup()

def profile_menu(is_unlimited: bool, devices_limit: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Мои устройства", callback_data="my_peers")
    if not is_unlimited:
        kb.button(text="💳 Оплатить месяц", callback_data="pay_month")
    kb.button(text="⬅️ Назад", callback_data="back_main")
    kb.adjust(1, 1, 1)
    return kb.as_markup()

def peers_menu(peer_ids_with_names):
    """
    Экран «Мои устройства»:
    - верхний ряд: Добавить / Статистика
    - ниже: список устройств (по клику — меню устройства)
    - внизу: Назад
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить",      callback_data="add_peer")
    kb.button(text="📊 Статистика",    callback_data="peers_stats")
    for pid, nm in peer_ids_with_names:
        kb.button(text=f"⚙️ {nm}", callback_data=f"peer:{pid}")
    kb.button(text="⬅️ Назад", callback_data="profile")
    kb.adjust(2, 1, 1)
    return kb.as_markup()

def peer_actions_menu(pid: int) -> InlineKeyboardMarkup:
    """Меню конкретного пира: конфиг / QR / удалить / назад к списку."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Конфиг",   callback_data=f"peerconf:{pid}")
    kb.button(text="🔳 QR-код",   callback_data=f"peerqr:{pid}")
    kb.button(text="🗑 Удалить",  callback_data=f"delpeer:{pid}")
    kb.button(text="⬅️ К списку устройств", callback_data="my_peers")
    kb.adjust(2, 2)
    return kb.as_markup()

def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Пользователи", callback_data="adm_users")
    kb.button(text="🔗 Пиры",        callback_data="adm_peers")
    kb.button(text="🛠 Инструменты", callback_data="adm_tools")
    kb.button(text="📝 Логи",        callback_data="adm_logs")
    kb.adjust(2, 2)
    return kb.as_markup()

def admin_user_actions(tg_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎟 Триал 7д",        callback_data=f"adm_trial:{tg_id}")
    kb.button(text="📆 +30 дней",        callback_data=f"adm_month:{tg_id}")
    kb.button(text="♾ Безлимит",         callback_data=f"adm_unlim:{tg_id}")
    kb.button(text="📉 Лимит устройств", callback_data=f"adm_limit:{tg_id}")
    kb.adjust(3, 1)
    return kb.as_markup()
