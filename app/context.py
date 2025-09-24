import os
from config import get_settings
from db import DB

SET = get_settings()
DBH = DB(SET.sqlite_path)

DATA_DIR = SET.data_dir
PEERS_DIR = os.path.join(DATA_DIR, "peers")
os.makedirs(PEERS_DIR, exist_ok=True)

WG_CONF_PATH = f"/etc/wireguard/{SET.wg_interface}.conf"
