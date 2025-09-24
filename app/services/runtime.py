import html, time, re
from datetime import datetime
from typing import Dict, Any, List, Optional
from app.context import DBH, SET
from wg import peers_snapshot
from utils import human_bytes

def H(s: object) -> str:
    return html.escape(str(s), quote=False)

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

def human_delta(seconds: int) -> str:
    """Человеческое 'давно': 37 м, 5 ч, 2 д, 0 с."""
    if seconds < 60:
        return f"{seconds} с"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} м"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч"
    days = hours // 24
    return f"{days} д"

def status_badge(latest_ts: int, now: Optional[int] = None) -> str:
    """Статус со значком и временем: 🟢 Онлайн / 🟡 Офлайн (37 м) / ⚫️ Никогда."""
    if latest_ts <= 0:
        return "⚫️ Никогда"
    now = now or int(time.time())
    age = now - latest_ts
    if age <= 180:
        return "🟢 Онлайн"
    return f"🟡 Офлайн ({human_delta(age)})"

def human_delta(seconds: int) -> str:
    """Человеческое 'давно': 37 м, 5 ч, 2 д, 0 с."""
    if seconds < 60:
        return f"{seconds} с"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} м"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч"
    days = hours // 24
    return f"{days} д"

def status_icon(latest_ts: int, now: Optional[int] = None) -> str:
    """Только значок статуса."""
    if latest_ts <= 0:
        return "⚫️"
    now = now or int(time.time())
    return "🟢" if (now - latest_ts) <= 180 else "🟡"

def status_badge(latest_ts: int, now: Optional[int] = None) -> str:
    """Статус со значком и словом: 🟢 Онлайн / 🟡 Офлайн (37 м) / ⚫️ Никогда."""
    if latest_ts <= 0:
        return "⚫️ Никогда"
    now = now or int(time.time())
    age = now - latest_ts
    if age <= 180:
        return "🟢 Онлайн"
    return f"🟡 Офлайн ({human_delta(age)})"
