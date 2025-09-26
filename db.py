import aiosqlite
import os
import time
from typing import Optional, List

def ensure_dir(path: str):
    if path:
        os.makedirs(path, exist_ok=True)

class DB:
    def __init__(self, path: str):
        ensure_dir(os.path.dirname(path))
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as con:
            await con.execute("PRAGMA journal_mode=WAL;")
            await con.execute("PRAGMA foreign_keys=ON;")
            await con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                plan TEXT DEFAULT 'none',
                devices_limit INTEGER DEFAULT 1,
                expires_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """)
            await con.execute("""
            CREATE TABLE IF NOT EXISTS peers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT,
                public_key TEXT UNIQUE NOT NULL,
                private_key TEXT NOT NULL,
                address_v4 TEXT,
                address_v6 TEXT,
                preshared_key TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """)
            #-- Уникальные индексы на IP, чтобы не было дублей адресов
            await con.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_peer_addr_v4
            ON peers(address_v4) WHERE address_v4 IS NOT NULL;
            """)
            await con.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_peer_addr_v6
            ON peers(address_v6) WHERE address_v6 IS NOT NULL;
            """)
            await con.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                tg_id INTEGER,
                level TEXT,
                message TEXT
            );
            """)
            await con.commit()

    async def fetchone(self, q: str, args: tuple = ()) -> Optional[aiosqlite.Row]:
        async with aiosqlite.connect(self.path) as con:
            con.row_factory = aiosqlite.Row
            cur = await con.execute(q, args)
            row = await cur.fetchone()
            await cur.close()
            return row

    async def fetchall(self, q: str, args: tuple = ()) -> List[aiosqlite.Row]:
        async with aiosqlite.connect(self.path) as con:
            con.row_factory = aiosqlite.Row
            cur = await con.execute(q, args)
            rows = await cur.fetchall()
            await cur.close()
            return rows

    async def execute(self, q: str, args: tuple = ()) -> int:
        async with aiosqlite.connect(self.path) as con:
            cur = await con.execute(q, args)
            await con.commit()
            lastrowid = cur.lastrowid
            await cur.close()
            return lastrowid

    # ====== USERS ======

    async def get_or_create_user(self, tg_id: int, username: str, first_name: str, last_name: str):
        now = int(time.time())
        row = await self.fetchone("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        if row:
            await self.execute("""
                UPDATE users SET username=?, first_name=?, last_name=?, updated_at=?
                WHERE tg_id=?
            """, (username, first_name, last_name, now, tg_id))
            return row
        uid = await self.execute("""
            INSERT INTO users (tg_id, username, first_name, last_name, plan, devices_limit, expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'none', 1, NULL, ?, ?)
        """, (tg_id, username, first_name, last_name, now, now))
        return await self.fetchone("SELECT * FROM users WHERE id=?", (uid,))

    async def set_plan(self, tg_id: int, plan: str, devices_limit: int, expires_at: Optional[int]):
        now = int(time.time())
        await self.execute("""
            UPDATE users SET plan=?, devices_limit=?, expires_at=?, updated_at=? WHERE tg_id=?
        """, (plan, devices_limit, expires_at, now, tg_id))

    async def get_user(self, tg_id: int):
        return await self.fetchone("SELECT * FROM users WHERE tg_id=?", (tg_id,))

    async def list_users(self):
        return await self.fetchall("SELECT * FROM users ORDER BY id DESC")

    async def set_devices_limit(self, tg_id: int, limit: int):
        now = int(time.time())
        await self.execute("""
            UPDATE users SET devices_limit=?, updated_at=? WHERE tg_id=?
        """, (limit, now, tg_id))

    async def prolong(self, tg_id: int, extra_days: int):
        now = int(time.time())
        u = await self.get_user(tg_id)
        if not u:
            return
        expires = u["expires_at"]
        base = expires if expires and expires > now else now
        new_expires = base + extra_days * 86400
        await self.execute("""
            UPDATE users SET expires_at=?, updated_at=? WHERE tg_id=?
        """, (new_expires, now, tg_id))

    # ====== PEERS ======

    async def create_peer(self, user_id: int, name: str, public_key: str, private_key: str,
                          address_v4: Optional[str], address_v6: Optional[str], preshared_key: Optional[str]):
        now = int(time.time())
        pid = await self.execute("""
            INSERT INTO peers (user_id, name, public_key, private_key, address_v4, address_v6, preshared_key, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, public_key, private_key, address_v4, address_v6, preshared_key, now, now))
        return await self.fetchone("SELECT * FROM peers WHERE id=?", (pid,))

    async def list_user_peers(self, user_id: int):
        return await self.fetchall("SELECT * FROM peers WHERE user_id=? ORDER BY id DESC", (user_id,))

    async def get_peer_by_pub(self, public_key: str):
        return await self.fetchone("SELECT * FROM peers WHERE public_key=?", (public_key,))

    async def delete_peer(self, peer_id: int):
        await self.execute("DELETE FROM peers WHERE id=?", (peer_id,))

    async def count_user_peers(self, user_id: int):
        row = await self.fetchone("SELECT COUNT(*) as c FROM peers WHERE user_id=?", (user_id,))
        return row["c"] if row else 0

    # ====== EVENTS ======

    async def add_event(self, tg_id: Optional[int], level: str, message: str):
        now = int(time.time())
        await self.execute(
            "INSERT INTO events (ts, tg_id, level, message) VALUES (?, ?, ?, ?)",
            (now, tg_id, level, message)
        )

    async def list_events(self, limit: int = 50):
        # SQLite поддерживает LIMIT с параметром, это безопасно.
        return await self.fetchall("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
