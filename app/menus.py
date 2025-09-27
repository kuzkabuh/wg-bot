"""Keyboard and menu constructors for the WireGuard bot.

This module centralises the creation of reply and inline keyboards used
throughout the bot.  By keeping menu definitions here, handlers can
reuse them without duplicating layout logic.  Each function below
returns an `InlineKeyboardMarkup` or `ReplyKeyboardMarkup` with a
clear docstring explaining its intended use.
"""

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

# ===== Тексты кнопок ReplyKeyboard (всегда видимых) =====
# Users always see these buttons at the bottom of the chat when the bot is active.
PROFILE_BTN_TEXT = "👤 Профиль"
ADMIN_BTN_TEXT   = "🛡 Админ-панель"

def persistent_main_kb(is_admin: bool) -> ReplyKeyboardMarkup:
    """Return a persistent reply keyboard for the main navigation.

    The keyboard contains a single row with a "Профиль" button.  If the
    `is_admin` flag is true, a second row with an "Админ-панель" button
    is added.  The keyboard is persistent so that the user can always
    navigate back to their profile or the admin panel.
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
    """Return an inline menu shown to unregistered users.

    Provides two options: register to start using the service, or show
    information about the bot.  Only one column is used.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="🆕 Регистрация", callback_data="reg")
    kb.button(text="ℹ️ О боте", callback_data="about")
    kb.adjust(1)
    return kb.as_markup()

def user_menu(*, is_admin: bool=False) -> InlineKeyboardMarkup:
    """Return the main inline menu for a registered user.

    All users see a "Профиль" button to open their profile.  Admin users
    additionally see an "Админ-панель" button to access administrative
    functions.  Each button is placed on its own row for clarity.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Профиль", callback_data="profile")
    if is_admin:
        kb.button(text="🛡 Админ-панель", callback_data="admin_panel")
    kb.adjust(1, 1)
    return kb.as_markup()

def profile_menu(is_unlimited: bool, devices_limit: int) -> InlineKeyboardMarkup:
    """Return an inline menu displayed on the user's profile screen.

    The menu includes a button to list the user's devices, an optional
    payment button when the plan is limited, and a back button to
    return to the main screen.  Rows are balanced for a compact layout.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Мои устройства", callback_data="my_peers")
    if not is_unlimited:
        kb.button(text="💳 Оплатить месяц", callback_data="pay_month")
    kb.button(text="⬅️ Назад", callback_data="back_main")
    kb.adjust(1, 1, 1)
    return kb.as_markup()

def peers_menu(peer_ids_with_names):
    """Return an inline menu listing all of a user's devices.

    The menu is composed of several logical rows:

    * The first row contains two buttons: **Add** to create a new device
      and **Statistics** to view aggregate usage across all devices.
    * Each subsequent row contains exactly one button for an individual
      peer.  The button text is prefixed with a gear emoji and uses
      the peer's friendly name.  The callback data embeds the peer
      identifier so handlers can look up the record.
    * The final row contains a back button to return to the profile.

    The call to ``adjust`` uses a variable-length signature to ensure
    that the keyboard has the correct number of rows regardless of
    how many peers are present.  ``kb.adjust`` accepts one integer
    per row specifying the number of columns in that row.  By
    unpacking a list of ``1`` values equal to the number of peers we
    guarantee that each peer is shown on its own row.

    Parameters
    ----------
    peer_ids_with_names: iterable of tuples
        Each tuple is ``(peer_id, name)`` and will produce a button
        labelled ``"⚙️ {name}"`` that sends callback data
        ``peer:{peer_id}``.

    Returns
    -------
    InlineKeyboardMarkup
        The constructed keyboard markup ready to be passed to Telegram.
    """
    kb = InlineKeyboardBuilder()
    # First row: add and stats buttons
    kb.button(text="➕ Добавить",      callback_data="add_peer")
    kb.button(text="📊 Статистика",    callback_data="peers_stats")
    # Middle rows: one button per peer
    for pid, nm in peer_ids_with_names:
        kb.button(text=f"⚙️ {nm}", callback_data=f"peer:{pid}")
    # Last row: back to profile
    kb.button(text="⬅️ Назад", callback_data="profile")
    # Compose row pattern: two buttons in first row, then one per peer, then one for back
    row_pattern = [2] + [1] * len(peer_ids_with_names) + [1]
    kb.adjust(*row_pattern)
    return kb.as_markup()

def peer_actions_menu(pid: int) -> InlineKeyboardMarkup:
    """Return the inline menu for actions on a specific peer.

    Buttons allow the user to download the configuration, display a QR
    code, delete the peer, or return to the list of devices.  Two
    columns are used for a balanced layout.

    Parameters
    ----------
    pid: int
        The ID of the peer; it is embedded in callback data so the
        handler knows which peer is being acted on.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Конфиг",   callback_data=f"peerconf:{pid}")
    kb.button(text="🔳 QR-код",   callback_data=f"peerqr:{pid}")
    kb.button(text="🗑 Удалить",  callback_data=f"delpeer:{pid}")
    kb.button(text="⬅️ К списку устройств", callback_data="my_peers")
    kb.adjust(2, 2)
    return kb.as_markup()

def admin_menu() -> InlineKeyboardMarkup:
    """Return the main inline menu for the admin panel.

    The admin panel groups administrative actions into four buttons: users,
    peers, tools and logs.  Buttons are arranged in a 2x2 grid.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Пользователи", callback_data="adm_users")
    kb.button(text="🔗 Пиры",        callback_data="adm_peers")
    kb.button(text="🛠 Инструменты", callback_data="adm_tools")
    kb.button(text="📝 Логи",        callback_data="adm_logs")
    kb.adjust(2, 2)
    return kb.as_markup()

def admin_user_actions(tg_id: int) -> InlineKeyboardMarkup:
    """Return an inline menu of actions for a specific user in the admin panel.

    Admins can grant a 7‑day trial, add 30 days, grant unlimited plan or
    prompt to set a custom device limit.  The menu is arranged with
    three buttons on the first row and one on the second.

    Parameters
    ----------
    tg_id: int
        Telegram ID of the user being managed.  It is included in the
        callback data so handlers know which record to modify.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="🎟 Триал 7д",        callback_data=f"adm_trial:{tg_id}")
    kb.button(text="📆 +30 дней",        callback_data=f"adm_month:{tg_id}")
    kb.button(text="♾ Безлимит",         callback_data=f"adm_unlim:{tg_id}")
    kb.button(text="📉 Лимит устройств", callback_data=f"adm_limit:{tg_id}")
    kb.adjust(3, 1)
    return kb.as_markup()