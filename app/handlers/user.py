import os
import re
import time
import html
import asyncio
from datetime import datetime
from typing import Optional, List, Dict

from aiogram import Router, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, BufferedInputFile,
    InlineKeyboardMarkup, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from app.context import SET, DBH, PEERS_DIR
from app.menus import user_menu_for, tariffs_inline_menu
from app.services.runtime import (
    merge_user_peers_with_runtime, H, format_hs,
    status_icon, status_badge, human_delta, snapshot_map_by_pub
)
from wg import (
    server_public_key, used_ips_v4, used_ips_v6,
    genkeys, add_peer, remove_peer, save_config
)
from utils import next_free_ipv4, next_free_ipv6, qr_png_from_config, human_bytes

router = Router()

# =========================
# КОМПАКТНЫЙ РЕЖИМ (дефолты можно переопределить в SET)
# =========================
COMPACT_DELETE_INPUT: bool = getattr(SET, "compact_delete_user_input", True)
TTL_FILES_SEC: int = getattr(SET, "autodelete_ttl_sec", 180)         # .conf / QR
TTL_NOTE_SEC: int = getattr(SET, "autodelete_note_sec", 2)           # служебные подсказки
TTL_SWAP_OLD_MENU_SEC: int = getattr(SET, "menu_anchor_swap_delete_sec", 10)  # удаление старого «якоря» меню

# =========================
# СОСТОЯНИЯ СООБЩЕНИЙ
# =========================
PANELS: Dict[int, int] = {}        # tg_id -> message_id «панели» (редактируемый пост)
MENU_ANCHORS: Dict[int, int] = {}  # tg_id -> message_id «якорного» сообщения с reply-клавиатурой

# =========================
# ВСПОМОГАТЕЛЬНЫЕ
# =========================

KNOWN_TEXT_BUTTONS = {
    "🎫 Получить 7 дней",
    "💰 Оплатить доступ",
    "🔁 Продлить доступ",
    "➕ Создать устройство",
    "👤 Профиль",
    "❓ Помощь",
    "📥 Мой доступ / Скачать",  # legacy
    "📊 Статистика",            # legacy
    "🛠 Админ-панель",
}

def _is_admin(tg_id: int) -> bool:
    return tg_id in SET.admin_ids

def _now() -> int:
    return int(time.time())

def _plan_active(u: dict) -> bool:
    return (u["plan"] == "unlimited") or (u["expires_at"] and u["expires_at"] > _now())

def _has_room(u: dict) -> bool:
    if u["plan"] == "unlimited":
        return True
    limit = u["devices_limit"]
    if limit < 0:
        return True
    return DBH.count_active_peers(u["id"]) < limit

def plan_human(plan: str, devices_limit: int) -> str:
    base = {
        "none": "нет",
        "trial": "триал",
        "paid": "оплачено",
        "unlimited": "безлимит (навсегда)",
    }.get(plan, plan)
    suffix = " · безлимит устройств" if devices_limit < 0 else " · 1 устройство"
    return base + suffix if plan in ("trial", "paid") else base

async def _maybe_delete_user_input(m: Message):
    """Удаляем пользовательские сообщения для компактности (не трогаем /команды и не-текст)."""
    if not COMPACT_DELETE_INPUT:
        return
    try:
        if not m.text:
            return
        if m.text.startswith("/"):
            return
        # не удаляем якорные сообщения бота — только пользовательский ввод
        await m.delete()
    except Exception:
        pass

async def _autodelete_message(msg: Message, ttl: int):
    try:
        await asyncio.sleep(ttl)
        await msg.delete()
    except Exception:
        pass

async def _delete_message_by_id(bot, chat_id: int, msg_id: int, delay: int = 0):
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

async def _refresh_main_menu(target: Message | CallbackQuery, note: str = "🏠 Меню обновлено."):
    """
    Обновляем reply-клавиатуру БЕЗ её исчезновения:
      1) «невидимкой» снимаем старую раскладку;
      2) отправляем НОВОЕ якорное меню (не удаляем его);
      3) старый якорь удаляем позже (swap).
    """
    if isinstance(target, CallbackQuery):
        chat = target.message
        tg_id = target.from_user.id
        bot = target.bot
    else:
        chat = target
        tg_id = target.from_user.id
        bot = target.bot

    # (1) сброс старой раскладки
    try:
        rm_msg = await chat.answer("\u2060", reply_markup=ReplyKeyboardRemove())
        asyncio.create_task(_autodelete_message(rm_msg, TTL_NOTE_SEC))
    except Exception:
        pass

    # (2) рисуем новый якорь
    u = DBH.get_user_by_tg(tg_id)
    menu = user_menu_for(u, _is_admin(tg_id), DBH.count_active_peers(u["id"]), _has_room(u))
    new_anchor = await chat.answer(note, reply_markup=menu)

    # (3) swap старого якоря
    old_anchor_id = MENU_ANCHORS.get(tg_id)
    MENU_ANCHORS[tg_id] = new_anchor.message_id
    if old_anchor_id and old_anchor_id != new_anchor.message_id:
        asyncio.create_task(_delete_message_by_id(bot, chat.chat.id, old_anchor_id, delay=TTL_SWAP_OLD_MENU_SEC))

async def _render_panel(tg_id: int, source: Message | CallbackQuery, text: str, kb: Optional[InlineKeyboardMarkup] = None):
    """Поддерживаем один «панельный» пост на пользователя."""
    chat_id = source.chat.id if isinstance(source, Message) else source.message.chat.id
    bot = source.bot
    msg_id = PANELS.get(tg_id)

    if msg_id:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb)
            return
        except Exception:
            PANELS.pop(tg_id, None)

    if isinstance(source, CallbackQuery):
        sent = await source.message.answer(text, reply_markup=kb)
    else:
        sent = await source.answer(text, reply_markup=kb)
    PANELS[tg_id] = sent.message_id

# =========================
# СБОРЩИКИ ПАНЕЛЕЙ
# =========================

def profile_panel_text(u: dict, peers: List[dict]) -> str:
    now = _now()
    exp = u["expires_at"]
    left_days = ((exp - now) // 86400) if exp else 0
    exp_str = "∞" if not exp else datetime.utcfromtimestamp(exp).strftime("%Y-%m-%d %H:%M UTC")
    count = DBH.count_active_peers(u["id"])
    limit = u["devices_limit"]
    limit_str = "безлимит" if limit < 0 else str(limit)

    header = (
        "👤 <b>Ваш профиль</b>\n"
        f"Тариф: <b>{html.escape(plan_human(u['plan'], u['devices_limit']))}</b>\n"
        f"Срок: <b>{html.escape(exp_str)}</b> "
        f"(осталось дней: <b>{html.escape(str(left_days))}</b>)\n"
        f"Устройства: <b>{count}</b> из <b>{limit_str}</b>\n\n"
    )

    if not peers:
        body = ("🔌 <b>Мои подключения</b>\n"
                "Пока нет активных устройств.\n\n"
                "Нажмите «➕ Создать устройство», если тариф активен.")
    else:
        lines = ["🔌 <b>Мои подключения</b>\nВыберите устройство ниже:"]
        now_i = int(time.time())
        for p in peers:
            lines.append(f"{status_icon(p['latest'], now_i)} <b>{H(p['name'])}</b> — "
                         f"{'онлайн' if p['latest']>0 and (now_i - p['latest'])<130 else 'офлайн'}")
        body = "\n".join(lines)

    return header + body

def profile_panel_kb(peers: List[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    now = int(time.time())
    for p in peers:
        icon = status_icon(p["latest"], now)
        kb.button(text=f"{icon} {p['name']}", callback_data=f"peer:menu:{p['id']}")
    if peers:
        kb.button(text="📊 Статистика", callback_data="prof:stats")
    kb.adjust(2)
    return kb.as_markup()

def peer_menu_text(p: dict, latest: int, endpoint: str) -> str:
    now = int(time.time())
    status = status_badge(latest, now)
    ago = "—" if latest <= 0 else human_delta(now - latest)
    hs_line = "—" if latest <= 0 else f"{format_hs(latest)} ({ago} назад)"
    return (
        f"🔌 <b>{H(p['name'])}</b>\n"
        f"• Статус: {status}\n"
        f"• IPv4: <code>{H(p['ipv4'])}</code>" +
        (f"\n• IPv6: <code>{H(p['ipv6'])}</code>" if p.get("ipv6") else "") + "\n"
        f"• Endpoint: <code>{H(endpoint)}</code>\n"
        f"• Последний handshake: {hs_line}\n\n"
        f"Выберите действие:"
    )

def peer_menu_kb(peer_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🧾 Открыть QR", callback_data=f"qr:{peer_id}")
    kb.button(text="⬇️ Скачать .conf", callback_data=f"conf:{peer_id}")
    kb.button(text="🗑 Удалить", callback_data=f"rm:{peer_id}")
    kb.button(text="◀️ Назад к профилю", callback_data="prof:back")
    kb.adjust(2, 2)
    return kb.as_markup()

def stats_text(user_id: int) -> str:
    peers = merge_user_peers_with_runtime(user_id)
    if not peers:
        return "Пока нет активных устройств."
    total_rx = sum(p["rx"] for p in peers)
    total_tx = sum(p["tx"] for p in peers)
    blocks = [f"📈 <b>Статистика</b> — устройств: <b>{len(peers)}</b>\n"
              f"Итого: ⬇️ {human_bytes(total_rx)} · ⬆️ {human_bytes(total_tx)}"]
    now = int(time.time())
    for p in peers:
        status = status_badge(p["latest"], now)
        ago = "—" if p["latest"] <= 0 else human_delta(now - p["latest"])
        hs_line = "—" if p["latest"] <= 0 else f"{format_hs(p['latest'])} ({ago} назад)"
        ip_lines = f"IPv4: <code>{H(p['ipv4'])}</code>"
        if p.get("ipv6"):
            ip_lines += f"\nIPv6: <code>{H(p['ipv6'])}</code>"
        block = (
            f"\n🖥 <b>{H(p['name'])}</b>\n"
            f"• Статус: {status}\n"
            f"• Последний handshake: {hs_line}\n"
            f"• Трафик: ⬇️ {human_bytes(p['rx'])} · ⬆️ {human_bytes(p['tx'])}\n"
            f"• {ip_lines}\n"
            f"• Endpoint: <code>{H(p['endpoint'])}</code>"
        )
        blocks.append(block)
    return "\n".join(blocks)

def tariffs_panel_text(header: str) -> str:
    return header + "\n\nВыберите тариф ниже (оплата звёздами — заглушка)."

# =========================
# /start и первичная отрисовка
# =========================

@router.message(CommandStart())
async def cmd_start(m: Message):
    DBH.upsert_user(
        tg_id=m.from_user.id,
        username=m.from_user.username or "",
        first=m.from_user.first_name or "",
        last=m.from_user.last_name or ""
    )
    await _refresh_main_menu(m, note="👋 Привет! Я помогу подключиться к VPN (WireGuard). Клавиатура обновлена.")
    u = DBH.get_user_by_tg(m.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(m.from_user.id, m, profile_panel_text(u, peers), profile_panel_kb(peers))
    await _maybe_delete_user_input(m)

# ==========================
# ОПЛАТА / ПРОДЛЕНИЕ — ПАНЕЛЬ
# ==========================

@router.message(F.text == "💰 Оплатить доступ")
async def pay_open(m: Message):
    text = tariffs_panel_text("💡 <b>Оплатить доступ</b>")
    await _render_panel(m.from_user.id, m, text, tariffs_inline_menu())
    await _maybe_delete_user_input(m)

@router.message(F.text == "🔁 Продлить доступ")
async def extend_open(m: Message):
    u = DBH.get_user_by_tg(m.from_user.id)
    if u["plan"] == "unlimited":
        await _render_panel(m.from_user.id, m, "♾ У вас <b>безлимит (навсегда)</b>. Продление не требуется.")
        await _maybe_delete_user_input(m)
        return
    text = tariffs_panel_text("🔁 <b>Продлить доступ</b>")
    await _render_panel(m.from_user.id, m, text, tariffs_inline_menu())
    await _maybe_delete_user_input(m)

# =============================
# ТРИАЛ / СОЗДАНИЕ УСТРОЙСТВА
# =============================

@router.message(F.text == "🎫 Получить 7 дней")
async def get_trial(m: Message):
    u = DBH.get_user_by_tg(m.from_user.id)
    now = _now()

    if u["plan"] == "trial" and u["expires_at"] and u["expires_at"] > now:
        await _render_panel(m.from_user.id, m, "ℹ️ У вас уже активен триал. Можно продолжить на платном тарифе в «💰 Оплатить доступ».")
        await _maybe_delete_user_input(m)
        return

    if u["plan"] in ("none", "trial"):
        DBH.set_plan(u["id"], plan="trial", devices_limit=1, expires_at=now + 7*86400)
        await _render_panel(m.from_user.id, m, "✅ Триал активирован на 7 дней. Создаю устройство…")
    elif u["plan"] == "paid":
        await _render_panel(m.from_user.id, m, "ℹ️ У вас уже есть оплаченный тариф. Используйте «➕ Создать устройство».")
        await _maybe_delete_user_input(m)
        return
    elif u["plan"] == "unlimited":
        await _render_panel(m.from_user.id, m, "ℹ️ У вас безлимитный тариф. Используйте «➕ Создать устройство».")
        await _maybe_delete_user_input(m)
        return

    u = DBH.get_user_by_tg(m.from_user.id)
    if not _plan_active(u):
        await _render_panel(m.from_user.id, m, "⚠️ Сначала активируйте тариф.")
        await _maybe_delete_user_input(m)
        return

    await create_device_for_user(m, u)
    await _refresh_main_menu(m, note="🏠 Меню обновлено.")
    u = DBH.get_user_by_tg(m.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(m.from_user.id, m, profile_panel_text(u, peers), profile_panel_kb(peers))
    await _maybe_delete_user_input(m)

@router.message(F.text == "➕ Создать устройство")
async def create_device_btn(m: Message):
    u = DBH.get_user_by_tg(m.from_user.id)
    if not _plan_active(u):
        await _render_panel(m.from_user.id, m, "⚠️ Тариф не активен. Используйте «💰 Оплатить доступ».")
        await _maybe_delete_user_input(m)
        return
    if not _has_room(u):
        limit = u["devices_limit"]
        await _render_panel(m.from_user.id, m, f"⛔ Лимит устройств исчерпан ({DBH.count_active_peers(u['id'])}/{limit}).")
        await _maybe_delete_user_input(m)
        return

    await create_device_for_user(m, u)
    await _refresh_main_menu(m, note="🏠 Меню обновлено.")
    u = DBH.get_user_by_tg(m.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(m.from_user.id, m, profile_panel_text(u, peers), profile_panel_kb(peers))
    await _maybe_delete_user_input(m)

async def create_device_for_user(m: Message, u: dict):
    """Создание пира в ядре + конфиг/QR пользователю + запись в БД + save_config."""
    count = DBH.count_active_peers(u["id"])
    device_name = f"device-{count+1}"

    keys = genkeys()
    ipv4 = next_free_ipv4(SET.network_v4, used_ips_v4(SET.wg_interface))
    ipv6: Optional[str] = None
    if SET.network_v6:
        try:
            ipv6 = next_free_ipv6(SET.network_v6, used_ips_v6(SET.wg_interface))
        except Exception:
            ipv6 = None

    add_peer(SET.wg_interface, keys["public"], ipv4, ipv6)

    user_dir = os.path.join(PEERS_DIR, str(u["tg_id"]))
    os.makedirs(user_dir, exist_ok=True)
    conf_text = build_client_conf(keys["private"], ipv4, ipv6)
    conf_path = os.path.join(user_dir, f"{device_name}.conf")
    with open(conf_path, "w") as f:
        f.write(conf_text)
    qr_path = os.path.join(user_dir, f"{device_name}.png")
    qr_png_from_config(conf_text, qr_path)

    DBH.add_peer(
        user_id=u["id"], name=device_name,
        private=keys["private"], public=keys["public"],
        ipv4=ipv4, ipv6=ipv6 or "", conf_path=conf_path, qr_path=qr_path
    )

    try:
        save_config(SET.wg_interface)  # чтобы WGDashboard видел пиры
    except Exception:
        pass

    v6info = f"\n• IPv6: <code>{html.escape(ipv6)}</code>" if ipv6 else ""
    await _render_panel(m.from_user.id, m,
        "✅ Устройство <b>{}</b> готово!\n"
        "• IPv4: <code>{}</code>{}\n"
        "• Сервер: <code>{}:{}</code>\n\n"
        "Отправляю файл и QR (они исчезнут через несколько минут ✨)".format(
            html.escape(device_name), html.escape(ipv4), v6info,
            html.escape(SET.endpoint_host), html.escape(str(SET.endpoint_port))
        )
    )

    # .conf (эпхемерно)
    try:
        data = conf_text.encode("utf-8")
        doc_msg = await m.answer_document(
            BufferedInputFile(data, filename=f"{device_name}.conf"),
            caption=f"{device_name}.conf — импортируйте в приложение WireGuard"
        )
        asyncio.create_task(_autodelete_message(doc_msg, TTL_FILES_SEC))
    except Exception:
        try:
            with open(conf_path, "r") as f:
                txt = f.read()
            warn = await m.answer("Не удалось отправить как файл. Вот конфигурация (исчезнет через 3 мин):")
            asyncio.create_task(_autodelete_message(warn, TTL_FILES_SEC))
            code_msg = await m.answer(f"<pre><code>{html.escape(txt)}</code></pre>")
            asyncio.create_task(_autodelete_message(code_msg, TTL_FILES_SEC))
        except Exception:
            pass

    # QR (эпхемерно)
    try:
        qr_msg = await m.answer_photo(FSInputFile(qr_path), caption="QR для быстрого импорта (исчезнет через 3 мин)")
        asyncio.create_task(_autodelete_message(qr_msg, TTL_FILES_SEC))
    except Exception:
        pass

def build_client_conf(peer_priv: str, addr_v4: Optional[str], addr_v6: Optional[str]) -> str:
    s_pub = server_public_key(SET.wg_interface)
    lines: List[str] = ["[Interface]", f"PrivateKey = {peer_priv}"]
    if addr_v4 and addr_v6:
        lines.append(f"Address = {addr_v4},{addr_v6}")
    elif addr_v4:
        lines.append(f"Address = {addr_v4}")
    elif addr_v6:
        lines.append(f"Address = {addr_v6}")
    if SET.dns_servers:
        lines.append(f"DNS = {SET.dns_servers}")
    lines += [
        "", "[Peer]", f"PublicKey = {s_pub}",
        "AllowedIPs = 0.0.0.0/0, ::/0",
        f"Endpoint = {SET.endpoint_host}:{SET.endpoint_port}",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(lines) + "\n"

# ===========================================
# ПРОФИЛЬ / СТАТИСТИКА / ПОМОЩЬ — ПАНЕЛЬ
# ===========================================

@router.message(F.text == "👤 Профиль")
async def profile_cmd(m: Message):
    await _refresh_main_menu(m, note="👤 Профиль открыт. Клавиатура обновлена.")
    u = DBH.get_user_by_tg(m.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(m.from_user.id, m, profile_panel_text(u, peers), profile_panel_kb(peers))
    await _maybe_delete_user_input(m)

@router.callback_query(F.data == "prof:back")
async def prof_back_cb(c: CallbackQuery):
    u = DBH.get_user_by_tg(c.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(c.from_user.id, c, profile_panel_text(u, peers), profile_panel_kb(peers))
    await c.answer()

@router.callback_query(F.data == "prof:stats")
async def prof_stats_cb(c: CallbackQuery):
    u = DBH.get_user_by_tg(c.from_user.id)
    text = stats_text(u["id"])
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад к профилю", callback_data="prof:back")
    await _render_panel(c.from_user.id, c, text, kb.as_markup())
    await c.answer()

@router.message(F.text == "❓ Помощь")
async def help_msg(m: Message):
    await _render_panel(m.from_user.id, m,
        "🆘 <b>Как подключиться к VPN (WireGuard)</b>\n\n"
        "🔑 <b>Шаг 1. Получите доступ у бота</b>\n"
        "• Откройте: <b>👤 Профиль</b> → <b>🔌 Мои подключения</b>\n"
        "• Нажмите на устройство → выберите: <b>🧾 Открыть QR</b> (iOS/Android) или <b>⬇️ Скачать .conf</b>\n"
        "• Если устройств нет — нажмите в главном меню <b>➕ Создать устройство</b>\n\n"
        "📲 <b>Шаг 2. Установите приложение WireGuard</b>\n"
        "• iOS / macOS: App Store → <b>WireGuard</b>\n"
        "• Android: Google Play → <b>WireGuard</b>\n"
        "• Windows: сайт разработчика → <b>WireGuard for Windows</b>\n"
        "• Linux: установите пакет <code>wireguard-tools</code> из репозитория вашей ОС\n\n"
        "📷 <b>Вариант A — по QR (iOS/Android)</b>\n"
        "1) WireGuard → <b>Add</b> → <b>Create from QR code</b>\n"
        "2) Наведите камеру на QR из бота\n"
        "3) Имя (например, <code>my_phone</code>) → <b>Save</b> → включите туннель\n\n"
        "📄 <b>Вариант B — по файлу .conf</b>\n"
        "• iOS: сохраните .conf в «Файлы» → WireGuard → <b>Add</b> → <b>Import from file</b>\n"
        "• Android: сохраните .conf → WireGuard → <b>Add</b> → <b>Import from file or archive</b>\n"
        "• Windows: WireGuard → <b>Add Tunnel</b> → <b>Import from file</b> → <b>Activate</b>\n"
        "• macOS: WireGuard → <b>Add Tunnel</b> → <b>Import from file</b> → включите туннель\n"
        "• Linux:\n"
        "  <code>sudo mkdir -p /etc/wireguard</code>\n"
        "  <code>sudo cp ~/Downloads/your_device.conf /etc/wireguard/wg0.conf</code>\n"
        "  <code>sudo chmod 600 /etc/wireguard/wg0.conf</code>\n"
        "  <code>sudo wg-quick up wg0</code>\n\n"
        "✅ <b>Проверка</b>: есть <i>Latest Handshake</i>, в «📊 Статистика» видно трафик и статус 🟢 Онлайн.\n"
        "🛠 Если интернет не идёт: проверьте <code>AllowedIPs = 0.0.0.0/0, ::/0</code>, <code>Endpoint</code> и сеть (UDP).\n"
    )
    await _maybe_delete_user_input(m)

# =========================
# АДМИН-ПАНЕЛЬ (встроенная)
# =========================

def admin_panel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📶 Пиры (детально)", callback_data="admin:peers")
    kb.button(text="🔁 Обновить меню", callback_data="admin:refresh")
    kb.button(text="◀️ Назад к профилю", callback_data="prof:back")
    kb.adjust(2, 1)
    return kb.as_markup()

@router.message(F.text == "🛠 Админ-панель")
async def admin_panel_open(m: Message):
    if not _is_admin(m.from_user.id):
        await _render_panel(m.from_user.id, m, "⛔ Нет доступа к админ-панели.")
        await _maybe_delete_user_input(m)
        return
    await _render_panel(m.from_user.id, m, "🛠 <b>Админ-панель</b>\nВыберите действие:", admin_panel_kb())
    await _maybe_delete_user_input(m)

@router.callback_query(F.data == "admin:refresh")
async def admin_refresh(c: CallbackQuery):
    if not _is_admin(c.from_user.id):
        await c.answer("Нет доступа", show_alert=True); return
    await _refresh_main_menu(c, note="🛠 Меню админа обновлено.")
    await _render_panel(c.from_user.id, c, "🛠 <b>Админ-панель</b>\nВыберите действие:", admin_panel_kb())
    await c.answer()

def _db_peers_by_public() -> Dict[str, dict]:
    """Читаем peers из БД и строим map public_key -> peer_row."""
    mapping: Dict[str, dict] = {}
    try:
        with DBH._connect() as con:
            cur = con.cursor()
            # допускаем наличие столбца revoked
            try:
                cur.execute("SELECT id,user_id,name,public_key,ipv4,ipv6,COALESCE(revoked,0) FROM peers")
            except Exception:
                cur.execute("SELECT id,user_id,name,public_key,ipv4,ipv6,0 FROM peers")
            for row in cur.fetchall():
                pid, uid, name, pub, ipv4, ipv6, rev = row
                mapping[str(pub)] = {
                    "id": pid, "user_id": uid, "name": name,
                    "public_key": str(pub), "ipv4": ipv4 or "", "ipv6": ipv6 or "",
                    "revoked": int(rev) if rev is not None else 0
                }
    except Exception:
        pass
    return mapping

def _db_users_tg_map() -> Dict[int, int]:
    """user_id -> tg_id"""
    mp: Dict[int, int] = {}
    try:
        with DBH._connect() as con:
            cur = con.cursor()
            cur.execute("SELECT id, tg_id FROM users")
            for uid, tg_id in cur.fetchall():
                mp[int(uid)] = int(tg_id)
    except Exception:
        pass
    return mp

def _format_admin_peers_text() -> str:
    rt = snapshot_map_by_pub(SET.wg_interface)  # public_key -> runtime
    dbmap = _db_peers_by_public()
    user_tg = _db_users_tg_map()

    now = int(time.time())
    lines: List[str] = []
    total = len(rt)
    lines.append(f"📶 <b>Пиры (всего: {total})</b>:\n")

    # показываем все из runtime (это то, что видит wg)
    for pub, r in rt.items():
        p = dbmap.get(pub)
        name = p["name"] if p else "(вне БД)"
        tg = user_tg.get(p["user_id"], "?") if p else "?"
        ipv4 = r.get("ipv4", p["ipv4"] if p else "")
        ipv6 = r.get("ipv6", p["ipv6"] if p else "")
        endpoint = r.get("endpoint", "(none)")
        latest = int(r.get("latest", 0) or 0)
        rx = int(r.get("rx", 0) or 0)
        tx = int(r.get("tx", 0) or 0)
        status = "online" if latest > 0 and (now - latest) < 130 else ("never" if latest <= 0 else f"offline ({human_delta(now - latest)})")
        hs_line = "never" if latest <= 0 else f"{format_hs(latest)}"
        v4 = f"{ipv4}" if ipv4 else "—"
        v6 = f"{ipv6}" if ipv6 else "—"
        lines.append(
            f"• <b>{H(name)}</b> (tg:{tg})\n"
            f"  статус: {status}\n"
            f"  IPv4: <code>{H(v4)}</code> | IPv6: <code>{H(v6)}</code>\n"
            f"  endpoint: <code>{H(endpoint)}</code>\n"
            f"  last HS: {hs_line} | RX {human_bytes(rx)}, TX {human_bytes(tx)}\n"
        )

    text = "\n".join(lines)
    # безопасно помещаемся в лимит Telegram
    if len(text) > 3500:
        text = text[:3400] + "\n… (обрезано, слишком длинный список)"
    return text

@router.callback_query(F.data == "admin:peers")
async def admin_peers(c: CallbackQuery):
    if not _is_admin(c.from_user.id):
        await c.answer("Нет доступа", show_alert=True); return
    try:
        text = _format_admin_peers_text()
        await _render_panel(c.from_user.id, c, text, admin_panel_kb())
    except Exception as e:
        await _render_panel(c.from_user.id, c, f"Ошибка получения пиров: {html.escape(str(e))}", admin_panel_kb())
    await c.answer()

# ===================================
# СПИСОК ПОДКЛЮЧЕНИЙ / МЕНЮ УСТРОЙСТВА
# ===================================

@router.callback_query(F.data.startswith("peer:menu:"))
async def peer_menu(c: CallbackQuery):
    try:
        peer_id = int(c.data.split(":")[2])
    except Exception:
        await c.answer("Некорректный peer_id", show_alert=True)
        return

    p = DBH.get_peer(peer_id)
    owner = DBH.get_user_by_tg(c.from_user.id)
    if not p or p["user_id"] != owner["id"]:
        await c.answer("Нет доступа", show_alert=True)
        return

    rt = snapshot_map_by_pub(SET.wg_interface).get(p["public_key"])
    latest = int(rt["latest"]) if rt and str(rt["latest"]).isdigit() else 0
    endpoint = rt["endpoint"] if rt else "(none)"

    await _render_panel(c.from_user.id, c, peer_menu_text(p, latest, endpoint), peer_menu_kb(peer_id))
    await c.answer()

@router.callback_query(F.data == "prof:back")
async def peer_back(c: CallbackQuery):
    u = DBH.get_user_by_tg(c.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(c.from_user.id, c, profile_panel_text(u, peers), profile_panel_kb(peers))
    await c.answer()

# =========================================
# ДЕЙСТВИЯ: .CONF / QR / УДАЛЕНИЕ / РЕИМЕН
# =========================================

@router.callback_query(F.data.startswith("conf:"))
async def cb_conf(c: CallbackQuery):
    peer_id = int(c.data.split(":")[1])
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(c.from_user.id)["id"]:
        await c.answer("Нет доступа", show_alert=True)
        return
    try:
        with open(p["conf_path"], "rb") as f:
            buf = f.read()
        doc_msg = await c.message.answer_document(
            BufferedInputFile(buf, filename=os.path.basename(p["conf_path"])),
            caption=os.path.basename(p["conf_path"]) + " (исчезнет через несколько минут)"
        )
        asyncio.create_task(_autodelete_message(doc_msg, TTL_FILES_SEC))
    except Exception:
        try:
            with open(p["conf_path"], "r") as f:
                conf_text = f.read()
            warn = await c.message.answer("Не удалось отправить как файл. Текст конфига ниже (исчезнет через 3 мин):")
            asyncio.create_task(_autodelete_message(warn, TTL_FILES_SEC))
            code = await c.message.answer(f"<pre><code>{html.escape(conf_text)}</code></pre>")
            asyncio.create_task(_autodelete_message(code, TTL_FILES_SEC))
        except Exception as ex:
            await c.message.answer(f"Не удалось прочитать конфиг: {html.escape(ex)}")
    await c.answer()

@router.callback_query(F.data.startswith("qr:"))
async def cb_qr(c: CallbackQuery):
    peer_id = int(c.data.split(":")[1])
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(c.from_user.id)["id"]:
        await c.answer("Нет доступа", show_alert=True)
        return
    try:
        qr_msg = await c.message.answer_photo(FSInputFile(p["qr_path"]), caption=f"QR: {p['name']} (исчезнет через 3 мин)")
        asyncio.create_task(_autodelete_message(qr_msg, TTL_FILES_SEC))
    except Exception:
        await c.message.answer("Не удалось отправить QR.")
    await c.answer()

@router.callback_query(F.data.startswith("rm:"))
async def cb_rm(c: CallbackQuery):
    peer_id = int(c.data.split(":")[1])
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(c.from_user.id)["id"]:
        await c.answer("Нет доступа", show_alert=True)
        return
    try:
        remove_peer(SET.wg_interface, p["public_key"])
    except Exception:
        pass
    DBH.revoke_peer(peer_id)
    try:
        save_config(SET.wg_interface)
    except Exception:
        pass
    u = DBH.get_user_by_tg(c.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(c.from_user.id, c, "🗑 Устройство удалено.\n\n" + profile_panel_text(u, peers), profile_panel_kb(peers))
    await _refresh_main_menu(c, note="🏠 Меню обновлено.")
    await c.answer()

# ==========================
# ПЕРЕИМЕНОВАНИЕ УСТРОЙСТВА
# ==========================

class RenamePeer(StatesGroup):
    waiting_name = State()

@router.callback_query(F.data.startswith("ren:"))
async def cb_rename_start(c: CallbackQuery, state: FSMContext):
    peer_id = int(c.data.split(":")[1])
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(c.from_user.id)["id"]:
        await c.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(RenamePeer.waiting_name)
    await state.update_data(peer_id=peer_id)

    prompt = await c.message.answer("✏️ Введите новое имя устройства (3–32 символа: A–Z, a–z, 0–9, -, _). Сообщение исчезнет через 3 мин.")
    asyncio.create_task(_autodelete_message(prompt, TTL_FILES_SEC))
    await c.answer()

@router.message(RenamePeer.waiting_name)
async def cb_rename_finish(m: Message, state: FSMContext):
    data = await state.get_data()
    peer_id = int(data.get("peer_id", 0))
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(m.from_user.id)["id"]:
        await _render_panel(m.from_user.id, m, "Нет доступа.")
        await state.clear()
        await _maybe_delete_user_input(m)
        return

    new_name = (m.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{3,32}", new_name):
        msg = await m.answer("⚠️ Некорректное имя. Разрешено 3–32 символа: буквы/цифры/дефис/подчёркивание. (исчезнет)")
        asyncio.create_task(_autodelete_message(msg, TTL_NOTE_SEC + 2))
        await _maybe_delete_user_input(m)
        return

    try:
        with DBH._connect() as con:
            cur = con.cursor()
            cur.execute("UPDATE peers SET name=? WHERE id=?", (new_name, peer_id))
            con.commit()

        rt = snapshot_map_by_pub(SET.wg_interface).get(p["public_key"])
        latest = int(rt["latest"]) if rt and str(rt["latest"]).isdigit() else 0
        endpoint = rt["endpoint"] if rt else "(none)"
        p = DBH.get_peer(peer_id)  # перечитать имя

        await _render_panel(
            m.from_user.id,
            m,
            f"✅ Имя обновлено: <b>{html.escape(new_name)}</b>\n\n" + peer_menu_text(p, latest, endpoint),
            peer_menu_kb(peer_id)
        )
    except Exception as e:
        await _render_panel(m.from_user.id, m, f"Ошибка переименования: {html.escape(e)}")
    finally:
        await state.clear()
        await _maybe_delete_user_input(m)

# ==========================================
# УМНЫЕ ФОЛБЭКИ (бот «просыпается» без /start)
# ==========================================

@router.message(StateFilter(None), F.text.as_("t"))
async def fallback_text(m: Message, t: Optional[str]):
    """Не трогаем /команды (важно для админ-хендлеров)."""
    if t:
        if t.startswith("/"):
            return
        if t in KNOWN_TEXT_BUTTONS:
            return

    DBH.upsert_user(
        tg_id=m.from_user.id,
        username=m.from_user.username or "",
        first=m.from_user.first_name or "",
        last=m.from_user.last_name or ""
    )
    await _refresh_main_menu(m, note="✨ Клавиатура обновлена.")
    u = DBH.get_user_by_tg(m.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(m.from_user.id, m, profile_panel_text(u, peers), profile_panel_kb(peers))
    await _maybe_delete_user_input(m)

@router.message(StateFilter(None), ~F.text)
async def fallback_non_text(m: Message):
    DBH.upsert_user(
        tg_id=m.from_user.id,
        username=m.from_user.username or "",
        first=m.from_user.first_name or "",
        last=m.from_user.last_name or ""
    )
    await _refresh_main_menu(m, note="✨ Клавиатура обновлена.")
    u = DBH.get_user_by_tg(m.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    await _render_panel(m.from_user.id, m, profile_panel_text(u, peers), profile_panel_kb(peers))
    # медиа пользователя не удаляем
