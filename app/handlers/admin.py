"""Handlers for admin commands and callbacks.

This module defines administrative functionality accessible via
commands and inline menus. Only users whose Telegram IDs appear
in :data:`ctx.SET.ADMIN_IDS` will trigger these handlers. Admins can
inspect users and peers, change subscription plans, view logs and
perform management operations.
"""

from html import escape

from aiogram import Router, F, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

import app.context as ctx
from app.menus import admin_menu, admin_user_actions, ADMIN_BTN_TEXT
from app.services import wg as WG
from app.services.sync import resync_from_runtime

# A dedicated router for admin-only interactions
router = Router(name="admin")


def register_admin_handlers(dp: Dispatcher) -> None:
    """Attach all admin handlers to the dispatcher.

    Parameters
    ----------
    dp: Dispatcher
        The aiogram dispatcher to register handlers on.
    """
    dp.include_router(router)


def is_admin(uid: int) -> bool:
    """Return ``True`` if the given Telegram ID belongs to an admin user."""
    return uid in ctx.SET.ADMIN_IDS


def format_user_row(u) -> str:
    """Format a user record for display in the admin user list.

    Parameters
    ----------
    u: aiosqlite.Row
        A row from the ``users`` table.

    Returns
    -------
    str
        A single-line summary of the user including ID, plan and
        device limits.
    """
    lim = "∞" if (u["devices_limit"] < 0 or u["plan"] == "unlimited") else str(u["devices_limit"])
    return (
        f"#{u['id']} tg:{u['tg_id']} "
        f"@{u['username'] or '-'} "
        f"plan={u['plan']} lim={lim} exp={u['expires_at'] or '-'}"
    )


# ===== Commands and reply keyboard shortcuts =====

@router.message(Command("admin"))
async def admin_cmd(message: Message) -> None:
    """Enter the admin panel via the ``/admin`` command."""
    if not is_admin(message.from_user.id):
        return
    await message.answer("Админ-панель", reply_markup=admin_menu())


# ReplyKeyboard-button "🛡 Админ-панель"
@router.message(F.text == ADMIN_BTN_TEXT)
async def admin_btn(message: Message) -> None:
    """Enter the admin panel from the persistent reply keyboard."""
    if not is_admin(message.from_user.id):
        return
    await message.answer("Админ-панель", reply_markup=admin_menu())


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(c: CallbackQuery) -> None:
    """Inline callback to open the admin panel."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    await c.message.answer("Админ-панель", reply_markup=admin_menu())
    await c.answer()


@router.callback_query(F.data == "adm_tools")
async def cb_adm_tools(c: CallbackQuery) -> None:
    """Admin tools info (manual sync, limits, actions)."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    text = (
        "<b>Инструменты администратора</b>\n\n"
        "Доступные команды:\n"
        "• /resync – синхронизировать базу данных с текущим состоянием WireGuard\n"
        "• /delpeer &lt;pid&gt; – удалить пир из системы\n"
        "• /limit &lt;tg_id&gt; &lt;число&gt; – установить лимит устройств пользователю\n"
        "• /act &lt;tg_id&gt; – открыть меню действий для пользователя\n"
        "\nДругие функции (перезапуск WG, создание резервных копий и пр.)\n"
        "можно добавить здесь при необходимости."
    )
    await c.message.answer(text, parse_mode="HTML")
    await c.answer()


@router.callback_query(F.data == "adm_logs")
async def cb_adm_logs(c: CallbackQuery) -> None:
    """Show the last 50 logged events."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    events = await ctx.DBH.list_events(50)
    if not events:
        await c.message.answer("Логов пока нет.")
        await c.answer()
        return
    lines = ["<b>Последние события</b>"]
    for e in events:
        # Escape dynamic text so HTML parse won't break
        line = f"{escape(str(e['ts']))}: {escape(str(e['level']))} tg:{escape(str(e['tg_id'] or '-'))} {escape(str(e['message']))}"
        lines.append(line)
    await c.message.answer("\n".join(lines[:50]), parse_mode="HTML")
    await c.answer()


@router.callback_query(F.data == "adm_users")
async def cb_adm_users(c: CallbackQuery) -> None:
    """Display a list of users with instructions for taking action."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return

    users = await ctx.DBH.list_users()
    if not users:
        await c.message.answer("Пользователей нет.")
        await c.answer()
        return

    # Header keeps bold; dynamic rows are escaped
    lines = ["<b>Пользователи</b> (введите /act &lt;tg_id&gt; для действий):"]
    for u in users[:50]:
        lines.append(escape(format_user_row(u)))

    # Send ONCE, after the loop, with safe HTML
    await c.message.answer("\n".join(lines), parse_mode="HTML")
    await c.answer()


@router.message(Command("act"))
async def cmd_act(message: Message) -> None:
    """Show actions for a specific user (triggered by ``/act <tg_id>``)."""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /act <tg_id>")
        return
    tg_id = int(parts[1])
    target = await ctx.DBH.get_user(tg_id)
    if not target:
        await message.answer("Пользователь не найден")
        return
    await message.answer(f"Действия для tg:{tg_id}", reply_markup=admin_user_actions(tg_id))


@router.callback_query(F.data.startswith("adm_trial:"))
async def cb_adm_trial(c: CallbackQuery) -> None:
    """Grant a 7-day trial to a user."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    tg_id = int(c.data.split(":")[1])
    import time
    expires = int(time.time()) + 7 * 86400
    await ctx.DBH.set_plan(tg_id, "trial", 1, expires)
    await c.message.answer(f"Выдан триал 7 дней для tg:{tg_id}")
    await c.answer()


@router.callback_query(F.data.startswith("adm_month:"))
async def cb_adm_month(c: CallbackQuery) -> None:
    """Add one month of paid plan to a user."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    tg_id = int(c.data.split(":")[1])
    import time
    expires = int(time.time()) + 30 * 86400
    await ctx.DBH.set_plan(tg_id, "paid", 1, expires)
    await c.message.answer(f"Добавлен месяц для tg:{tg_id}")
    await c.answer()


@router.callback_query(F.data.startswith("adm_unlim:"))
async def cb_adm_unlim(c: CallbackQuery) -> None:
    """Grant unlimited plan to a user."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    tg_id = int(c.data.split(":")[1])
    await ctx.DBH.set_plan(tg_id, "unlimited", -1, None)
    await c.message.answer(f"Установлен безлимит для tg:{tg_id}")
    await c.answer()


@router.callback_query(F.data.startswith("adm_limit:"))
async def cb_adm_limit(c: CallbackQuery) -> None:
    """Prompt the admin to set a custom device limit via ``/limit``."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    tg_id = int(c.data.split(":")[1])
    await c.message.answer(f"Отправьте команду /limit {tg_id} <число> (−1 для бесконечности)")
    await c.answer()


@router.message(Command("limit"))
async def cmd_limit(message: Message) -> None:
    """Set a custom device limit for a user (``/limit <tg_id> <num>``)."""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer("Формат: /limit <tg_id> <число>")
        return
    tg_id = int(parts[1])
    try:
        lim = int(parts[2])
    except Exception:
        await message.answer("Лимит должен быть целым числом (−1 для ∞)")
        return
    await ctx.DBH.set_devices_limit(tg_id, lim)
    await message.answer(f"Лимит устройств для tg:{tg_id} установлен: {('∞' if lim < 0 else lim)}")


# ===== Manual synchronisation command =====

@router.message(Command("resync"))
async def cmd_resync(message: Message) -> None:
    """Manually synchronise the database with the WireGuard runtime.

    When invoked by an admin via the ``/resync`` command, this handler
    calls :func:`app.services.sync.resync_from_runtime` to reconcile
    external peers created outside the bot (e.g. via WGDashboard) and
    update address assignments in the database. A confirmation
    message is sent to the admin when complete.
    """
    if not is_admin(message.from_user.id):
        return
    try:
        await resync_from_runtime()
        await message.answer("База данных синхронизирована с WireGuard.")
    except Exception as e:
        await message.answer(f"Ошибка синхронизации: {e}")


# ===== Peers (with owners) =====

@router.callback_query(F.data == "adm_peers")
async def cb_adm_peers(c: CallbackQuery) -> None:
    """List recent peers along with their owners."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    rows = await ctx.DBH.fetchall(
        """
        SELECT p.id AS pid, p.name, p.public_key, p.address_v4, p.address_v6,
               u.tg_id, u.username, u.first_name, u.last_name
        FROM peers p
        JOIN users u ON u.id = p.user_id
        ORDER BY p.id DESC
        LIMIT 100
        """
    )
    if not rows:
        await c.message.answer("Пиров нет.")
        await c.answer()
        return

    # Header keeps bold; dynamic rows are escaped
    lines = ["<b>Пиры (последние 100)</b> — нажмите /delpeer &lt;pid&gt; для удаления"]
    for r in rows:
        owner = f"@{r['username']}" if r["username"] else f"tg:{r['tg_id']}"
        lines.append(
            escape(
                f"#{r['pid']} «{r['name']}» | {owner} | "
                f"v4:{r['address_v4'] or '-'} v6:{r['address_v6'] or '-'}"
            )
        )

    # Send ONCE, after the loop, with safe HTML
    await c.message.answer("\n".join(lines), parse_mode="HTML")
    await c.message.answer("Для удаления: /delpeer &lt;pid&gt;", parse_mode="HTML")
    await c.answer()


@router.message(Command("delpeer"))
async def cmd_adm_delpeer(message: Message) -> None:
    """Remove a peer by ID via the ``/delpeer <pid>`` command."""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /delpeer <pid>")
        return
    pid = int(parts[1])
    p = await ctx.DBH.fetchall("SELECT * FROM peers WHERE id=?", (pid,))
    if not p:
        await message.answer("Пир не найден")
        return
    peer = p[0]
    try:
        WG.remove_peer(peer["public_key"])
    except Exception as e:
        await message.answer(f"Ошибка wg remove: {e}")
        return
    await ctx.DBH.delete_peer(pid)
    await message.answer(f"Пир #{pid} удалён.")
