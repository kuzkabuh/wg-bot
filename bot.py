# bot.py
import asyncio
import os
import time
import html
import re
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile, BufferedInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties

from config import get_settings
from db import DB
from wg import (
    server_public_key, used_ips_v4, used_ips_v6,
    genkeys, add_peer, remove_peer, save_config, transfers, peers_snapshot
)
from utils import next_free_ipv4, next_free_ipv6, qr_png_from_config, human_bytes

SET = get_settings()
DBH = DB(SET.sqlite_path)
DATA_DIR = SET.data_dir
PEERS_DIR = os.path.join(DATA_DIR, "peers")
os.makedirs(PEERS_DIR, exist_ok=True)
WG_CONF_PATH = f"/etc/wireguard/{SET.wg_interface}.conf"

router = Router()

# ---------- УТИЛИТЫ ----------

def H(s: object) -> str:
    return html.escape(str(s), quote=False)

def code_block(s: str) -> str:
    return f"<pre><code>{H(s)}</code></pre>"

def is_admin(tg_id: int) -> bool:
    return tg_id in SET.admin_ids

def days_left(exp: Optional[int]) -> str:
    if not exp:
        return "∞ (безлимит)"
    delta = exp - int(time.time())
    if delta <= 0:
        return "0"
    return str(delta // 86400)

def plan_human(plan: str, devices_limit: int) -> str:
    base = {
        "none": "нет",
        "trial": "триал",
        "paid": "оплачено",
        "unlimited": "безлимит (навсегда)",
    }.get(plan, plan)
    suffix = " · безлимит устройств" if devices_limit < 0 else " · 1 устройство"
    return base + suffix if plan in ("trial", "paid") else base

def months_to_seconds(months: int) -> int:
    # Простой расчёт: 30 дней в месяце
    return months * 30 * 86400

# ---------- ВИТРИНА ТАРИФОВ (звезды со скидками) ----------

BASE_PRICE_1DEV = 100   # 100 звёзд за 1 месяц, 1 устройство
BASE_PRICE_UNLIM = 250  # 250 звёзд за 1 месяц, безлимит устройств

DISCOUNTS = {3: 0.25, 6: 0.50, 12: 0.75}  # 25%/50%/75%

def price_with_discount(base: int, months: int) -> int:
    """
    Возвращаем целое число звёзд для подписки на N месяцев с скидкой.
    Используем округление вниз (floor), чтобы не «перебрать» при .5.
    """
    disc = DISCOUNTS.get(months, 0.0)
    value = base * months * (1.0 - disc)
    return int(value + 1e-9)

def tariff_catalog() -> List[Tuple[str, str]]:
    """
    Возвращает список кнопок: (текст, callback_data)
    Формат callback_data: buy:<tier>:<months>:<stars>
      - tier: one | unlim
      - months: 0 (trial) или 1/3/6/12
    """
    items = []
    # Бесплатный триал
    items.append(("🎁 Бесплатный · 7 дней · 1 устройство", "buy:trial:0:0"))

    # Месяц — 1 устройство / безлимит
    items.append((f"⭐ 100 звёзд · 1 месяц · 1 устройство", f"buy:one:1:{BASE_PRICE_1DEV}"))
    items.append((f"⭐ 250 звёзд · 1 месяц · безлимит", f"buy:unlim:1:{BASE_PRICE_UNLIM}"))

    # 3/6/12 месяцев со скидками — для обоих уровней
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

def user_menu(admin: bool) -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="🎫 Получить доступ (7 дней)"), KeyboardButton(text="💡 Тарифы")],
        [KeyboardButton(text="📥 Мой доступ / Скачать")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="❓ Помощь")],
    ]
    if admin:
        kb.append([KeyboardButton(text="🛠 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ---------- РАНТАЙМ ДАННЫЕ ----------

def build_client_conf(peer_priv: str, addr_v4: Optional[str], addr_v6: Optional[str]) -> str:
    s_pub = server_public_key(SET.wg_interface)
    lines = []
    lines.append("[Interface]")
    lines.append(f"PrivateKey = {peer_priv}")
    if addr_v4 and addr_v6:
        lines.append(f"Address = {addr_v4},{addr_v6}")
    elif addr_v4:
        lines.append(f"Address = {addr_v4}")
    elif addr_v6:
        lines.append(f"Address = {addr_v6}")
    if SET.dns_servers:
        lines.append(f"DNS = {SET.dns_servers}")
    lines.append("")
    lines.append("[Peer]")
    lines.append(f"PublicKey = {s_pub}")
    lines.append("AllowedIPs = 0.0.0.0/0, ::/0")
    lines.append(f"Endpoint = {SET.endpoint_host}:{SET.endpoint_port}")
    lines.append("PersistentKeepalive = 25")
    return "\n".join(lines) + "\n"

def hs_status(latest_ts: int, now: Optional[int] = None) -> str:
    if latest_ts <= 0:
        return "never"
    now = now or int(time.time())
    age = now - latest_ts
    if age <= 180:
        return "online"
    return f"offline ({age//60}m)"

def format_hs(latest_ts: int) -> str:
    return "never" if latest_ts <= 0 else datetime.utcfromtimestamp(latest_ts).strftime("%Y-%m-%d %H:%M:%S UTC")

def snapshot_map_by_pub(iface: str) -> Dict[str, Dict[str, str]]:
    data = {}
    for row in peers_snapshot(iface):
        data[row["public"]] = row
    return data

def merge_user_peers_with_runtime(user_id: int) -> List[Dict[str, Any]]:
    peers = DBH.list_user_peers(user_id, active_only=True)
    smap = snapshot_map_by_pub(SET.wg_interface)
    now = int(time.time())
    enriched = []
    for p in peers:
        row = smap.get(p["public_key"])
        latest = int(row["latest"]) if row and row["latest"].isdigit() else 0
        enriched.append({
            **p,
            "endpoint": (row["endpoint"] if row else "(none)"),
            "latest": latest,
            "status": hs_status(latest, now),
            "rx": (int(row["rx"]) if row and row["rx"].isdigit() else 0),
            "tx": (int(row["tx"]) if row and row["tx"].isdigit() else 0),
        })
    return enriched

# ---------- ФОН: отзыв просрочек ----------
async def expire_sweeper(bot: Bot):
    while True:
        try:
            users = DBH.list_users(limit=10000)
            now = int(time.time())
            for u in users:
                exp = u.get("expires_at")
                plan = u.get("plan")
                # 'unlimited' — навсегда, не трогаем
                if plan in ("none", "unlimited"):
                    continue
                if exp and exp < now:
                    peers = DBH.list_user_peers(u["id"], active_only=True)
                    for p in peers:
                        try:
                            remove_peer(SET.wg_interface, p["public_key"])
                        except Exception:
                            pass
                        DBH.revoke_peer(p["id"])
                    try:
                        save_config(SET.wg_interface)  # фикс в wg0.conf для WGDashboard
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(1800)

# ---------- ПОЛЬЗОВАТЕЛЬ ----------

@router.message(CommandStart())
async def cmd_start(m: Message):
    DBH.upsert_user(
        tg_id=m.from_user.id,
        username=m.from_user.username or "",
        first=m.from_user.first_name or "",
        last=m.from_user.last_name or ""
    )
    await m.answer(
        "👋 Привет! Я помогу подключиться к VPN (WireGuard).\n\n"
        "• 🎁 Бесплатный триал — 7 дней, 1 устройство\n"
        "• ⭐ Тарифы в звёздах — 1м/3м/6м/12м (скидки до 75%)\n"
        "• Админ может выдать безлимит навсегда\n\n"
        "Выберите действие на клавиатуре ниже 👇",
        reply_markup=user_menu(admin=is_admin(m.from_user.id))
    )

# Витрина тарифов
@router.message(F.text == "💡 Тарифы")
async def show_tariffs(m: Message):
    await m.answer(
        "💡 <b>Тарифы</b>\n"
        "Выберите подходящий вариант. Оплата звёздами сейчас — заглушка, списание не выполняется.",
        reply_markup=tariffs_inline_menu()
    )

# Кнопка «Оплатить месяц (заглушка)» — перенаправим в витрину
@router.message(F.text == "💳 Оплатить месяц (заглушка)")
async def pay_redirect(m: Message):
    await show_tariffs(m)

# Покупка тарифа (заглушка Stars → активируем тариф)
@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(c: CallbackQuery):
    # buy:<tier>:<months>:<stars>
    try:
        _, tier, months_s, stars_s = c.data.split(":")
        months = int(months_s)
    except Exception:
        await c.answer("Некорректный параметр", show_alert=True); return

    u = DBH.get_user_by_tg(c.from_user.id)
    now = int(time.time())

    if tier == "trial":
        # Бесплатный: 7 дней, 1 устройство
        DBH.set_plan(u["id"], plan="trial", devices_limit=1, expires_at=now + 7*86400)
        await c.message.answer("✅ Активирован бесплатный триал: 7 дней, 1 устройство. Можно создавать устройство!")
        await c.answer(); return

    # Остальные — платные по звёздам (заглушка)
    # one: 1 устройство; unlim: безлимит устройств
    devices_limit = 1 if tier == "one" else -1
    plan = "paid"  # ВАЖНО: даже для безлимит устройств помечаем как 'paid', чтобы срок учитывался
    start = u["expires_at"] if (u["expires_at"] and u["plan"] == "paid" and u["expires_at"] > now) else now
    expires = start + months_to_seconds(months)

    DBH.set_plan(u["id"], plan=plan, devices_limit=devices_limit, expires_at=expires)

    human = "1 устройство" if devices_limit == 1 else "безлимит устройств"
    await c.message.answer(
        f"✅ Тариф оформлен: <b>{months} мес</b>, <b>{human}</b>.\n"
        f"Срок до: <b>{datetime.utcfromtimestamp(expires).strftime('%Y-%m-%d %H:%M UTC')}</b>\n"
        f"Для импорта создайте или откройте устройство в разделе «📥 Мой доступ / Скачать»."
    )
    await c.answer()

# Классическая кнопка триала — оставляем для удобства
@router.message(F.text == "🎫 Получить доступ (7 дней)")
async def get_trial(m: Message):
    u = DBH.get_user_by_tg(m.from_user.id)
    now = int(time.time())
    if u["plan"] == "none":
        DBH.set_plan(u["id"], plan="trial", devices_limit=1, expires_at=now + 7*86400)
        await m.answer("✅ Триал активирован на 7 дней. Создаю устройство…")
    elif u["plan"] == "trial":
        await m.answer("ℹ️ У вас уже активен триал. Можно создать устройство, если лимит не исчерпан.")
    elif u["plan"] == "paid":
        await m.answer("ℹ️ У вас уже есть оплаченный тариф. Можно создавать устройство.")
    elif u["plan"] == "unlimited":
        await m.answer("ℹ️ У вас безлимитный тариф навсегда.")

    u = DBH.get_user_by_tg(m.from_user.id)
    exp = u["expires_at"]
    if u["plan"] != "unlimited" and (not exp or exp < now):
        await m.answer("⚠️ Сначала активируйте тариф (триал или платный).")
        return

    count = DBH.count_active_peers(u["id"])
    limit = u["devices_limit"]
    if limit >= 0 and count >= limit:
        await m.answer(f"⛔ Лимит устройств исчерпан ({count}/{limit}).")
        return

    try:
        await create_peer_for_user_and_send_to_user(m, u, device_name=f"device-{count+1}")
    except Exception as e:
        await m.answer(f"❌ Ошибка создания устройства: {H(e)}")

# Создание устройства
async def create_peer_for_user_and_send_to_user(m: Message, user_row: dict, device_name: str):
    keys = genkeys()

    used4 = used_ips_v4(SET.wg_interface)
    ipv4 = next_free_ipv4(SET.network_v4, used4)

    ipv6 = None
    if SET.network_v6:
        try:
            used6 = used_ips_v6(SET.wg_interface)
            ipv6 = next_free_ipv6(SET.network_v6, used6)
        except Exception:
            ipv6 = None

    add_peer(SET.wg_interface, keys["public"], ipv4, ipv6)

    user_dir = os.path.join(PEERS_DIR, str(user_row["tg_id"]))
    os.makedirs(user_dir, exist_ok=True)
    conf_text = build_client_conf(keys["private"], ipv4, ipv6)
    conf_path = os.path.join(user_dir, f"{device_name}.conf")
    with open(conf_path, "w") as f:
        f.write(conf_text)
    qr_path = os.path.join(user_dir, f"{device_name}.png")
    qr_png_from_config(conf_text, qr_path)

    DBH.add_peer(
        user_id=user_row["id"], name=device_name,
        private=keys["private"], public=keys["public"],
        ipv4=ipv4, ipv6=ipv6 or "", conf_path=conf_path, qr_path=qr_path
    )

    try:
        save_config(SET.wg_interface)  # запись в wg0.conf для WGDashboard
    except Exception:
        pass

    v6info = f"\n• IPv6: <code>{H(ipv6)}</code>" if ipv6 else ""
    await m.answer(
        "✅ Устройство <b>{}</b> готово!\n"
        "• IPv4: <code>{}</code>{}\n"
        "• Сервер: <code>{}:{}</code>\n\n"
        "Сейчас пришлю файл и QR 😊".format(H(device_name), H(ipv4), v6info, H(SET.endpoint_host), H(SET.endpoint_port))
    )

    try:
        data = conf_text.encode("utf-8")
        await m.answer_document(
            BufferedInputFile(data, filename=f"{device_name}.conf"),
            caption=f"{device_name}.conf — скачайте и импортируйте в приложение WireGuard"
        )
    except TelegramBadRequest:
        await m.answer("Не удалось отправить файл документом. Вот содержимое конфигурации (скопируйте в .conf):")
        await m.answer(code_block(conf_text))

    await m.answer_photo(FSInputFile(qr_path), caption="QR для быстрого импорта в приложении WireGuard")

# ---------- МОИ УСТРОЙСТВА / ДРУЖЕЛЮБНЫЕ КАРТОЧКИ ----------

def devices_inline_kb(peer_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬇️ .conf", callback_data=f"conf:{peer_id}")
    kb.button(text="🧾 QR", callback_data=f"qr:{peer_id}")
    kb.button(text="✏️ Переименовать", callback_data=f"ren:{peer_id}")
    kb.button(text="🗑 Удалить", callback_data=f"rm:{peer_id}")
    kb.adjust(2,2)
    return kb.as_markup()

@router.message(F.text == "📥 Мой доступ / Скачать")
async def my_access(m: Message):
    u = DBH.get_user_by_tg(m.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    if not peers:
        await m.answer("У вас пока нет активных устройств. Оформите тариф или активируйте триал в «💡 Тарифы».")
        return
    await m.answer("📂 Ваши устройства:")
    for p in peers:
        lines = [
            f"🖥 <b>{H(p['name'])}</b>",
            f"IPv4: <code>{H(p['ipv4'])}</code>" + (f"\nIPv6: <code>{H(p['ipv6'])}</code>" if p.get("ipv6") else ""),
            f"статус: <b>{H(p['status'])}</b>",
            f"endpoint: <code>{H(p['endpoint'])}</code>",
            f"last HS: {H(format_hs(p['latest']))}",
            f"трафик: RX {H(human_bytes(p['rx']))}, TX {H(human_bytes(p['tx']))}",
        ]
        await m.answer("\n".join(lines), reply_markup=devices_inline_kb(p["id"]))

@router.callback_query(F.data.startswith("conf:"))
async def cb_conf(c: CallbackQuery):
    peer_id = int(c.data.split(":")[1])
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(c.from_user.id)["id"]:
        await c.answer("Нет доступа", show_alert=True); return
    try:
        with open(p["conf_path"], "rb") as f:
            buf = f.read()
        await c.message.answer_document(
            BufferedInputFile(buf, filename=os.path.basename(p["conf_path"])),
            caption=f"{os.path.basename(p['conf_path'])}"
        )
    except Exception:
        try:
            with open(p["conf_path"], "r") as f:
                conf_text = f.read()
            await c.message.answer("Не удалось отправить как файл. Вот конфигурация (скопируйте в .conf):")
            await c.message.answer(code_block(conf_text))
        except Exception as e:
            await c.message.answer(f"Не удалось прочитать конфиг: {H(e)}")
    await c.answer()

@router.callback_query(F.data.startswith("qr:"))
async def cb_qr(c: CallbackQuery):
    peer_id = int(c.data.split(":")[1])
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(c.from_user.id)["id"]:
        await c.answer("Нет доступа", show_alert=True); return
    await c.message.answer_photo(FSInputFile(p["qr_path"]), caption=f"QR: {p['name']}")
    await c.answer()

@router.callback_query(F.data.startswith("rm:"))
async def cb_rm(c: CallbackQuery):
    peer_id = int(c.data.split(":")[1])
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(c.from_user.id)["id"]:
        await c.answer("Нет доступа", show_alert=True); return
    try:
        remove_peer(SET.wg_interface, p["public_key"])
    except Exception:
        pass
    DBH.revoke_peer(peer_id)
    try:
        save_config(SET.wg_interface)
    except Exception:
        pass
    await c.message.answer("🗑 Готово! Устройство удалено.")
    await c.answer()

# ---------- ПЕРЕИМЕНОВАНИЕ УСТРОЙСТВА ----------

class RenamePeer(StatesGroup):
    waiting_name = State()

@router.callback_query(F.data.startswith("ren:"))
async def cb_rename_start(c: CallbackQuery, state: FSMContext):
    peer_id = int(c.data.split(":")[1])
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(c.from_user.id)["id"]:
        await c.answer("Нет доступа", show_alert=True); return
    await state.set_state(RenamePeer.waiting_name)
    await state.update_data(peer_id=peer_id)
    await c.message.answer("✏️ Введите новое имя устройства (3–32 символа, буквы/цифры/дефис/нижнее подчёркивание):")
    await c.answer()

@router.message(RenamePeer.waiting_name)
async def cb_rename_finish(m: Message, state: FSMContext):
    data = await state.get_data()
    peer_id = int(data.get("peer_id", 0))
    p = DBH.get_peer(peer_id)
    if not p or p["user_id"] != DBH.get_user_by_tg(m.from_user.id)["id"]:
        await m.answer("Нет доступа."); await state.clear(); return
    new_name = m.text.strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{3,32}", new_name):
        await m.answer("⚠️ Некорректное имя. Разрешено 3–32 символа: буквы/цифры/дефис/подчёркивание.")
        return
    try:
        # прямое обновление (в db.py есть connect)
        with DBH._connect() as con:
            cur = con.cursor()
            cur.execute("UPDATE peers SET name=? WHERE id=?", (new_name, peer_id))
            con.commit()
        await m.answer(f"✅ Имя обновлено: <b>{H(new_name)}</b>")
    except Exception as e:
        await m.answer(f"Ошибка переименования: {H(e)}")
    finally:
        await state.clear()

# ---------- ПРОФИЛЬ / СТАТИСТИКА / ПОМОЩЬ ----------

@router.message(F.text == "👤 Профиль")
async def profile(m: Message):
    u = DBH.get_user_by_tg(m.from_user.id)
    exp = u["expires_at"]
    exp_str = "∞" if not exp else datetime.utcfromtimestamp(exp).strftime("%Y-%m-%d %H:%M UTC")
    count = DBH.count_active_peers(u["id"])
    limit = u["devices_limit"]
    limit_str = "безлимит" if limit < 0 else str(limit)
    await m.answer(
        "👤 <b>Ваш профиль</b>\n"
        f"Тариф: <b>{H(plan_human(u['plan'], u['devices_limit']))}</b>\n"
        f"Срок: <b>{H(exp_str)}</b> (осталось дней: <b>{H(days_left(exp))}</b>)\n"
        f"Устройства: <b>{count}</b> из <b>{limit_str}</b>"
    )

@router.message(F.text == "📊 Статистика")
async def stats(m: Message):
    u = DBH.get_user_by_tg(m.from_user.id)
    peers = merge_user_peers_with_runtime(u["id"])
    if not peers:
        await m.answer("Пока нет активных устройств.")
        return
    text = ["📈 Ваша статистика трафика:"]
    for p in peers:
        text.append(f"• {p['name']}: RX {human_bytes(p['rx'])} / TX {human_bytes(p['tx'])} (статус: {p['status']})")
    await m.answer("\n".join(text))

@router.message(F.text == "❓ Помощь")
async def help_msg(m: Message):
    await m.answer(
        "🆘 <b>Как подключиться</b>\n"
        "1) Установите приложение WireGuard (iOS/Android/macOS/Windows/Linux).\n"
        "2) Оформите тариф в «💡 Тарифы» и создайте устройство.\n"
        "3) Скачайте <code>.conf</code> или отсканируйте QR.\n"
        "4) Импортируйте конфиг в приложение и включите туннель.\n\n"
        "💡 Если интернет не идёт через VPN — проверьте: <code>AllowedIPs = 0.0.0.0/0, ::/0</code> и корректный <code>Endpoint</code>."
    )

# ---------- АДМИН ПАНЕЛЬ ----------

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

class AdminPaid(StatesGroup):
    tgid = State()
class AdminUnlim(StatesGroup):
    tgid = State()
class AdminExtend(StatesGroup):
    tgid_days = State()
class AdminSetLimit(StatesGroup):
    tgid_limit = State()
class AdminMkDevice(StatesGroup):
    tgid = State()
class AdminRevokeAll(StatesGroup):
    tgid = State()

@router.message(F.text == "🛠 Админ-панель")
async def admin_panel(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("🛠 Привет, админ! Выбери действие:", reply_markup=admin_inline_menu())

@router.callback_query(F.data == "admin:users")
async def admin_users_cb(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    users = DBH.list_users(limit=30)
    if not users:
        await c.message.answer("Пока нет пользователей.")
    else:
        lines=[]
        for u in users:
            exp = u['expires_at']
            exp_s = ("∞" if not exp else datetime.utcfromtimestamp(exp).strftime("%Y-%m-%d"))
            lines.append(f"tg:{u['tg_id']} | plan:{u['plan']} | limit:{u['devices_limit']} | exp:{exp_s}")
        await c.message.answer("👥 Последние пользователи:\n" + "\n".join(lines))
    await c.answer()

@router.callback_query(F.data == "admin:peers")
async def admin_peers_cb(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    try:
        used4 = used_ips_v4(SET.wg_interface)
        used6 = used_ips_v6(SET.wg_interface)
        await c.message.answer(f"📶 Активные IP у пиров: v4={len(used4)}, v6={len(used6)}")
    except Exception as e:
        await c.message.answer(f"Ошибка: {H(e)}")
    await c.answer()

@router.callback_query(F.data == "admin:peers_detail")
async def admin_peers_detail_cb(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    try:
        snap = peers_snapshot(SET.wg_interface)
        now = int(time.time())
        lines = []
        total = 0
        for row in snap:
            pub = row["public"]
            allowed = row["allowed_ips"]
            endpoint = row["endpoint"]
            latest = int(row["latest"]) if row["latest"].isdigit() else 0
            status = hs_status(latest, now)
            rx = int(row["rx"]) if row["rx"].isdigit() else 0
            tx = int(row["tx"]) if row["tx"].isdigit() else 0

            dbp = DBH.get_peer_by_pub(pub)
            name = dbp["name"] if dbp else "(вне БД)"
            tg_info = f"tg:{dbp['tg_id']}" if dbp and dbp.get("tg_id") else "tg:?"
            ipv4 = dbp["ipv4"] if dbp and dbp.get("ipv4") else ""
            ipv6 = dbp["ipv6"] if dbp and dbp.get("ipv6") else ""
            if not ipv4:
                for ipm in allowed.split(','):
                    ipm = ipm.strip()
                    if ipm.count('.') == 3:
                        ipv4 = ipm; break
            if not ipv6:
                for ipm in allowed.split(','):
                    ipm = ipm.strip()
                    if ':' in ipm:
                        ipv6 = ipm; break

            hs_str = format_hs(latest)
            line = (
                f"• <b>{H(name)}</b> ({H(tg_info)})\n"
                f"  статус: <b>{H(status)}</b>\n"
                f"  IPv4: <code>{H(ipv4 or '-')}</code>" + (f" | IPv6: <code>{H(ipv6)}</code>" if ipv6 else "") + "\n"
                f"  endpoint: <code>{H(endpoint)}</code>\n"
                f"  last HS: {H(hs_str)} | RX {H(human_bytes(rx))}, TX {H(human_bytes(tx))}"
            )
            lines.append(line)
            total += 1

        header = f"📋 Пиры (всего: {total}):"
        if not lines:
            await c.message.answer(header + "\n— нет активных пиров —")
        else:
            await c.message.answer(header)
            chunk = []
            size = 0
            for ln in lines:
                if size + len(ln) > 3500:
                    await c.message.answer("\n\n".join(chunk))
                    chunk, size = [], 0
                chunk.append(ln)
                size += len(ln)
            if chunk:
                await c.message.answer("\n\n".join(chunk))
    except Exception as e:
        await c.message.answer(f"Ошибка: {H(e)}")
    await c.answer()

@router.callback_query(F.data == "admin:saveconf")
async def admin_saveconf_cb(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    try:
        save_config(SET.wg_interface)
        file_txt = ""
        try:
            with open(WG_CONF_PATH, "r") as f:
                file_txt = f.read()
        except Exception as e:
            file_txt = f"(не удалось прочитать {WG_CONF_PATH}: {e})"
        peers_in_file = len(re.findall(r"^\[Peer\]", file_txt, flags=re.M))
        kern = peers_snapshot(SET.wg_interface)
        await c.message.answer(f"💾 Сохранено в {H(WG_CONF_PATH)}.\n"
                               f"В файле [Peer]: <b>{peers_in_file}</b>\n"
                               f"В ядре (wg show): <b>{len(kern)}</b>")
    except Exception as e:
        await c.message.answer(f"Ошибка сохранения: {H(e)}")
    await c.answer()

@router.callback_query(F.data == "admin:conf_list")
async def admin_conf_list_cb(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    try:
        with open(WG_CONF_PATH, "r") as f:
            txt = f.read()
        blocks = re.split(r"(?m)^\[Peer\]\s*", txt)
        peers = []
        for b in blocks[1:]:
            pub = re.search(r"(?m)^PublicKey\s*=\s*(.+)$", b)
            ips = re.search(r"(?m)^AllowedIPs\s*=\s*(.+)$", b)
            peers.append((pub.group(1).strip() if pub else "?", ips.group(1).strip() if ips else "?"))
        if not peers:
            await c.message.answer("📄 В wg0.conf нет пиров.")
        else:
            lines = ["📄 Пиры из wg0.conf:"]
            for pub, ips in peers:
                lines.append(f"• <code>{H(pub)}</code>\n  AllowedIPs: <code>{H(ips)}</code>")
            chunk = []
            size = 0
            for ln in lines:
                if size + len(ln) > 3500:
                    await c.message.answer("\n".join(chunk))
                    chunk, size = [], 0
                chunk.append(ln)
                size += len(ln)
            if chunk:
                await c.message.answer("\n".join(chunk))
    except Exception as e:
        await c.message.answer(f"Ошибка чтения {H(WG_CONF_PATH)}: {H(e)}")
    await c.answer()

# ---- выдача тарифов / лимитов / устройств (FSM) ----

@router.callback_query(F.data == "admin:grant_paid")
async def admin_paid_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminPaid.tgid)
    await c.message.answer("Введите tg_id пользователя для выдачи <b>месяца</b> (paid, 1 устройство):")
    await c.answer()

@router.message(AdminPaid.tgid)
async def admin_paid_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        tg_id = int(m.text.strip())
        u = DBH.ensure_user(tg_id)
        now = int(time.time())
        DBH.set_plan(u["id"], plan="paid", devices_limit=1, expires_at=now + months_to_seconds(1))
        await m.answer(f"✅ Пользователю tg:{H(tg_id)} выдан месяц (paid, 1 устройство).")
    except Exception as e:
        await m.answer(f"Ошибка: {H(e)}")
    finally:
        await state.clear()

@router.callback_query(F.data == "admin:grant_unlim")
async def admin_unlim_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminUnlim.tgid)
    await c.message.answer("Введите tg_id пользователя для выдачи <b>безлимита навсегда</b> (∞):")
    await c.answer()

@router.message(AdminUnlim.tgid)
async def admin_unlim_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        tg_id = int(m.text.strip())
        u = DBH.ensure_user(tg_id)
        DBH.set_plan(u["id"], plan="unlimited", devices_limit=-1, expires_at=None)
        await m.answer(f"✅ Пользователю tg:{H(tg_id)} выдан безлимит <b>навсегда</b>.")
    except Exception as e:
        await m.answer(f"Ошибка: {H(e)}")
    finally:
        await state.clear()

@router.callback_query(F.data == "admin:extend")
async def admin_extend_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminExtend.tgid_days)
    await c.message.answer("Введите: <code>tg_id</code> пробел <code>days</code> (например: <code>123456789 7</code>)")
    await c.answer()

@router.message(AdminExtend.tgid_days)
async def admin_extend_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        parts = m.text.strip().split()
        if len(parts) != 2: raise ValueError("Формат: tg_id days")
        tg_id = int(parts[0]); days = int(parts[1])
        u = DBH.ensure_user(tg_id)
        now = int(time.time())
        start = u["expires_at"] if (u["expires_at"] and u["expires_at"]>now) else now
        DBH.set_plan(u["id"], plan=("paid" if u["plan"]!="unlimited" else "unlimited"),
                     devices_limit=u["devices_limit"], expires_at=(None if u["plan"]=="unlimited" else start + days*86400))
        await m.answer(f"✅ Продлено: tg:{H(tg_id)} на {H(days)} дней.")
    except Exception as e:
        await m.answer(f"Ошибка: {H(e)}")
    finally:
        await state.clear()

@router.callback_query(F.data == "admin:setlimit")
async def admin_setlimit_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminSetLimit.tgid_limit)
    await c.message.answer("Введите: <code>tg_id</code> пробел <code>limit</code> (например: <code>123456789 3</code>, -1 для безлимита)")
    await c.answer()

@router.message(AdminSetLimit.tgid_limit)
async def admin_setlimit_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        parts = m.text.strip().split()
        if len(parts) != 2: raise ValueError("Формат: tg_id limit")
        tg_id = int(parts[0]); limit = int(parts[1])
        u = DBH.ensure_user(tg_id)
        DBH.set_plan(u["id"], plan=u["plan"], devices_limit=limit, expires_at=u["expires_at"])
        await m.answer(f"✅ Обновлён лимит: tg:{H(tg_id)} → {('безлимит' if limit<0 else H(limit))}.")
    except Exception as e:
        await m.answer(f"Ошибка: {H(e)}")
    finally:
        await state.clear()

@router.callback_query(F.data == "admin:mkdevice")
async def admin_mkdevice_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminMkDevice.tgid)
    await c.message.answer("Введите tg_id пользователя, для которого создать <b>устройство</b> (конфиг придёт вам):")
    await c.answer()

class AdminMkDevice(StatesGroup):
    tgid = State()

@router.message(AdminMkDevice.tgid)
async def admin_mkdevice_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        tg_id = int(m.text.strip())
        u = DBH.ensure_user(tg_id)
        now = int(time.time())
        exp = u["expires_at"]
        if not (u["plan"] in ("trial","paid","unlimited") and (u["plan"]=="unlimited" or (exp and exp>now))):
            return await m.answer("⛔ У пользователя нет активного тарифа. Сначала выдайте месяц/безлимит или пусть активирует триал.")

        count = DBH.count_active_peers(u["id"])
        limit = u["devices_limit"]
        if limit >= 0 and count >= limit:
            return await m.answer(f"⛔ Лимит устройств исчерпан ({count}/{limit}). Увеличьте лимит.")

        device_name = f"device-{count+1}"
        keys = genkeys()

        used4 = used_ips_v4(SET.wg_interface)
        ipv4 = next_free_ipv4(SET.network_v4, used4)

        ipv6 = None
        if SET.network_v6:
            try:
                used6 = used_ips_v6(SET.wg_interface)
                ipv6 = next_free_ipv6(SET.network_v6, used6)
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
            save_config(SET.wg_interface)  # фикс в wg0.conf
        except Exception:
            pass

        msg = f"✅ Создано устройство для tg:{H(tg_id)}: <b>{H(device_name)}</b>\nIPv4: <code>{H(ipv4)}</code>"
        if ipv6: msg += f"\nIPv6: <code>{H(ipv6)}</code>"
        await m.answer(msg)

        await m.answer_document(BufferedInputFile(conf_text.encode("utf-8"), filename=f"{device_name}.conf"),
                                caption=f"{device_name}.conf — отправьте пользователю tg:{tg_id}")
        await m.answer_photo(FSInputFile(qr_path), caption=f"QR для tg:{tg_id}")
    except Exception as e:
        await m.answer(f"Ошибка: {H(e)}")
    finally:
        await state.clear()

@router.callback_query(F.data == "admin:revoke_all")
async def admin_revoke_all_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminRevokeAll.tgid)
    await c.message.answer("Введите tg_id пользователя, у которого <b>отозвать все устройства</b>:")
    await c.answer()

class AdminRevokeAll(StatesGroup):
    tgid = State()

@router.message(AdminRevokeAll.tgid)
async def admin_revoke_all_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        tg_id = int(m.text.strip())
        u = DBH.get_user_by_tg(tg_id)
        if not u:
            return await m.answer("Пользователь не найден.")
        peers = DBH.list_user_peers(u["id"], active_only=True)
        for p in peers:
            try:
                remove_peer(SET.wg_interface, p["public_key"])
            except Exception:
                pass
            DBH.revoke_peer(p["id"])
        try:
            save_config(SET.wg_interface)
        except Exception:
            pass
        await m.answer(f"✅ Все устройства пользователя tg:{H(tg_id)} отозваны.")
    except Exception as e:
        await m.answer(f"Ошибка: {H(e)}")
    finally:
        await state.clear()

# ---------- main ----------
async def main():
    bot = Bot(SET.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(expire_sweeper(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
