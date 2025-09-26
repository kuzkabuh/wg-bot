import os
import logging
from dataclasses import dataclass
from typing import List
from dotenv import load_dotenv
from db import DB

logger = logging.getLogger("ctx")

@dataclass
class Settings:
    BOT_TOKEN: str
    ADMIN_IDS: List[int]

    WG_INTERFACE: str
    WG_CONFIG: str
    SERVER_PUBLIC_ENDPOINT: str
    SERVER_PUBLIC_PORT: int
    SERVER_PUBLIC_KEY: str

    WG_PRIVATE_SUBNET_V4: str
    WG_PRIVATE_SUBNET_V6: str
    WG_DNS: str
    WG_ALLOWED_IPS: str

    DATA_DIR: str
    CONFIGS_DIR: str
    QRCODES_DIR: str

    TRIAL_DAYS: int
    MONTH_DAYS: int
    DEFAULT_DEVICES_LIMIT: int

    DELETE_MESSAGES: bool
    TZ: str

    DB_PATH: str = "./data/wgbot.sqlite3"

SET: Settings = None
DBH: DB = None

def _env_list_int(v: str) -> List[int]:
    if not v:
        return []
    return [int(x.strip()) for x in v.split(",") if x.strip()]

def init_context():
    load_dotenv()
    global SET, DBH
    SET = Settings(
        BOT_TOKEN=os.environ.get("BOT_TOKEN", "").strip(),
        ADMIN_IDS=_env_list_int(os.environ.get("ADMIN_IDS", "")),

        WG_INTERFACE=os.environ.get("WG_INTERFACE", "wg0").strip(),
        WG_CONFIG=os.environ.get("WG_CONFIG", "/etc/wireguard/wg0.conf").strip(),
        SERVER_PUBLIC_ENDPOINT=os.environ.get("SERVER_PUBLIC_ENDPOINT", "127.0.0.1").strip(),
        SERVER_PUBLIC_PORT=int(os.environ.get("SERVER_PUBLIC_PORT", "49153")),
        SERVER_PUBLIC_KEY=os.environ.get("SERVER_PUBLIC_KEY", "").strip(),

        WG_PRIVATE_SUBNET_V4=os.environ.get("WG_PRIVATE_SUBNET_V4", "10.8.0.0/24").strip(),
        WG_PRIVATE_SUBNET_V6=os.environ.get("WG_PRIVATE_SUBNET_V6", "fd86:ea04:1115::/64").strip(),
        WG_DNS=os.environ.get("WG_DNS", "1.1.1.1,1.0.0.1").strip(),
        WG_ALLOWED_IPS=os.environ.get("WG_ALLOWED_IPS", "0.0.0.0/0,::/0").strip(),

        DATA_DIR=os.environ.get("DATA_DIR", "./data").strip(),
        CONFIGS_DIR=os.environ.get("CONFIGS_DIR", "./data/configs").strip(),
        QRCODES_DIR=os.environ.get("QRCODES_DIR", "./data/qrcodes").strip(),

        TRIAL_DAYS=int(os.environ.get("TRIAL_DAYS", "7")),
        MONTH_DAYS=int(os.environ.get("MONTH_DAYS", "30")),
        DEFAULT_DEVICES_LIMIT=int(os.environ.get("DEFAULT_DEVICES_LIMIT", "1")),

        DELETE_MESSAGES=os.environ.get("DELETE_MESSAGES", "false").lower() == "true",
        TZ=os.environ.get("TZ", "Europe/Moscow").strip(),

        DB_PATH=os.environ.get("DB_PATH", "./data/wgbot.sqlite3").strip()
    )
    if not SET.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required in .env")

    # Создаём хэндл БД, без init (его вызовем в bot.py через await DBH.init())
    DBH = DB(SET.DB_PATH)
    logger.info("Context initialized")
