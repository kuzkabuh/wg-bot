import time
from datetime import datetime
from typing import Optional

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

router = Router(name="user")

def register_user_handlers(dp: Dispatcher):
    dp.include_router(router)

def fmt_date(ts: Optional[int]) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

def plan_badge(plan: str) -> str:
    return {
        "none": "🔘 Без тарифа",
        "trial": "🟡 Триал",
        "paid": "🟢 Оплачен месяц",
        "unlimited": "♾ Безлимит",
    }.get(plan, plan)

async def ensure_user(message: Message):
    return await ctx.DBH.get_or_create_user(
        tg_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )

async def show_main(message: Message):
    """
    Экран «главная»: для НЕзарегистрированных — приглашение к регистрации,
    для зарегистрированных — короткая карточка + кнопка «Профиль».
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
        try:
            await message.answer("Навигация всегда здесь👇", reply_markup=kb_reply)
        except Exception:
            pass
        return

    # Зарегистрирован — краткая сводка + кнопка 'Профиль'
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
        await message.answer("Навигация всегда здесь👇", reply_markup=kb_reply)
    except Exception:
        pass

@router.message(CommandStart())
async def start_cmd(message: Message):
    u = await ensure_user(message)
    # Админ — сразу безлимит по умолчанию
    if message.from_user.id in ctx.SET.ADMIN_IDS and u["plan"] != "unlimited":
        await ctx.DBH.set_plan(
            tg_id=message.from_user.id, plan="unlimited", devices_limit=-1, expires_at=None
        )
    await show_main(message)

# ===== ReplyKeyboard: обработчик «👤 Профиль» =====

@router.message(F.text == PROFILE_BTN_TEXT)
async def on_profile_btn(message: Message):
    await open_profile(message)

async def open_profile(message_or_cbmsg):
    """Показ профиля с входом в 'Мои устройства'."""
    u = await ctx.DBH.get_user(message_or_cbmsg.from_user.id)
    if not u or u["plan"] == "none":
        # если не зарегистрирован — вернуть на главную
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

# ===== Inline-обработчики =====

@router.callback_query(F.data == "about")
async def cb_about(c: CallbackQuery):
    await c.message.answer(
        "Бот создаёт VPN-профиль WireGuard, выдаёт .conf и QR-код.\n"
        "По умолчанию — 7 дней триал и 1 устройство. Администратор может выдать безлимит."
    )
    await c.answer()

@router.callback_query(F.data == "reg")
async def cb_register(c: CallbackQuery):
    u = await ctx.DBH.get_user(c.from_user.id)
    if u["plan"] != "none":
        await c.answer("Вы уже зарегистрированы", show_alert=True)
        return
    expires_at = int(time.time()) + ctx.SET.TRIAL_DAYS * 86400
    await ctx.DBH.set_plan(tg_id=c.from_user.id, plan="trial",
                           devices_limit=ctx.SET.DEFAULT_DEVICES_LIMIT,
                           expires_at=expires_at)
    await ctx.DBH.add_event(c.from_user.id, "info",
                            f"User registered with trial {ctx.SET.TRIAL_DAYS} days")
    await open_profile(c.message)
    await c.answer()

@router.callback_query(F.data == "back_main")
async def cb_back_main(c: CallbackQuery):
    await show_main(c.message)
    await c.answer()

@router.callback_query(F.data == "profile")
async def cb_profile(c: CallbackQuery):
    await open_profile(c.message)
    await c.answer()

@router.callback_query(F.data == "my_peers")
async def cb_my_peers(c: CallbackQuery):
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
async def cb_peers_stats(c: CallbackQuery):
    # Статистика по всем устройствам пользователя
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
async def cb_add_peer(c: CallbackQuery):
    # Добавление устройства из меню "Мои устройства"
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
        priv, pub = WG.genkeys()
        psk = WG.gen_psk()
        v4_used = WG.used_ips_v4()
        v6_used = WG.used_ips_v6()
        addr_v4 = WG.next_ip(ctx.SET.WG_PRIVATE_SUBNET_V4, v4_used)
        addr_v6 = WG.next_ip(ctx.SET.WG_PRIVATE_SUBNET_V6, v6_used)
        allowed_ips = f"{addr_v4}/32, {addr_v6}/128"
        WG.add_peer(public_key=pub, allowed_ips=allowed_ips, preshared_key=psk)
        peer = await ctx.DBH.create_peer(
            u["id"], name=f"{c.from_user.first_name or 'device'}-{cnt+1}",
            public_key=pub, private_key=priv,
            address_v4=addr_v4, address_v6=addr_v6,
            preshared_key=psk
        )
        # Выдать конфиг и QR сразу
        conf = WG.build_client_conf(private_key=priv, address_v4=addr_v4, address_v6=addr_v6)
        path = WG.save_client_conf_to_file(c.from_user.username or str(c.from_user.id), peer["name"], conf)
        png = save_qr_png(conf, f"{c.from_user.id}_{peer['id']}")
        await c.message.answer_document(FSInputFile(path), caption="Ваш .conf файл")
        await c.message.answer_photo(FSInputFile(png), caption="Ваш QR-код для мобильного клиента WireGuard")
        # И показать обновлённый список
        await cb_my_peers(c)
    except Exception as e:
        await ctx.DBH.add_event(c.from_user.id, "error", f"add_peer fail: {e}")
        await c.answer(f"Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("peer:"))
async def cb_peer_menu(c: CallbackQuery):
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
async def cb_peer_conf(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    u = await ctx.DBH.get_user(c.from_user.id)
    rows = await ctx.DBH.fetchall("SELECT * FROM peers WHERE id=? AND user_id=?", (pid, u["id"]))
    if not rows:
        await c.answer("Не найдено", show_alert=True)
        return
    p = rows[0]
    conf = WG.build_client_conf(p["private_key"], p["address_v4"], p["address_v6"])
    path = WG.save_client_conf_to_file(c.from_user.username or str(c.from_user.id), p["name"], conf)
    await c.message.answer_document(FSInputFile(path), caption=f"Конфиг для {p['name']}", reply_markup=peer_actions_menu(pid))
    await c.answer("Готово")

@router.callback_query(F.data.startswith("peerqr:"))
async def cb_peer_qr(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    u = await ctx.DBH.get_user(c.from_user.id)
    rows = await ctx.DBH.fetchall("SELECT * FROM peers WHERE id=? AND user_id=?", (pid, u["id"]))
    if not rows:
        await c.answer("Не найдено", show_alert=True)
        return
    p = rows[0]
    conf = WG.build_client_conf(p["private_key"], p["address_v4"], p["address_v6"])
    png = save_qr_png(conf, f"{c.from_user.id}_{p['id']}_qr")
    await c.message.answer_photo(FSInputFile(png), caption=f"QR для {p['name']}", reply_markup=peer_actions_menu(pid))
    await c.answer("Готово")

@router.callback_query(F.data.startswith("delpeer:"))
async def cb_del_peer(c: CallbackQuery):
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
async def cb_pay_month(c: CallbackQuery):
    expires_at = int(time.time()) + ctx.SET.MONTH_DAYS * 86400
    await ctx.DBH.set_plan(tg_id=c.from_user.id, plan="paid",
                           devices_limit=ctx.SET.DEFAULT_DEVICES_LIMIT,
                           expires_at=expires_at)
    await ctx.DBH.add_event(c.from_user.id, "info",
                            f"Fake payment: +{ctx.SET.MONTH_DAYS} days")
    u = await ctx.DBH.get_user(c.from_user.id)
    await c.message.answer(f"Оплата зачтена (заглушка). Подписка до: {fmt_date(u['expires_at'])}")
    await c.answer()

# ===== Универсальные «поймать-всё» =====

@router.message()
async def catch_all_text(message: Message):
    # Любой произвольный текст возвращает к главной/профилю
    await show_main(message)

@router.callback_query()
async def catch_all_callback(c: CallbackQuery):
    await c.answer()
    await show_main(c.message)
