# wg.py
import subprocess
from typing import Optional, Tuple, Set, Dict, List

def _run(cmd: list[str]) -> Tuple[int, str, str]:
    p = subprocess.Popen(["sudo", "-n"] + cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate()
    return p.returncode, out.strip(), err.strip()

def server_public_key(iface: str) -> str:
    code, out, err = _run(["/usr/bin/wg", "show", iface, "public-key"])
    if code != 0:
        raise RuntimeError(f"wg show {iface} public-key failed: {err}")
    return out

def used_ips_v4(iface: str) -> Set[str]:
    code, out, err = _run(["/usr/bin/wg", "show", iface, "dump"])
    if code != 0:
        raise RuntimeError(f"wg show {iface} dump failed: {err}")
    used: Set[str] = set()
    for line in out.splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) < 5:  # skip malformed
            continue
        allowed = parts[3]
        for ipmask in allowed.split(','):
            ipmask = ipmask.strip()
            if ipmask.count('.') == 3 and '/' in ipmask:
                used.add(ipmask.split('/')[0])
    return used

def used_ips_v6(iface: str) -> Set[str]:
    code, out, err = _run(["/usr/bin/wg", "show", iface, "dump"])
    if code != 0:
        raise RuntimeError(f"wg show {iface} dump failed: {err}")
    used: Set[str] = set()
    for line in out.splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        allowed = parts[3]
        for ipmask in allowed.split(','):
            ipmask = ipmask.strip()
            if ':' in ipmask and '/' in ipmask:
                used.add(ipmask.split('/')[0])
    return used

def genkeys() -> Dict[str,str]:
    code, priv, err = _run(["/usr/bin/wg", "genkey"])
    if code != 0:
        raise RuntimeError(f"wg genkey failed: {err}")
    p = subprocess.Popen(["/usr/bin/wg", "pubkey"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err2 = p.communicate(input=priv)
    if p.returncode != 0:
        raise RuntimeError(f"wg pubkey failed: {err2}")
    return {"private": priv.strip(), "public": out.strip()}

def add_peer(iface: str, peer_pub: str, allowed_ipv4: Optional[str], allowed_ipv6: Optional[str]) -> None:
    allowed = []
    if allowed_ipv4: allowed.append(allowed_ipv4)
    if allowed_ipv6: allowed.append(allowed_ipv6)
    cmd = ["/usr/bin/wg", "set", iface, "peer", peer_pub]
    if allowed:
        cmd += ["allowed-ips", ",".join(allowed)]
    code, out, err = _run(cmd)
    if code != 0:
        raise RuntimeError(f"wg set peer failed: {err}")

def remove_peer(iface: str, peer_pub: str) -> None:
    code, out, err = _run(["/usr/bin/wg", "set", iface, "peer", peer_pub, "remove"])
    if code != 0:
        raise RuntimeError(f"wg remove peer failed: {err}")

def save_config(iface: str) -> None:
    code, out, err = _run(["/usr/bin/wg-quick", "save", iface])
    if code != 0:
        raise RuntimeError(f"wg-quick save failed: {err}")

def transfers(iface: str) -> Dict[str, Dict[str,int]]:
    code, out, err = _run(["/usr/bin/wg", "show", iface, "dump"])
    if code != 0:
        raise RuntimeError(f"wg show dump failed: {err}")
    lines = out.splitlines()
    data: Dict[str, Dict[str,int]] = {}
    for line in lines[1:]:
        parts = line.split('\t')
        if len(parts) < 7:
            continue
        pub = parts[0]
        rx = int(parts[5]) if parts[5].isdigit() else 0
        tx = int(parts[6]) if parts[6].isdigit() else 0
        data[pub] = {"rx": rx, "tx": tx}
    return data

def peers_snapshot(iface: str) -> List[Dict[str, str]]:
    """
    Подробный снимок пиров из wg show <iface> dump.
    Возвращает список словарей:
    {
      "public": str,
      "endpoint": str,            # ip:port или "(none)"
      "allowed_ips": str,         # как в dump (может быть v4,v6)
      "latest": str,              # unix ts (строкой) или "0"
      "rx": str,                  # bytes
      "tx": str,                  # bytes
      "keepalive": str            # сек или "off"
    }
    """
    code, out, err = _run(["/usr/bin/wg", "show", iface, "dump"])
    if code != 0:
        raise RuntimeError(f"wg show {iface} dump failed: {err}")
    snap: List[Dict[str,str]] = []
    for line in out.splitlines()[1:]:
        parts = line.split('\t')
        # peer row format (wg show dump): pub, psk, endpoint, allowed_ips, latest_hs, rx, tx, keepalive
        if len(parts) < 8:
            continue
        snap.append({
            "public": parts[0],
            "endpoint": parts[2],
            "allowed_ips": parts[3],
            "latest": parts[4],
            "rx": parts[5],
            "tx": parts[6],
            "keepalive": parts[7]
        })
    return snap
