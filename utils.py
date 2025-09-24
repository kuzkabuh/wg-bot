# utils.py
import ipaddress
from typing import Set, Optional
import os
import qrcode
import time

def human_bytes(n: int) -> str:
    for unit in ['B','KB','MB','GB','TB']:
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"

def human_ago(ts: int) -> str:
    if not ts:
        return "никогда"
    delta = int(time.time()) - int(ts)
    if delta < 0: delta = 0
    if delta < 60: return f"{delta}s назад"
    m = delta // 60
    if m < 60: return f"{m}m назад"
    h = m // 60
    if h < 24: return f"{h}h назад"
    d = h // 24
    return f"{d}d назад"

def next_free_ipv4(cidr: str, used: Set[str]) -> Optional[str]:
    """
    Возвращает свободный IPv4 /32 внутри сети cidr, пропуская адрес .1 (сервер).
    """
    network = ipaddress.ip_network(cidr, strict=False)
    for host in network.hosts():
        s = str(host)
        if s.endswith(".1"):
            continue
        if s not in used:
            return f"{s}/32"
    return None

def next_free_ipv6(cidr: str, used: Set[str]) -> Optional[str]:
    """
    Возвращает свободный IPv6 /128 внутри сети cidr, пропуская адрес ::1 (сервер).
    """
    network = ipaddress.ip_network(cidr, strict=False)
    for host in network.hosts():
        s = str(host)
        if s.endswith("::1"):
            continue
        if s not in used:
            return f"{s}/128"
    return None

def qr_png_from_config(conf_text: str, out_path: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img = qrcode.make(conf_text)
    img.save(out_path)
