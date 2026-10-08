"""Wake-on-LAN: risveglia il PC inviando un magic packet.

Limite intrinseco del protocollo, non di questa implementazione: il pacchetto
deve arrivare via broadcast sulla stessa rete locale della scheda di rete.
Se il PC e' completamente spento non c'e' nulla in ascolto per ricevere
comandi via WebSocket: e' per questo che il "magic packet" lo invia il
telefono stesso in broadcast sulla LAN, non il nostro server (che a PC spento
non sarebbe comunque in esecuzione). Da fuori casa non e' possibile senza un
secondo dispositivo sempre acceso sulla LAN che faccia da ponte.
"""
import socket


def build_magic_packet(mac_address):
    mac_bytes = bytes.fromhex(mac_address.replace(":", "").replace("-", ""))
    if len(mac_bytes) != 6:
        raise ValueError(f"Indirizzo MAC non valido: {mac_address}")
    return b"\xff" * 6 + mac_bytes * 16


def send_magic_packet(mac_address, broadcast_ip="255.255.255.255", port=9):
    packet = build_magic_packet(mac_address)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.sendto(packet, (broadcast_ip, port))
    finally:
        sock.close()


def get_local_mac(interface_hint=None):
    """Ritorna l'indirizzo MAC della scheda di rete attiva, per mostrarlo una
    volta sola all'utente (che poi lo salva nel telefono)."""
    import subprocess

    result = subprocess.run(
        ["getmac", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=5
    )
    lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    if not lines:
        return None
    first = lines[0].split(",")[0].strip('"')
    return first
