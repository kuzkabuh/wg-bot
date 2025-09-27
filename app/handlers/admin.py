"""Handlers for admin commands and callbacks.

This module defines administrative functionality accessible via
commands and inline menus. Only users whose Telegram IDs appear
in :data:`ctx.SET.ADMIN_IDS` will trigger these handlers. Admins can
inspect users and peers, change subscription plans, view logs and
perform management operations.

Добавлено:
- Админ видит «Профиль» как у пользователя (тариф/лимит/его подключения).
- Админская inline-панель поверх обычного пользовательского меню.
- Расширенные действия над любыми пирам: /peer, /link <pid> <tg_id>.
"""

from html import escape
from datetime import datetime, timezone

from aiogram import Router, F, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

import app.context as ctx
from app.menus import admin_menu, admin_user_actions, ADMIN_BTN_TEXT, new_user_menu
from app.services import wg as WG
from app.services.sync import resync_from_runtime

router = Router(name="admin")


def register_admin_handlers(dp: Dispatcher) -> None:
    """Attach all admin handlers to the dispatcher."""
    dp.include_router(router)


def is_admin(uid: int) -> bool:
    """Return ``True`` if the given Telegram ID belongs to an admin user."""
    return uid in ctx.SET.ADMIN_IDS


def _fmt_user_line(u) -> str:
    """One-line summary for admin user lists."""
    lim = "∞" if (u["devices_limit"] < 0 or u["plan"] == "unlimited") else str(u["devices_limit"])
    return (
        f"#{u['id']} tg:{u['tg_id']} "
        f"@{u['username'] or '-'} "
        f"plan={u['plan']} lim={lim} exp={u['expires_at'] or '-'}"
    )


def _fmt_exp(expires_at) -> str:
    """Format unix timestamp or None to human string."""
    if not expires_at:
        return "—"
    try:
        dt = datetime.fromtimestamp(int(expires_at), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(expires_at)


async def _get_user_by_tgid(tg_id: int):
    """Return user row by Telegram ID or None."""
    return await ctx.DBH.get_user(tg_id)


async def _get_peer_by_id(pid: int):
    """Return peer row by peer id or None."""
    rows = await ctx.DBH.fetchall("SELECT * FROM peers WHERE id=?", (pid,))
    return rows[0] if rows else None


async def _render_self_profile_text(tg_id: int) -> str:
    """Build 'profile-like' text for admin (as for regular users)."""
    u = await ctx.DBH.get_user(tg_id)
    if not u:
        return (
            "<b>Профиль</b>\n"
            "Пользователь не найден в базе. Отправьте /start боту, "
            "затем повторите вход в админ-панель."
        )

    rows = await ctx.DBH.fetchall(
        "SELECT id AS pid, name, address_v4, address_v6 "
        "FROM peers WHERE user_id=? ORDER BY id DESC",
        (u["id"],),
    )
    dev_cnt = len(rows)
    lim = "∞" if (u["devices_limit"] < 0 or u["plan"] == "unlimited") else str(u["devices_limit"])

    hello = f"Привет, {escape(u['first_name'] or 'друг')}!"
    lines = [
        f"<b>{hello}</b>",
        f"Ваш статус: {escape(u['plan'] or 'none')}",
        f"Устройства: {dev_cnt} / {lim}",
        f"Действует до: {_fmt_exp(u['expires_at'])}",
    ]

    if rows:
        lines.append("")
        lines.append("<b>Ваши подключения</b>")
        for r in rows[:10]:
            peer_line = (
                f"#{r['pid']} «{r['name']}» "
                f"v4:{r['address_v4'] or '-'} v6:{r['address_v6'] or '-'}"
            )
            lines.append(escape(peer_line))
        if len(rows) > 10:
            lines.append(f"… и ещё {len(rows) - 10}")

    return "\n".join(lines)


# ========= Entry points & admin panel =========

@router.message(Command("admin"))
async def admin_cmd(message: Message) -> None:
    """Enter the admin panel and show own profile.

    Админ видит обычное пользовательское меню (reply-клавиатура) и сверху
    — админскую inline-панель.
    """
    if not is_admin(message.from_user.id):
        return

    # 1) Админ-панель (inline)
    await message.answer("Админ-панель", reply_markup=admin_menu())

    # 2) Профиль (как у пользователя) + прикрепляем пользовательскую клавиатуру
    profile_text = await _render_self_profile_text(message.from_user.id)
    await message.answer(profile_text, parse_mode="HTML", reply_markup=new_user_menu())


@router.message(F.text == ADMIN_BTN_TEXT)
async def admin_btn(message: Message) -> None:
    """Enter the admin panel from the persistent reply keyboard."""
    if not is_admin(message.from_user.id):
        return
    await message.answer("Админ-панель", reply_markup=admin_menu())
    profile_text = await _render_self_profile_text(message.from_user.id)
    await message.answer(profile_text, parse_mode="HTML", reply_markup=new_user_menu())


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(c: CallbackQuery) -> None:
    """Inline callback to open admin panel and profile."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    await c.message.answer("Админ-панель", reply_markup=admin_menu())
    profile_text = await _render_self_profile_text(c.from_user.id)
    await c.message.answer(profile_text, parse_mode="HTML", reply_markup=new_user_menu())
    await c.answer()


# ========= Tools / Logs / Users lists =========

@router.callback_query(F.data == "adm_tools")
async def cb_adm_tools(c: CallbackQuery) -> None:
    """Admin tools info."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    text = (
        "<b>Инструменты администратора</b>\n\n"
        "Доступные команды:\n"
        "• /resync – синхронизировать базу данных с текущим состоянием WireGuard\n"
        "• /peer &lt;pid&gt; – карточка пира и действия\n"
        "• /delpeer &lt;pid&gt; – удалить пир из системы\n"
        "• /link &lt;pid&gt; &lt;tg_id&gt; – привязать пир к пользователю\n"
        "• /limit &lt;tg_id&gt; &lt;число&gt; – установить лимит устройств пользователю\n"
        "• /act &lt;tg_id&gt; – меню действий для пользователя\n"
        "\nПримечание: своими подключениями админ управляет через "
        "пользовательское меню (профиль/устройства/QR/конфиг)."
    )
    await c.message.answer(text, parse_mode="HTML", reply_markup=new_user_menu())
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
        line = (
            f"{escape(str(e['ts']))}: {escape(str(e['level']))} "
            f"tg:{escape(str(e['tg_id'] or '-'))} {escape(str(e['message']))}"
        )
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

    lines = ["<b>Пользователи</b> (введите /act &lt;tg_id&gt; для действий):"]
    for u in users[:50]:
        lines.append(escape(_fmt_user_line(u)))

    await c.message.answer("\n".join(lines), parse_mode="HTML")
    await c.answer()


# ========= Peers lists & actions =========

@router.callback_query(F.data == "adm_peers")
async def cb_adm_peers(c: CallbackQuery) -> None:
    """List recent peers along with their owners."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    rows = await ctx.DBH.fetchall(
        """
        SELECT p.id AS pid, p.name, p.public_key, p.address_v4, p.address_v6, p.user_id,
               u.tg_id, u.username
        FROM peers p
        LEFT JOIN users u ON u.id = p.user_id
        ORDER BY p.id DESC
        LIMIT 100
        """
    )
    if not rows:
        await c.message.answer("Пиров нет.")
        await c.answer()
        return

    lines = ["<b>Пиры (последние 100)</b> — используйте /peer &lt;pid&gt; или /link &lt;pid&gt; &lt;tg_id&gt;"]
    for r in rows:
        owner = f"@{r['username']}" if r["username"] else (f"tg:{r['tg_id']}" if r["tg_id"] else "—")
        lines.append(
            escape(
                f"#{r['pid']} «{r['name']}» | владелец: {owner} | "
                f"v4:{r['address_v4'] or '-'} v6:{r['address_v6'] or '-'}"
            )
        )

    await c.message.answer("\n".join(lines), parse_mode="HTML")
    await c.message.answer(
        "Для удаления: /delpeer <pid> • Для привязки: /link <pid> <tg_id>",
        parse_mode=None
    )
    await c.answer()


@router.message(Command("peer"))
async def cmd_peer(message: Message) -> None:
    """Show a peer card and available admin actions: /delpeer, /link."""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /peer <pid>")
        return
    pid = int(parts[1])
    p = await _get_peer_by_id(pid)
    if not p:
        await message.answer("Пир не найден")
        return

    # resolve owner (if any)
    u = None
    if p["user_id"]:
        rows = await ctx.DBH.fetchall("SELECT tg_id, username FROM users WHERE id=?", (p["user_id"],))
        u = rows[0] if rows else None
    owner = (f"@{u['username']}" if (u and u["username"]) else (f"tg:{u['tg_id']}" if u else "—"))

    lines = [
        "<b>Пир</b>",
        escape(
            f"#{p['id']} «{p['name']}» | владелец: {owner}\n"
            f"v4:{p['address_v4'] or '-'} v6:{p['address_v6'] or '-'}"
        ),
        "Доступные действия:",
        "• /delpeer {pid} — удалить пир",
        "• /link {pid} <tg_id> — привязать к пользователю",
    ]
    text = "\n".join(lines).format(pid=p["id"])
    await message.answer(text, parse_mode="HTML")


@router.message(Command("link"))
async def cmd_link(message: Message) -> None:
    """Link a peer to a user: ``/link <pid> <tg_id>``."""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Формат: /link <pid> <tg_id>")
        return

    pid = int(parts[1])
    tg_id = int(parts[2])

    p = await _get_peer_by_id(pid)
    if not p:
        await message.answer("Пир не найден")
        return

    u = await _get_user_by_tgid(tg_id)
    if not u:
        await message.answer("Пользователь с таким tg_id не найден. Сначала он должен написать боту /start.")
        return

    try:
        await ctx.DBH.execute("UPDATE peers SET user_id=? WHERE id=?", (u["id"], pid))
        await ctx.DBH.log_event("info", message.from_user.id, f"peer#{pid} linked to tg:{tg_id}")
        await message.answer(f"Пир #{pid} привязан к tg:{tg_id}.")
    except Exception as e:
        await message.answer(f"Ошибка привязки: {e}")


@router.callback_query(F.data.startswith("adm_link:"))
async def cb_adm_link(c: CallbackQuery) -> None:
    """Show hint to link a peer to a user via /link."""
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    try:
        pid = int(c.data.split(":")[1])
    except Exception:
        await c.answer()
        return
    await c.message.answer(f"Для привязки отправьте: /link {pid} <tg_id>")
    await c.answer()


# ========= Limits / Actions / Resync / Delete =========

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


@router.message(Command("resync"))
async def cmd_resync(message: Message) -> None:
    """Manually synchronise the database with the WireGuard runtime.

    Кроме синхронизации (подтягивает peers из WG/WGDashboard), сообщение
    возвращает прикреплённую пользовательскую клавиатуру.
    """
    if not is_admin(message.from_user.id):
        return
    try:
        await resync_from_runtime()
        await message.answer("База данных синхронизирована с WireGuard.", reply_markup=new_user_menu())
    except Exception as e:
        await message.answer(f"Ошибка синхронизации: {e}")


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
    p = await _get_peer_by_id(pid)
    if not p:
        await message.answer("Пир не найден")
        return
    try:
        WG.remove_peer(p["public_key"])
    except Exception as e:
        await message.answer(f"Ошибка wg remove: {e}")
        return
    await ctx.DBH.delete_peer(pid)
    await message.answer(f"Пир #{pid} удалён.")
