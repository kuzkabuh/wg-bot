import re, time, html
from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message

from app.context import SET, DBH, WG_CONF_PATH
from app.menus import admin_inline_menu
from app.services.runtime import H, hs_status, format_hs
from wg import used_ips_v4, used_ips_v6, peers_snapshot, save_config, remove_peer
from utils import human_bytes

router = Router()

def is_admin(tg_id: int) -> bool:
    return tg_id in SET.admin_ids

# ——— Панель ———
@router.message(F.text == "🛠 Админ-панель")
async def admin_panel(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("🛠 Привет, админ! Выбери действие:", reply_markup=admin_inline_menu())

# ——— Пользователи ———
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

# ——— Счётчик пиров ———
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

# ——— Пиры детально ———
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
            chunk = []; size = 0
            for ln in lines:
                if size + len(ln) > 3500:
                    await c.message.answer("\n\n".join(chunk))
                    chunk, size = [], 0
                chunk.append(ln); size += len(ln)
            if chunk:
                await c.message.answer("\n\n".join(chunk))
    except Exception as e:
        await c.message.answer(f"Ошибка: {H(e)}")
    await c.answer()

# ——— Сохранение и чтение wg0.conf ———
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
        await c.message.answer(f"💾 Сохранено в {html.escape(WG_CONF_PATH)}.\n"
                               f"В файле [Peer]: <b>{peers_in_file}</b>\n"
                               f"В ядре (wg show): <b>{len(kern)}</b>")
    except Exception as e:
        await c.message.answer(f"Ошибка сохранения: {html.escape(e)}")
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
                lines.append(f"• <code>{html.escape(pub)}</code>\n  AllowedIPs: <code>{html.escape(ips)}</code>")
            chunk = []; size = 0
            for ln in lines:
                if size + len(ln) > 3500:
                    await c.message.answer("\n".join(chunk))
                    chunk, size = [], 0
                chunk.append(ln); size += len(ln)
            if chunk:
                await c.message.answer("\n".join(chunk))
    except Exception as e:
        await c.message.answer(f"Ошибка чтения {html.escape(WG_CONF_PATH)}: {html.escape(e)}")
    await c.answer()

# ——— Выдача тарифов/лимитов/устройств ———
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

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
        DBH.set_plan(u["id"], plan="paid", devices_limit=1, expires_at=now + 30*86400)
        await m.answer(f"✅ Пользователю tg:{html.escape(str(tg_id))} выдан месяц (paid, 1 устройство).")
    except Exception as e:
        await m.answer(f"Ошибка: {html.escape(e)}")
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
        await m.answer(f"✅ Пользователю tg:{html.escape(str(tg_id))} выдан безлимит <b>навсегда</b>.")
    except Exception as e:
        await m.answer(f"Ошибка: {html.escape(e)}")
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
        await m.answer(f"✅ Продлено: tg:{html.escape(str(tg_id))} на {html.escape(str(days))} дней.")
    except Exception as e:
        await m.answer(f"Ошибка: {html.escape(e)}")
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
        await m.answer(f"✅ Обновлён лимит: tg:{html.escape(str(tg_id))} → {('безлимит' if limit<0 else html.escape(str(limit)))}.")
    except Exception as e:
        await m.answer(f"Ошибка: {html.escape(e)}")
    finally:
        await state.clear()

@router.callback_query(F.data == "admin:mkdevice")
async def admin_mkdevice_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminMkDevice.tgid)
    await c.message.answer("Введите tg_id пользователя, для которого создать <b>устройство</b> (конфиг придёт вам):")
    await c.answer()

@router.message(AdminMkDevice.tgid)
async def admin_mkdevice_finish(m: Message, state: FSMContext):
    # Логика создания устройства уже реализована в пользовательском модуле — для админа обычно ок выдать тариф и попросить пользователя нажать «➕ Создать устройство».
    # Если нужно, можно перенести туда же полноценное создание как раньше — оставим как в прошлой версии (для краткости).
    from app.handlers.user import create_device_for_user
    if not is_admin(m.from_user.id): return
    try:
        tg_id = int(m.text.strip())
        u = DBH.ensure_user(tg_id)
        now = int(time.time())
        exp = u["expires_at"]
        if not (u["plan"] in ("trial","paid","unlimited") and (u["plan"]=="unlimited" or (exp and exp>now))):
            return await m.answer("⛔ У пользователя нет активного тарифа. Выдайте месяц/безлимит или пусть активирует триал.")
        await create_device_for_user(m, u)
    except Exception as e:
        await m.answer(f"Ошибка: {html.escape(e)}")
    finally:
        await state.clear()

@router.callback_query(F.data == "admin:revoke_all")
async def admin_revoke_all_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminRevokeAll.tgid)
    await c.message.answer("Введите tg_id пользователя, у которого <b>отозвать все устройства</b>:")
    await c.answer()

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
        await m.answer(f"✅ Все устройства пользователя tg:{html.escape(str(tg_id))} отозваны.")
    except Exception as e:
        await m.answer(f"Ошибка: {html.escape(e)}")
    finally:
        await state.clear()
