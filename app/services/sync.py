import time
import app.context as ctx
from app.services import wg as WG

async def _ensure_system_user():
    """
    Для пиров, созданных не через бота (в WGDashboard/вручную),
    кладём их под спец-пользователя 'external' (tg_id=0),
    чтобы админ видел «чьи это» — помечаем как внешние.
    """
    u = await ctx.DBH.get_user(0)
    if u:
        return u
    # создадим «пользователя» с tg_id=0
    await ctx.DBH.get_or_create_user(0, "external", "External", "Peers")
    return await ctx.DBH.get_user(0)

async def resync_from_runtime():
    """
    Синхронизация БД с реальным состоянием интерфейса wg:
    - добавляет в БД отсутствующие «внешние» пиры,
    - обновляет адреса у существующих по public_key.
    """
    sys_user = await _ensure_system_user()
    now = int(time.time())

    rt_peers = WG.runtime_peers()  # список словарей
    for p in rt_peers:
        row = await ctx.DBH.get_peer_by_pub(p["public_key"])
        if row:
            if row["address_v4"] != p["address_v4"] or row["address_v6"] != p["address_v6"]:
                await ctx.DBH.execute(
                    "UPDATE peers SET address_v4=?, address_v6=?, updated_at=? WHERE id=?",
                    (p["address_v4"], p["address_v6"], now, row["id"])
                )
        else:
            # приватного ключа «внешнего» пира у нас нет — кладём заглушку
            name = f"external-{p['public_key'][-6:]}"
            await ctx.DBH.create_peer(
                user_id=sys_user["id"], name=name,
                public_key=p["public_key"], private_key="UNKNOWN",
                address_v4=p["address_v4"], address_v6=p["address_v6"],
                preshared_key=None
            )
