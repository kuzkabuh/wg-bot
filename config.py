# config.py
from dataclasses import dataclass
from dotenv import load_dotenv
import os
from typing import List, Optional

load_dotenv()

@dataclass
class Settings:
    bot_token: str
    admin_ids: List[int]
    wg_interface: str
    endpoint_host: str
    endpoint_port: int
    network_v4: str
    network_v6: Optional[str]
    dns_servers: str
    data_dir: str
    sqlite_path: str

def get_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in environment (.env)")

    admin_raw = os.getenv("ADMIN_IDS", "").replace(" ", "")
    admin_ids = [int(x) for x in admin_raw.split(",") if x]

    return Settings(
        bot_token=token,
        admin_ids=admin_ids,
        wg_interface=os.getenv("WG_INTERFACE", "wg0"),
        endpoint_host=os.getenv("ENDPOINT_HOST", "kuznetsovartem.ru"),
        endpoint_port=int(os.getenv("ENDPOINT_PORT", "49153")),
        network_v4=os.getenv("NETWORK_V4", "10.66.66.0/24"),
        network_v6=os.getenv("NETWORK_V6", "fd42:42:42::/64") or None,
        dns_servers=os.getenv("DNS", "1.1.1.1,8.8.8.8"),
        data_dir=os.getenv("DATA_DIR", "/var/lib/wg-bot"),
        sqlite_path=os.getenv("SQLITE_PATH", "/var/lib/wg-bot/db.sqlite3"),
    )
