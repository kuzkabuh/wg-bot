# db.py
import sqlite3
import os
import time
from typing import Optional, List, Dict, Any

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

class DB:
    def __init__(self, path: str):
        ensure_dir(os.path.dirname(path))
        self.path = path
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path, check_same_thread=False)

    def _init(self):
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                plan TEXT DEFAULT 'none',           -- none|trial|paid|unlimited
                devices_limit INTEGER DEFAULT 1,    -- -1 => безлимит
                expires_at INTEGER,                 -- unix ts, NULL для безлимита
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS peers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                private_key TEXT NOT NULL,
                public_key TEXT NOT NULL UNIQUE,
                ipv4 TEXT,
                ipv6 TEXT,
                conf_path TEXT,
                qr_path TEXT,
                created_at INTEGER NOT NULL,
                revoked_at INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """)
            con.commit()

    # ----- Users -----
    def upsert_user(self, tg_id: int, username: str, first: str, last: str) -> Dict[str, Any]:
        now = int(time.time())
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,))
            row = cur.fetchone()
            if row:
                user_id = row[0]
                cur.execute("""
                    UPDATE users SET username=?, first_name=?, last_name=?, updated_at=? WHERE id=?
                """, (username, first, last, now, user_id))
            else:
                cur.execute("""
                    INSERT INTO users (tg_id, username, first_name, last_name, plan, devices_limit, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'none', 1, ?, ?)
                """, (tg_id, username, first, last, now, now))
                user_id = cur.lastrowid
            con.commit()
            return self.get_user_by_tg(tg_id)

    def ensure_user(self, tg_id: int) -> Dict[str, Any]:
        u = self.get_user_by_tg(tg_id)
        if u: return u
        return self.upsert_user(tg_id, "", "", "")

    def get_user_by_tg(self, tg_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT id, tg_id, username, first_name, last_name, plan, devices_limit, expires_at, created_at, updated_at
                FROM users WHERE tg_id=?
            """, (tg_id,))
            row = cur.fetchone()
            if not row:
                return None
            keys = ["id","tg_id","username","first_name","last_name","plan","devices_limit","expires_at","created_at","updated_at"]
            return dict(zip(keys, row))

    def set_plan(self, user_id: int, plan: str, devices_limit: int, expires_at: Optional[int]):
        now = int(time.time())
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                UPDATE users SET plan=?, devices_limit=?, expires_at=?, updated_at=? WHERE id=?
            """, (plan, devices_limit, expires_at, now, user_id))
            con.commit()

    def list_users(self, limit: int = 30) -> List[Dict[str,Any]]:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT id, tg_id, username, first_name, last_name, plan, devices_limit, expires_at, created_at
                FROM users ORDER BY id DESC LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            out=[]
            for r in rows:
                out.append({
                    "id": r[0], "tg_id": r[1], "username": r[2], "first_name": r[3], "last_name": r[4],
                    "plan": r[5], "devices_limit": r[6], "expires_at": r[7], "created_at": r[8]
                })
            return out

    # ----- Peers -----
    def count_active_peers(self, user_id: int) -> int:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM peers WHERE user_id=? AND revoked_at IS NULL", (user_id,))
            return cur.fetchone()[0]

    def add_peer(self, user_id: int, name: str, private: str, public: str,
                 ipv4: str, ipv6: str, conf_path: str, qr_path: str) -> int:
        now = int(time.time())
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                INSERT INTO peers (user_id,name,private_key,public_key,ipv4,ipv6,conf_path,qr_path,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (user_id, name, private, public, ipv4, ipv6, conf_path, qr_path, now))
            con.commit()
            return cur.lastrowid

    def get_peer(self, peer_id: int) -> Optional[Dict[str,Any]]:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT id,user_id,name,public_key,private_key,ipv4,ipv6,conf_path,qr_path,revoked_at
                FROM peers WHERE id=?
            """, (peer_id,))
            r = cur.fetchone()
            if not r: return None
            keys = ["id","user_id","name","public_key","private_key","ipv4","ipv6","conf_path","qr_path","revoked_at"]
            return dict(zip(keys, r))

    def get_peer_by_pub(self, pub: str) -> Optional[Dict[str,Any]]:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT p.id,p.user_id,p.name,p.public_key,p.ipv4,p.ipv6,p.conf_path,p.qr_path,u.tg_id
                FROM peers p JOIN users u ON p.user_id=u.id
                WHERE p.public_key=? AND p.revoked_at IS NULL
            """, (pub,))
            r = cur.fetchone()
            if not r: return None
            keys = ["id","user_id","name","public_key","ipv4","ipv6","conf_path","qr_path","tg_id"]
            return dict(zip(keys, r))

    def list_user_peers(self, user_id: int, active_only: bool=True) -> List[Dict[str,Any]]:
        with self._connect() as con:
            cur = con.cursor()
            if active_only:
                cur.execute("""
                    SELECT id,name,public_key,ipv4,ipv6,conf_path,qr_path,created_at FROM peers
                    WHERE user_id=? AND revoked_at IS NULL ORDER BY id DESC
                """, (user_id,))
            else:
                cur.execute("""
                    SELECT id,name,public_key,ipv4,ipv6,conf_path,qr_path,created_at,revoked_at FROM peers
                    WHERE user_id=? ORDER BY id DESC
                """, (user_id,))
            rows = cur.fetchall()
            keys = ["id","name","public_key","ipv4","ipv6","conf_path","qr_path","created_at"] if active_only \
                   else ["id","name","public_key","ipv4","ipv6","conf_path","qr_path","created_at","revoked_at"]
            return [dict(zip(keys, r)) for r in rows]

    def list_all_active_peers(self, limit: int = 200) -> List[Dict[str,Any]]:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT p.id,p.user_id,p.name,p.public_key,p.ipv4,p.ipv6,u.tg_id
                FROM peers p JOIN users u ON p.user_id=u.id
                WHERE p.revoked_at IS NULL
                ORDER BY p.id DESC LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            keys = ["id","user_id","name","public_key","ipv4","ipv6","tg_id"]
            return [dict(zip(keys, r)) for r in rows]

    def revoke_peer(self, peer_id: int):
        now = int(time.time())
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("UPDATE peers SET revoked_at=? WHERE id=?", (now, peer_id))
            con.commit()
