import subprocess
import time
from typing import Dict, Optional, Tuple

# wg show all dump — формат:
# interface: ifname  private-key  public-key  listen-port  fwmark
# peer:     ifname   public-key   preshared-key  endpoint  allowed-ips  latest-handshake  transfer-rx  transfer-tx  persistent-keepalive

def wg_dump_all() -> str:
    res = subprocess.run(["sudo", "wg", "show", "all", "dump"], capture_output=True, text=True)
    if res.returncode != 0:
        return ""
    return res.stdout

def parse_dump(dump: str) -> Dict[str, Dict[str, dict]]:
    """
    Возвращает:
    {
      "wg0": {
        "peers": {
           "pubkey": {
               "endpoint": "1.2.3.4:51820",
               "allowed_ips": "10.8.0.2/32, fd86:.../128",
               "latest_handshake": 1699999999,
               "transfer_rx": 12345,
               "transfer_tx": 67890,
               "keepalive": 25
           }, ...
        }
      }
    }
    """
    result: Dict[str, Dict[str, dict]] = {}
    if not dump.strip():
        return result
    lines = [l for l in dump.splitlines() if l.strip()]
    current_iface = None
    for line in lines:
        parts = line.split('\t')
        if len(parts) == 5:
            # interface line
            ifname = parts[0]
            current_iface = ifname
            result.setdefault(ifname, {"peers": {}})
        elif len(parts) == 9 and current_iface:
            # peer line
            _, pub, psk, endpoint, allowed, latest, rx, tx, ka = parts
            try:
                latest_ts = int(latest) if latest.isdigit() else 0
            except:
                latest_ts = 0
            result[current_iface]["peers"][pub] = {
                "endpoint": endpoint,
                "allowed_ips": allowed,
                "latest_handshake": latest_ts,
                "transfer_rx": int(rx) if rx.isdigit() else 0,
                "transfer_tx": int(tx) if tx.isdigit() else 0,
                "keepalive": int(ka) if ka.isdigit() else 0,
            }
    return result

def human_bytes(n: int) -> str:
    units = ["B","KB","MB","GB","TB"]
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units)-1:
        f /= 1024
        i += 1
    return f"{f:.2f} {units[i]}"

def handshake_status(latest_ts: int, now: Optional[int] = None) -> Tuple[str, str]:
    """
    Возвращает (иконка, описание)
    """
    now = now or int(time.time())
    if latest_ts == 0:
        return ("🔴", "нет рукопожатия")
    delta = now - latest_ts
    if delta < 120:
        return ("🟢", f"онлайн ({delta} сек назад)")
    if delta < 1800:
        return ("🟡", f"неактивен {delta//60} мин назад")
    return ("⚪️", f"давно офлайн, {delta//3600} ч назад")
