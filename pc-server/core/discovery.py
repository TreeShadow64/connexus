"""Ricerca del PC da parte del telefono sulla rete locale.

Il modem assegna ogni tanto un indirizzo nuovo al PC e il telefono, che ha salvato quello vecchio,
risultava "offline". Il telefono ora manda un broadcast UDP e questo modulo risponde con nome,
indirizzo e porta. La risposta NON contiene il token: chi risponde si fa riconoscere solo
dall'autenticazione normale sulla porta WebSocket."""
import json
import logging
import platform
import socket
import threading

from core import pairing

log = logging.getLogger("hub-server")

PORT = 8774
MAGIC = b"CONNEXUS?"
WS_PORT = 8765


def _ip_towards(peer_ip):
    """Indirizzo del PC sulla stessa rete di chi ha chiesto."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer_ip, 9))
        return probe.getsockname()[0]
    except OSError:
        return pairing.lan_ip()
    finally:
        probe.close()


def build_reply(peer_ip):
    return json.dumps({
        "app": "connexus",
        "name": platform.node() or "PC",
        "ip": _ip_towards(peer_ip),
        "port": WS_PORT,
    }).encode("utf-8")


def _serve(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    log.info(f"Ricerca del PC dal telefono in ascolto su UDP {port}")
    while True:
        try:
            data, addr = sock.recvfrom(256)
            if data.strip() == MAGIC:
                sock.sendto(build_reply(addr[0]), addr)
        except OSError:
            return
        except Exception as e:
            log.warning(f"Ricerca del PC: {type(e).__name__}: {e}")


def start(port=PORT):
    threading.Thread(target=_serve, args=(port,), name="discovery", daemon=True).start()
