"""Collegamento del telefono con un codice QR: invece di scrivere a mano IP e
token, l'app inquadra il codice mostrato dalla dashboard del PC.

Il codice contiene il token di accesso: va mostrato solo sullo schermo del PC
dell'utente (la dashboard e' raggiungibile solo da questo computer) e mai
salvato su file o scritto nei log."""
import ipaddress
import platform
import socket
from urllib.parse import urlencode

from core import auth


def _candidates():
    found = []
    try:
        # indirizzo dell'interfaccia con cui il PC esce in rete (non invia pacchetti)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    return [ip for ip in dict.fromkeys(found) if not ip.startswith(("127.", "169.254."))]


def lan_ip():
    """Indirizzo del PC sulla rete di casa (192.168.x, 10.x, 172.16-31.x)."""
    candidates = _candidates()
    for ip in candidates:
        if ipaddress.ip_address(ip).is_private and not ip.startswith("100."):
            return ip
    return candidates[0] if candidates else ""


def payload():
    return "connexus://pair?" + urlencode({
        "ip": lan_ip(),
        "token": auth.TOKEN,
        "name": platform.node() or "PC",
    })


def qr_svg():
    """SVG inline del codice (scuro su bianco, con margine: serve per essere letto)."""
    import segno
    return segno.make(payload(), error="m").svg_inline(
        scale=6, border=3, dark="#000000", light="#ffffff")
