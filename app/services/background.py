import asyncio, time
from app.context import DBH, SET
from wg import remove_peer, save_config

async def expire_sweeper():
    while True:
        try:
            users = DBH.list_users(limit=10000)
            now = int(time.time())
            for u in users:
                exp = u.get("expires_at")
                plan = u.get("plan")
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
                        save_config(SET.wg_interface)
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(1800)
