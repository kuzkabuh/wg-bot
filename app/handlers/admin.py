from aiogram import Router, F, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

import app.context as ctx
from app.menus import admin_menu, admin_user_actions, ADMIN_BTN_TEXT
from app.services import wg as WG

router = Router(name="admin")

def register_admin_handlers(dp: Dispatcher):
    dp.include_router(router)

def is_admin(uid: int) -> bool:
    return uid in ctx.SET.ADMIN_IDS

def format_user_row(u) -> str:
    lim = "∞" if (u["devices_limit"] < 0 or u["plan"] == "unlimited") else str(u["devices_limit"])
    return f"#{u['id']} tg:{u['tg_id']} @{u['username'] or '-'} plan={u['plan']} lim={lim} exp={u['expires_at'] or '-'}"

@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Админ-панель", reply_markup=admin_menu())

# ReplyKeyboard-кнопка "🛡 Админ-панель"
@router.message(F.text == ADMIN_BTN_TEXT)
async def admin_btn(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Админ-панель", reply_markup=admin_menu())

@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    await c.message.answer("Админ-панель", reply_markup=admin_menu())
    await c.answer()

@router.callback_query(F.data == "adm_tools")
async def cb_adm_tools(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    await c.message.answer("Инструменты: сюда можно добавить команды перезагрузки WG, бэкапа и т.п.")
    await c.answer()

@router.callback_query(F.data == "adm_logs")
async def cb_adm_logs(c: CallbackQuery):
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
        lines.append(f"{e['ts']}: {e['level']} tg:{e['tg_id'] or '-'} {e['message']}")
    await c.message.answer("\n".join(lines[:50]))
    await c.answer()

@router.callback_query(F.data == "adm_users")
async def cb_adm_users(c: CallbackQuery):
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
        lines.append(format_user_row(u))
    await c.message.answer("\n".join(lines))
    await c.answer()

@router.message(Command("act"))
async def cmd_act(message: Message):
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
async def cb_adm_trial(c: CallbackQuery):
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
async def cb_adm_month(c: CallbackQuery):
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
async def cb_adm_unlim(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    tg_id = int(c.data.split(":")[1])
    await ctx.DBH.set_plan(tg_id, "unlimited", -1, None)
    await c.message.answer(f"Установлен безлимит для tg:{tg_id}")
    await c.answer()

@router.callback_query(F.data.startswith("adm_limit:"))
async def cb_adm_limit(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    tg_id = int(c.data.split(":")[1])
    await c.message.answer(f"Отправьте команду /limit {tg_id} <число> (−1 для бесконечности)")
    await c.answer()

@router.message(Command("limit"))
async def cmd_limit(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer("Формат: /limit <tg_id> <число>")
        return
    tg_id = int(parts[1])
    try:
        lim = int(parts[2])
    except:
        await message.answer("Лимит должен быть целым числом (−1 для ∞)")
        return
    await ctx.DBH.set_devices_limit(tg_id, lim)
    await message.answer(f"Лимит устройств для tg:{tg_id} установлен: {('∞' if lim<0 else lim)}")

# ===== Пиры (с владельцами) =====

@router.callback_query(F.data == "adm_peers")
async def cb_adm_peers(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    rows = await ctx.DBH.fetchall("""
        SELECT p.id AS pid, p.name, p.public_key, p.address_v4, p.address_v6,
               u.tg_id, u.username, u.first_name, u.last_name
        FROM peers p
        JOIN users u ON u.id = p.user_id
        ORDER BY p.id DESC
        LIMIT 100
    """)
    if not rows:
        await c.message.answer("Пиров нет.")
        await c.answer()
        return
    lines = ["<b>Пиры (последние 100)</b> — нажмите /delpeer &lt;pid&gt; для удаления"]
    for r in rows:
        owner = f"@{r['username']}" if r["username"] else f"tg:{r['tg_id']}"
        lines.append(f"#{r['pid']} «{r['name']}» | {owner} | v4:{r['address_v4'] or '-'} v6:{r['address_v6'] or '-'}")
    await c.message.answer("\n".join(lines))
    await c.message.answer("Для удаления: /delpeer <pid>")
    await c.answer()

@router.message(Command("delpeer"))
async def cmd_adm_delpeer(message: Message):
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
