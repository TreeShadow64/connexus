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
import time
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


# Su Windows una connessione rifiutata verso localhost impiega ~2 secondi a
# fallire: se il servizio e' installato ma fermo, ogni movimento del mouse
# costerebbe 2s. Dopo un fallimento si salta il servizio per qualche secondo
# e si usa subito pynput.
_RETRY_AFTER = 5.0
_CONNECT_TIMEOUT = 0.4
_unavailable_until = 0.0


def send_command(data, timeout=2):
    """Inoltra un comando (stesso formato usato da handle_command in server.py)
    al servizio elevato. Solleva ServiceUnavailable se non e' raggiungibile."""
    global _unavailable_until
    token = _read_token()
    if not token:
        raise ServiceUnavailable("Servizio non installato")
    if time.monotonic() < _unavailable_until:
        raise ServiceUnavailable("Servizio non raggiungibile (ritento a breve)")

    try:
        sock = socket.create_connection(("127.0.0.1", SERVICE_PORT), timeout=_CONNECT_TIMEOUT)
    except OSError as e:
        _unavailable_until = time.monotonic() + _RETRY_AFTER
        raise ServiceUnavailable(str(e))

    try:
        with sock:
            sock.settimeout(timeout)
            sock_file = sock.makefile("rwb")
            sock_file.write((json.dumps({"token": token}) + "\n").encode("utf-8"))
            sock_file.flush()
            auth_reply = json.loads(sock_file.readline().decode("utf-8"))
            if not auth_reply.get("ok"):
                raise ServiceUnavailable("Token del servizio non valido")

            sock_file.write((json.dumps(data) + "\n").encode("utf-8"))
            sock_file.flush()
            sock_file.readline()
    except ServiceUnavailable:
        _unavailable_until = time.monotonic() + _RETRY_AFTER
        raise
    except (ValueError, OSError, TimeoutError) as e:
        # ValueError copre una risposta vuota/non JSON (porta occupata da altro):
        # meglio ricadere su pynput che far fallire il comando.
        _unavailable_until = time.monotonic() + _RETRY_AFTER
        raise ServiceUnavailable(str(e))
