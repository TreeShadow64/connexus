"""Client per il servizio Windows elevato (hub_service.py).

Se il servizio e' installato e in esecuzione, i comandi mouse/tastiera
passano da qui e funzionano anche sulle finestre UAC e sulla schermata di
blocco. Se il servizio non e' installato, ogni chiamata solleva
ServiceUnavailable: server.py intercetta l'eccezione e ricade su pynput
(comportamento identico a prima di questa fase), quindi l'app funziona
comunque anche senza installare il servizio.
"""
import json
import socket
from pathlib import Path

SERVICE_PORT = 8770
TOKEN_PATH = Path(r"C:\ProgramData\HubPC\service_token.txt")


class ServiceUnavailable(Exception):
    pass


def _read_token():
    try:
        return TOKEN_PATH.read_text().strip()
    except OSError:
        return None


def is_installed():
    return TOKEN_PATH.exists()


def send_command(data, timeout=2):
    """Inoltra un comando (stesso formato usato da handle_command in server.py)
    al servizio elevato. Solleva ServiceUnavailable se non e' raggiungibile."""
    token = _read_token()
    if not token:
        raise ServiceUnavailable("Servizio non installato")

    try:
        with socket.create_connection(("127.0.0.1", SERVICE_PORT), timeout=timeout) as sock:
            sock_file = sock.makefile("rwb")
            sock_file.write((json.dumps({"token": token}) + "\n").encode("utf-8"))
            sock_file.flush()
            auth_reply = json.loads(sock_file.readline().decode("utf-8"))
            if not auth_reply.get("ok"):
                raise ServiceUnavailable("Token del servizio non valido")

            sock_file.write((json.dumps(data) + "\n").encode("utf-8"))
            sock_file.flush()
            sock_file.readline()
    except (ConnectionRefusedError, OSError, TimeoutError) as e:
        raise ServiceUnavailable(str(e))
