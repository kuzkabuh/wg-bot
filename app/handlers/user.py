"""Handlers for user commands and callbacks.

This module contains all `aiogram` handlers that serve regular
end‑users of the bot.  It covers registration, profile viewing,
device management, statistics and payment actions.  Each handler
function includes a short docstring explaining its purpose and the
context in which it is used.
"""

import time
from datetime import datetime
from typing import Optional, Iterable, Tuple

from aiogram import Router, F, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile

import app.context as ctx
from app.menus import (
    new_user_menu,
    user_menu,
    profile_menu,
    peers_menu,
    peer_actions_menu,
    persistent_main_kb,
    # ReplyKeyboard texts:
    PROFILE_BTN_TEXT,
)
from app.services import wg as WG
from app.services.qrcoder import save_qr_png
from app.services.runtime import wg_dump_all, parse_dump, human_bytes, handshake_status

# Create a router specifically for user interactions.  Routers allow
# grouping of related handlers and can be attached to a Dispatcher
# easily.
router = Router(name="user")


def register_user_handlers(dp: Dispatcher) -> None:
    """Register all user-level handlers on the given dispatcher.

    Parameters
    ----------
    dp: Dispatcher
        The aiogram Dispatcher instance to which handlers will be
        attached.  Once registered, these handlers will respond to
        matching messages and callback queries.
    """
    dp.include_router(router)


def fmt_date(ts: Optional[int]) -> str:
    """Convert an optional Unix timestamp into a human‑readable string.

    If ``ts`` is falsy (``None`` or zero), a dash is returned.  When
    provided, the timestamp is formatted as ``YYYY-MM-DD HH:MM`` in
    the server's local timezone.

    Parameters
    ----------
    ts: Optional[int]
        Unix timestamp in seconds, or ``None``.

    Returns
    -------
    str
        A human‑readable representation of the timestamp or ``"—"``.
    """
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def plan_badge(plan: str) -> str:
    """Return a badge (emoji and text) describing the user's plan.

    The plan string is mapped to a friendly description.  Unknown
    values are returned unchanged.  The mapping is kept here rather
    than in the database to decouple presentation from storage.

    Parameters
    ----------
    plan: str
        The plan identifier stored in the database (e.g. ``"none"``,
        ``"trial"``).

    Returns
    -------
    str
        A human‑friendly description including an emoji.
    """
    return {
        "none": "🔘 Без тарифа",
        "trial": "🟡 Триал",
        "paid": "🟢 Оплачен месяц",
        "unlimited": "♾ Безлимит",
    }.get(plan, plan)


async def ensure_user(message: Message):
    """Ensure that a Telegram user is present in the database.

    This helper either creates a new record or updates an existing one
    with the latest username and name fields.  It returns the row for
    further use in handlers.

    Parameters
    ----------
    message: Message
        The incoming Telegram message whose sender should be ensured
        to exist in the database.

    Returns
    -------
    aiogram.types.aiosqlite.Row
        The database row representing the user.
    """
    return await ctx.DBH.get_or_create_user(
        tg_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )


async def show_main(message: Message) -> None:
    """Display the main screen to the user.

    For unregistered users, this prompts registration; for registered
    users, it shows a brief account summary and a button to open the
    profile.  A persistent reply keyboard is attached so that the user
    always has navigation buttons available.

    Parameters
    ----------
    message: Message
        The Telegram message that triggered the display.  The reply is
        sent to the same chat.
    """
    u = await ctx.DBH.get_user(message.from_user.id)
    is_admin = message.from_user.id in ctx.SET.ADMIN_IDS
    kb_reply = persistent_main_kb(is_admin)

    if not u or u["plan"] == "none":
        text = (
            "<b>Добро пожаловать в WireGuard VPN бот</b>\n\n"
            "Этот бот выдаёт доступ к VPN на базе WireGuard.\n"
            "Нажмите «Регистрация», чтобы получить триал на 7 дней и 1 устройство."
        )
        await message.answer(text, reply_markup=new_user_menu())
        # Send a secondary message containing only a zero‑width space to attach
        # the persistent keyboard.  Without this, some Telegram clients do
        # not display the reply keyboard on the first message.  The
        # zero‑width space character (\u200B) is invisible to the user,
        # effectively hiding any service text.  We do not show the
        # "Навигация всегда здесь" message to regular users.
        try:
            await message.answer("\u200B", reply_markup=kb_reply)
        except Exception:
            # Ignore errors (e.g. user blocked the bot)
            pass
        return

    # Registered user — show summary and profile button
    peers_count = await ctx.DBH.count_user_peers(u["id"])
    is_unlimited = (u["plan"] == "unlimited" or u["devices_limit"] < 0)
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        f"Ваш статус: {plan_badge(u['plan'])}\n"
        f"Устройства: {peers_count} / {'∞' if is_unlimited else u['devices_limit']}\n"
        f"Действует до: {fmt_date(u['expires_at'])}",
        reply_markup=user_menu(is_admin=is_admin)
    )
    try:
        # Attach the persistent keyboard invisibly using a zero‑width space.
        await message.answer("\u200B", reply_markup=kb_reply)
    except Exception:
        pass


@router.message(CommandStart())
async def start_cmd(message: Message) -> None:
    """Handle the ``/start`` command.

    Ensures the user exists, applies a default unlimited plan for admins
    on first contact, and then delegates to ``show_main`` to present
    the appropriate screen.

    Parameters
    ----------
    message: Message
        The incoming ``/start`` command message.
    """
    u = await ensure_user(message)
    # Admins automatically receive the unlimited plan on first start
    if message.from_user.id in ctx.SET.ADMIN_IDS and u["plan"] != "unlimited":
        await ctx.DBH.set_plan(
            tg_id=message.from_user.id,
            plan="unlimited",
            devices_limit=-1,
            expires_at=None
        )
    await show_main(message)


# ===== ReplyKeyboard: обработчик «👤 Профиль» =====

@router.message(F.text == PROFILE_BTN_TEXT)
async def on_profile_btn(message: Message) -> None:
    """Respond to the persistent "Профиль" button.

    When the user taps the profile button on the reply keyboard, this
    handler opens the profile screen.
    """
    await open_profile(message)


async def open_profile(message_or_cbmsg) -> None:
    """Show the user's profile with a link to their devices.

    This function may be called from both message and callback query
    handlers; it therefore accepts either ``Message`` or
    ``CallbackQuery.message``.  If the user is not registered,
    ``show_main`` is called instead.  Otherwise the profile summary and
    appropriate inline menu are displayed.
    """
    u = await ctx.DBH.get_user(message_or_cbmsg.from_user.id)
    if not u or u["plan"] == "none":
        # not registered — send back to main menu
        await show_main(message_or_cbmsg)
        return
    peers_count = await ctx.DBH.count_user_peers(u["id"])
    is_unlimited = (u["plan"] == "unlimited" or u["devices_limit"] < 0)
    await message_or_cbmsg.answer(
        f"<b>Профиль</b>\n"
        f"Тариф: {plan_badge(u['plan'])}\n"
        f"Устройства: {peers_count} / {'∞' if is_unlimited else u['devices_limit']}\n"
        f"Действует до: {fmt_date(u['expires_at'])}",
        reply_markup=profile_menu(is_unlimited, u["devices_limit"])
    )


# ===== Inline‑обработчики =====

@router.callback_query(F.data == "about")
async def cb_about(c: CallbackQuery) -> None:
    """Show information about the bot when the «ℹ️ О боте» button is tapped."""
    await c.message.answer(
        "Бот создаёт VPN‑профиль WireGuard, выдаёт .conf и QR‑код.\n"
        "По умолчанию — 7 дней триал и 1 устройство. Администратор может выдать безлимит."
    )
    await c.answer()


@router.callback_query(F.data == "reg")
async def cb_register(c: CallbackQuery) -> None:
    """Register a new user when the «Регистрация» button is tapped."""
    u = await ctx.DBH.get_user(c.from_user.id)
    if u["plan"] != "none":
        await c.answer("Вы уже зарегистрированы", show_alert=True)
        return
    expires_at = int(time.time()) + ctx.SET.TRIAL_DAYS * 86400
    await ctx.DBH.set_plan(
        tg_id=c.from_user.id, plan="trial",
        devices_limit=ctx.SET.DEFAULT_DEVICES_LIMIT,
        expires_at=expires_at
    )
    await ctx.DBH.add_event(c.from_user.id, "info",
                            f"User registered with trial {ctx.SET.TRIAL_DAYS} days")
    await open_profile(c.message)
    await c.answer()


@router.callback_query(F.data == "back_main")
async def cb_back_main(c: CallbackQuery) -> None:
    """Return to the main screen from any inline context."""
    await show_main(c.message)
    await c.answer()


@router.callback_query(F.data == "profile")
async def cb_profile(c: CallbackQuery) -> None:
    """Open the profile screen from an inline button."""
    await open_profile(c.message)
    await c.answer()


@router.callback_query(F.data == "my_peers")
async def cb_my_peers(c: CallbackQuery) -> None:
    """Display the list of the user's devices (peers)."""
    u = await ctx.DBH.get_user(c.from_user.id)
    peers = await ctx.DBH.list_user_peers(u["id"])
    if not peers:
        await c.message.answer(
            "У вас пока нет устройств.",
            reply_markup=peers_menu([])
        )
    else:
        items = [(p["id"], p["name"] or f"device-{p['id']}") for p in peers]
        await c.message.answer(
            "Ваши устройства:",
            reply_markup=peers_menu(items)
        )
    await c.answer()


@router.callback_query(F.data == "peers_stats")
async def cb_peers_stats(c: CallbackQuery) -> None:
    """Show aggregated statistics for all of the user's peers."""
    # Gather runtime stats for all peers from the system
    dump = wg_dump_all()
    parsed = parse_dump(dump)
    iface = parsed.get(ctx.SET.WG_INTERFACE, {})
    peers_rt = iface.get("peers", {})

    u = await ctx.DBH.get_user(c.from_user.id)
    my_peers = await ctx.DBH.list_user_peers(u["id"])
    if not my_peers:
        await c.message.answer("Нет устройств для статистики.")
        await c.answer()
        return
    lines = ["<b>Статистика устройств</b>"]
    for p in my_peers:
        info = peers_rt.get(p["public_key"])
        if not info:
            lines.append(f"• {p['name']}: нет данных (peer неактивен)")
            continue
        icon, desc = handshake_status(info["latest_handshake"])
        lines.append(
            f"• <b>{p['name']}</b>: {icon} {desc}, RX={human_bytes(info['transfer_rx'])}, TX={human_bytes(info['transfer_tx'])}"
        )
    await c.message.answer("\n".join(lines))
    await c.answer()


@router.callback_query(F.data == "add_peer")
async def cb_add_peer(c: CallbackQuery) -> None:
    """Add a new peer (device) for the current user.

    This handler checks subscription limits and expiry, generates keys
    and addresses, adds the peer to the running WireGuard instance,
    stores it in the database, and immediately sends the configuration
    and QR code to the user.
    """
    u = await ctx.DBH.get_user(c.from_user.id)
    if not u:
        await c.answer("Сначала зарегистрируйтесь", show_alert=True)
        return
    now = int(time.time())
    if u["plan"] != "unlimited":
        if not u["expires_at"] or u["expires_at"] < now:
            await c.answer("Срок действия тарифа истёк", show_alert=True)
            return
    cnt = await ctx.DBH.count_user_peers(u["id"])
    lim = u["devices_limit"]
    if u["plan"] != "unlimited" and lim >= 0 and cnt >= lim:
        await c.answer("Достигнут лимит устройств", show_alert=True)
        return
    try:
        # Generate key pair and preshared key
        priv, pub = WG.genkeys()
        psk = WG.gen_psk()
        # Determine free IP addresses within configured subnets
        v4_used = WG.used_ips_v4()
        v6_used = WG.used_ips_v6()
        addr_v4 = WG.next_ip(ctx.SET.WG_PRIVATE_SUBNET_V4, v4_used)
        addr_v6 = WG.next_ip(ctx.SET.WG_PRIVATE_SUBNET_V6, v6_used)
        allowed_ips = f"{addr_v4}/32, {addr_v6}/128"
        # Add peer to WireGuard runtime (server side)
        WG.add_peer(public_key=pub, allowed_ips=allowed_ips, preshared_key=psk)
        # Persist peer in the database
        peer = await ctx.DBH.create_peer(
            u["id"], name=f"{c.from_user.first_name or 'device'}-{cnt+1}",
            public_key=pub, private_key=priv,
            address_v4=addr_v4, address_v6=addr_v6,
            preshared_key=psk
        )
        # Build client configuration including preshared key and allowed IPs for the peer
        client_allowed = getattr(ctx.SET, "WG_ALLOWED_IPS", None)
        conf = WG.build_client_conf(
            private_key=priv,
            address_v4=addr_v4,
            address_v6=addr_v6,
            preshared_key=psk,
            allowed_ips=client_allowed
        )
        path = WG.save_client_conf_to_file(c.from_user.username or str(c.from_user.id), peer["name"], conf)
        png = save_qr_png(conf, f"{c.from_user.id}_{peer['id']}")
        await c.message.answer_document(FSInputFile(path), caption="Ваш .conf файл")
        await c.message.answer_photo(FSInputFile(png), caption="Ваш QR‑код для мобильного клиента WireGuard")
        # Show updated list
        await cb_my_peers(c)
    except Exception as e:
        # Log and report the error
        await ctx.DBH.add_event(c.from_user.id, "error", f"add_peer fail: {e}")
        await c.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("peer:"))
async def cb_peer_menu(c: CallbackQuery) -> None:
    """Open the action menu for a specific peer.

    The callback data is expected to be of the form ``peer:<id>``.
    The handler fetches the peer from the database, displays its IP
    addresses and shows the action menu.
    """
    pid = int(c.data.split(":")[1])
    u = await ctx.DBH.get_user(c.from_user.id)
    row = await ctx.DBH.fetchone("SELECT * FROM peers WHERE id=? AND user_id=?", (pid, u["id"]))
    if not row:
        await c.answer("Устройство не найдено", show_alert=True)
        return
    v4 = row["address_v4"] or "—"
    v6 = row["address_v6"] or "—"
    await c.message.answer(
        f"<b>{row['name']}</b>\nIPv4: {v4}\nIPv6: {v6}",
        reply_markup=peer_actions_menu(pid)
    )
    await c.answer()


@router.callback_query(F.data.startswith("peerconf:"))
async def cb_peer_conf(c: CallbackQuery) -> None:
    """Send the configuration file for the selected peer."""
    pid = int(c.data.split(":")[1])
    u = await ctx.DBH.get_user(c.from_user.id)
    rows = await ctx.DBH.fetchall("SELECT * FROM peers WHERE id=? AND user_id=?", (pid, u["id"]))
    if not rows:
        await c.answer("Не найдено", show_alert=True)
        return
    p = rows[0]
    client_allowed = getattr(ctx.SET, "WG_ALLOWED_IPS", None)
    conf = WG.build_client_conf(
        private_key=p["private_key"],
        address_v4=p["address_v4"],
        address_v6=p["address_v6"],
        preshared_key=p["preshared_key"],
        allowed_ips=client_allowed
    )
    path = WG.save_client_conf_to_file(c.from_user.username or str(c.from_user.id), p["name"], conf)
    await c.message.answer_document(FSInputFile(path), caption=f"Конфиг для {p['name']}", reply_markup=peer_actions_menu(pid))
    await c.answer("Готово")


@router.callback_query(F.data.startswith("peerqr:"))
async def cb_peer_qr(c: CallbackQuery) -> None:
    """Send the QR code for the selected peer."""
    pid = int(c.data.split(":")[1])
    u = await ctx.DBH.get_user(c.from_user.id)
    rows = await ctx.DBH.fetchall("SELECT * FROM peers WHERE id=? AND user_id=?", (pid, u["id"]))
    if not rows:
        await c.answer("Не найдено", show_alert=True)
        return
    p = rows[0]
    client_allowed = getattr(ctx.SET, "WG_ALLOWED_IPS", None)
    conf = WG.build_client_conf(
        private_key=p["private_key"],
        address_v4=p["address_v4"],
        address_v6=p["address_v6"],
        preshared_key=p["preshared_key"],
        allowed_ips=client_allowed
    )
    png = save_qr_png(conf, f"{c.from_user.id}_{p['id']}_qr")
    await c.message.answer_photo(FSInputFile(png), caption=f"QR для {p['name']}", reply_markup=peer_actions_menu(pid))
    await c.answer("Готово")


@router.callback_query(F.data.startswith("delpeer:"))
async def cb_del_peer(c: CallbackQuery) -> None:
    """Delete a peer from the system and database."""
    pid = int(c.data.split(":")[1])
    u = await ctx.DBH.get_user(c.from_user.id)
    peers = await ctx.DBH.fetchall("SELECT * FROM peers WHERE id=? AND user_id=?", (pid, u["id"]))
    if not peers:
        await c.answer("Не найдено", show_alert=True)
        return
    peer = peers[0]
    try:
        WG.remove_peer(peer["public_key"])
    except Exception as e:
        await ctx.DBH.add_event(c.from_user.id, "error", f"remove_peer fail: {e}")
        await c.answer("Ошибка удаления из wg", show_alert=True)
        return
    await ctx.DBH.delete_peer(pid)
    await c.message.answer("Устройство удалено.")
    await cb_my_peers(c)
    await c.answer()


@router.callback_query(F.data == "pay_month")
async def cb_pay_month(c: CallbackQuery) -> None:
    """Simulate payment for one month of service (demo)."""
    expires_at = int(time.time()) + ctx.SET.MONTH_DAYS * 86400
    await ctx.DBH.set_plan(
        tg_id=c.from_user.id, plan="paid",
        devices_limit=ctx.SET.DEFAULT_DEVICES_LIMIT,
        expires_at=expires_at
    )
    await ctx.DBH.add_event(c.from_user.id, "info",
                            f"Fake payment: +{ctx.SET.MONTH_DAYS} days")
    u = await ctx.DBH.get_user(c.from_user.id)
    await c.message.answer(f"Оплата зачтена (заглушка). Подписка до: {fmt_date(u['expires_at'])}")
    await c.answer()


# ===== Catch‑all handlers =====

@router.message()
async def catch_all_text(message: Message) -> None:
    """Catch any unhandled text messages and return to the main screen."""
    await show_main(message)


@router.callback_query()
async def catch_all_callback(c: CallbackQuery) -> None:
    """Catch any unhandled callback queries and return to the main screen."""
    await c.answer()
    await show_main(c.message)