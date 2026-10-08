"""Ricerca delle TV LG webOS sulla rete locale (SSDP).

Le TV rispondono a una M-SEARCH per il servizio "webos-second-screen". La
richiesta parte da ogni interfaccia IPv4 del PC (una VPN o Tailscale potrebbero
altrimenti catturare l'interfaccia predefinita). Il nome mostrato all'utente
viene letto dalla descrizione UPnP della TV; l'UUID serve a riconoscerla se in
seguito il router le assegna un altro indirizzo IP.
"""
import ipaddress
import re
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGET = "urn:lge-com:service:webos-second-screen:1"


def _local_ipv4_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    try:
        # indirizzo dell'interfaccia usata per uscire in rete (nessun pacchetto inviato)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        addresses.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return [a for a in addresses
            if not a.startswith("127.") and not a.startswith("169.254.")]


def _header(text, name):
    match = re.search(rf"(?im)^{name}:\s*(.+?)\s*$", text)
    return match.group(1) if match else ""


def _describe(location):
    """Nome e modello dalla descrizione UPnP; stringhe vuote se non leggibile."""
    try:
        xml = urllib.request.urlopen(location, timeout=2).read().decode("utf-8", "ignore")
    except (OSError, ValueError):
        return "", ""
    name = re.search(r"<friendlyName>(.*?)</friendlyName>", xml, re.S)
    model = re.search(r"<modelName>(.*?)</modelName>", xml, re.S)
    return (name.group(1).strip() if name else ""), (model.group(1).strip() if model else "")


def discover(timeout=4.0):
    """Ritorna [{ip, uuid, name, model}] ordinato per nome."""
    request = (
        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\nMX: 2\r\n'
        f"ST: {SEARCH_TARGET}\r\n\r\n"
    ).encode()

    sockets = []
    for local_ip in _local_ipv4_addresses():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.bind((local_ip, 0))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.settimeout(0.3)
            sock.sendto(request, SSDP_ADDR)
            sockets.append(sock)
        except OSError:
            continue

    found = {}
    start = time.monotonic()
    deadline = start + timeout
    resend_at = [start + timeout * 0.3, start + timeout * 0.6]
    while time.monotonic() < deadline and sockets:
        # le TV rispondono a volte solo al secondo o terzo invio
        if resend_at and time.monotonic() >= resend_at[0]:
            resend_at.pop(0)
            for sock in sockets:
                try:
                    sock.sendto(request, SSDP_ADDR)
                except OSError:
                    pass
        for sock in sockets:
            try:
                data, addr = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            text = data.decode("utf-8", "ignore")
            if SEARCH_TARGET not in text:
                continue
            usn = _header(text, "usn")
            uuid = usn.split("::")[0].replace("uuid:", "") if usn else ""
            found[addr[0]] = {"ip": addr[0], "uuid": uuid, "location": _header(text, "location")}
    for sock in sockets:
        sock.close()

    def finish(entry):
        name, model = _describe(entry["location"]) if entry["location"] else ("", "")
        return {"ip": entry["ip"], "uuid": entry["uuid"],
                "name": name or "TV LG", "model": model}

    with ThreadPoolExecutor(max_workers=8) as pool:
        tvs = list(pool.map(finish, found.values()))
    return sorted(tvs, key=lambda tv: (tv["name"].lower(), tv["ip"]))


def is_valid_tv_ip(value):
    """Solo indirizzi IPv4 della rete locale: l'app non deve poter puntare il
    server verso un indirizzo qualsiasi di Internet."""
    try:
        ip = ipaddress.IPv4Address(str(value).strip())
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_link_local
