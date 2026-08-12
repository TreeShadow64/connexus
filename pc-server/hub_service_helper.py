"""Helper lanciato dal servizio DENTRO la sessione interattiva (non in
Sessione 0), con privilegi SYSTEM. E' questo processo, non il servizio
stesso, che puo' effettivamente chiamare OpenInputDesktop() con successo:
l'isolamento delle sessioni di Windows impedisce a un servizio in Sessione 0
di raggiungere qualsiasi desktop visibile, anche con privilegi SYSTEM.

Non e' un servizio Windows: e' un processo normale, avviato da hub_service.py
tramite CreateProcessAsUser con un token SYSTEM la cui sessione e' stata
riassegnata a quella interattiva corrente (la stessa tecnica usata da
strumenti come PsExec con "-s -i").
"""
import json
import secrets
import socketserver
from pathlib import Path

import input_inject

SERVICE_PORT = 8770
TOKEN_DIR = Path(r"C:\ProgramData\HubPC")
TOKEN_PATH = TOKEN_DIR / "service_token.txt"


def _load_or_create_token():
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists():
        token = TOKEN_PATH.read_text().strip()
        if token:
            return token
    token = secrets.token_hex(16)
    TOKEN_PATH.write_text(token)
    return token


class InputHandler(socketserver.StreamRequestHandler):
    def handle(self):
        token = self.server.shared_token
        authenticated = False
        while True:
            line = self.rfile.readline()
            if not line:
                return
            try:
                data = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            if not authenticated:
                if data.get("token") == token:
                    authenticated = True
                    self.wfile.write(b'{"ok":true}\n')
                else:
                    self.wfile.write(b'{"ok":false}\n')
                    return
                continue

            cmd = data.get("type")
            ok = False
            if cmd == "move":
                ok = input_inject.move_mouse(int(data.get("dx", 0)), int(data.get("dy", 0)))
            elif cmd == "click":
                ok = input_inject.click_mouse(data.get("button", "left"))
            elif cmd == "scroll":
                ok = input_inject.scroll_mouse(data.get("amount", 0))
            elif cmd == "text":
                ok = input_inject.type_text(data.get("value", ""))
            elif cmd == "key":
                ok = input_inject.press_key(data.get("value", ""))
            elif cmd == "uac_accept":
                ok = input_inject.accept_uac()
            self.wfile.write((json.dumps({"ok": ok}) + "\n").encode())


class InputServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    token = _load_or_create_token()
    server = InputServer(("127.0.0.1", SERVICE_PORT), InputHandler)
    server.shared_token = token
    print(f"helper attivo su 127.0.0.1:{SERVICE_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
