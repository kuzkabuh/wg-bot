import ipaddress
import os
import re
import subprocess
from typing import Optional, Tuple, List, Set, Dict

import app.context as ctx

def run(cmd: list, check: bool = False) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{res.stdout}\n{res.stderr}")
    return res

# ---------- чтение состояния/конфига ----------

def _read_config_text() -> str:
    try:
        with open(ctx.SET.WG_CONFIG, "r") as f:
            return f.read()
    except Exception:
        return ""

def _net(s: str):
    try:
        return ipaddress.ip_network(s, strict=False)
    except Exception:
        return None

def _collect_used_from_allowed_str(allowed_str: str,
                                   net_v4: Optional[ipaddress._BaseNetwork],
                                   net_v6: Optional[ipaddress._BaseNetwork]) -> Tuple[Set[str], Set[str]]:
    used4: Set[str] = set()
    used6: Set[str] = set()
    for part in allowed_str.split(","):
        part = part.strip()
        if not part:
            continue
        net = _net(part)
        if not net:
            continue
        if net.version == 4 and net_v4 and net.subnet_of(net_v4):
            used4.add(str(net.network_address))
        elif net.version == 6 and net_v6 and net.subnet_of(net_v6):
            used6.add(str(net.network_address))
    return used4, used6

def _used_from_config() -> Tuple[Set[str], Set[str]]:
    text = _read_config_text()
    if not text:
        return set(), set()
    net_v4 = _net(ctx.SET.WG_PRIVATE_SUBNET_V4)
    net_v6 = _net(ctx.SET.WG_PRIVATE_SUBNET_V6)
    used4: Set[str] = set()
    used6: Set[str] = set()
    for m in re.finditer(r"(?mi)^\s*AllowedIPs\s*=\s*([^\n#;]+)", text):
        a4, a6 = _collect_used_from_allowed_str(m.group(1), net_v4, net_v6)
        used4 |= a4
        used6 |= a6
    return used4, used6

def _used_from_runtime() -> Tuple[Set[str], Set[str]]:
    net_v4 = _net(ctx.SET.WG_PRIVATE_SUBNET_V4)
    net_v6 = _net(ctx.SET.WG_PRIVATE_SUBNET_V6)
    used4: Set[str] = set()
    used6: Set[str] = set()
    res = run(["sudo", "wg", "show", "all", "dump"])
    if res.returncode != 0:
        return used4, used6
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 9:
            allowed = parts[4]
            a4, a6 = _collect_used_from_allowed_str(allowed, net_v4, net_v6)
            used4 |= a4
            used6 |= a6
    return used4, used6

def used_ips_v4() -> List[str]:
    c4, _ = _used_from_config()
    r4, _ = _used_from_runtime()
    return sorted(c4 | r4)

def used_ips_v6() -> List[str]:
    _, c6 = _used_from_config()
    _, r6 = _used_from_runtime()
    return sorted(c6 | r6)

def next_ip(subnet: str, used: List[str]) -> str:
    net = ipaddress.ip_network(subnet, strict=False)
    start = 2  # .1 / ::1 — адрес интерфейса сервера
    for host in net.hosts():
        idx = int(host) - int(net.network_address)
        if idx < start:
            continue
        if str(host) not in used:
            return str(host)
    raise RuntimeError(f"No free IP in subnet {subnet}")

# ---------- параметры сервера (живые) ----------

def server_public_key() -> str:
    if getattr(ctx.SET, "SERVER_PUBLIC_KEY", ""):
        return ctx.SET.SERVER_PUBLIC_KEY
    res = run(["sudo", "wg", "show", ctx.SET.WG_INTERFACE, "public-key"])
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return ""

def server_listen_port() -> int:
    # Берём фактический порт из рантайма, а не из .env
    res = run(["sudo", "wg", "show", ctx.SET.WG_INTERFACE, "listen-port"])
    if res.returncode == 0 and res.stdout.strip():
        try:
            return int(res.stdout.strip())
        except Exception:
            pass
    # запасной вариант: переменная .env
    return int(getattr(ctx.SET, "SERVER_PUBLIC_PORT", 51820))

# ---------- операции с peer ----------

def genkeys() -> Tuple[str, str]:
    r1 = run(["wg", "genkey"], check=True)
    priv = r1.stdout.strip()
    r2 = subprocess.run(["wg", "pubkey"], input=priv, text=True, capture_output=True)
    pub = r2.stdout.strip()
    return priv, pub

def gen_psk() -> str:
    r = run(["wg", "genpsk"], check=True)
    return r.stdout.strip()

def add_peer(public_key: str, allowed_ips: str, preshared_key: Optional[str] = None) -> None:
    """
    Добавляет peer в рантайм и сразу сохраняет конфиг:
    wg set ... ; wg-quick save wg0
    """
    cmd = ["sudo", "wg", "set", ctx.SET.WG_INTERFACE, "peer", public_key, "allowed-ips", allowed_ips]
    if preshared_key:
        cmd += ["preshared-key", "/dev/stdin"]
        res = subprocess.run(cmd, input=preshared_key, text=True, capture_output=True)
    else:
        res = run(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"wg set failed: {res.stdout}\n{res.stderr}")
    res2 = run(["sudo", "wg-quick", "save", ctx.SET.WG_INTERFACE])
    if res2.returncode != 0:
        raise RuntimeError(f"wg-quick save failed: {res2.stdout}\n{res2.stderr}")

def remove_peer(public_key: str) -> None:
    res = run(["sudo", "wg", "set", ctx.SET.WG_INTERFACE, "peer", public_key, "remove"])
    if res.returncode != 0:
        raise RuntimeError(f"wg remove failed: {res.stdout}\n{res.stderr}")
    run(["sudo", "wg-quick", "save", ctx.SET.WG_INTERFACE])

# ---------- генерация клиентского .conf (от сервера) ----------

def build_client_conf(private_key: str, address_v4: Optional[str], address_v6: Optional[str]) -> str:
    """
    Шаблон строго как просили:

    [Interface]
    PrivateKey = <priv>
    Address = <v4>/32, <v6>/128
    MTU = 1420
    DNS = 1.1.1.1

    [Peer]
    PublicKey = <server_pub>
    AllowedIPs = 0.0.0.0/0
    Endpoint = <endpoint>:<port>
    PersistentKeepalive = 21
    """
    addrs = []
    if address_v4:
        addrs.append(f"{address_v4}/32")
    if address_v6:
        addrs.append(f"{address_v6}/128")
    addresses = ", ".join(addrs) if addrs else ""

    dns_source = (getattr(ctx.SET, "WG_DNS", "") or "").split(",")[0].strip()
    dns = dns_source or "1.1.1.1"

    mtu = int(getattr(ctx.SET, "WG_MTU", 1420) or 1420)
    keepalive = int(getattr(ctx.SET, "WG_KEEPALIVE", 21) or 21)

    endpoint_host = getattr(ctx.SET, "SERVER_PUBLIC_ENDPOINT", "")
    endpoint_port = server_listen_port()  # живой порт
    pubkey = server_public_key()

    conf = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {addresses}",
        f"MTU = {mtu}",
        f"DNS = {dns}",
        "",
        "[Peer]",
        f"PublicKey = {pubkey}",
        "AllowedIPs = 0.0.0.0/0",
        f"Endpoint = {endpoint_host}:{endpoint_port}",
        f"PersistentKeepalive = {keepalive}",
        ""
    ]
    return "\n".join(conf)

def save_client_conf_to_file(username: str, dev_name: str, conf_text: str) -> str:
    os.makedirs(ctx.SET.CONFIGS_DIR, exist_ok=True)
    import re
    safe_user = re.sub(r"[^a-zA-Z0-9_.-]+", "_", username or "user")
    safe_dev = re.sub(r"[^a-zA-Z0-9_.-]+", "_", dev_name or "device")
    filename = f"{safe_user}_{safe_dev}.conf"
    path = os.path.join(ctx.SET.CONFIGS_DIR, filename)
    with open(path, "w") as f:
        f.write(conf_text)
    return path

# ---------- утилита для синка (для вебхука) ----------

def runtime_peers() -> List[Dict[str, Optional[str]]]:
    """
    Разбор `wg show <iface> dump`.
    Возвращает [{'public_key', 'address_v4', 'address_v6'}].
    """
    v4net = _net(ctx.SET.WG_PRIVATE_SUBNET_V4)
    v6net = _net(ctx.SET.WG_PRIVATE_SUBNET_V6)
    res = run(["sudo", "wg", "show", ctx.SET.WG_INTERFACE, "dump"])
    if res.returncode != 0 or not res.stdout:
        return []
    lines = res.stdout.splitlines()
    out: List[Dict[str, Optional[str]]] = []
    # первая строка — про интерфейс; дальше идут пиры
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        pub = cols[0]
        allowed = cols[3]  # "ip1,ip2"
        a4 = None
        a6 = None
        for item in allowed.split(","):
            item = item.strip()
            net = _net(item)
            if not net:
                continue
            if v4net and net.version == 4 and net.subnet_of(v4net):
                a4 = str(net.network_address)
            if v6net and net.version == 6 and net.subnet_of(v6net):
                a6 = str(net.network_address)
        out.append({"public_key": pub, "address_v4": a4, "address_v6": a6})
    return out
