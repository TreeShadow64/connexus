"""Dashboard PC locale: serve l'interfaccia HTML/CSS/JS statica della cartella
'dashboard/' e un endpoint JSON di stato che la pagina interroga a intervalli
regolari (niente WebSocket per ora: i dati cambiano lentamente — spettatori,
condivisioni, stato dei servizi — un polling semplice basta ed e' molto meno
codice da mantenere di un canale push dedicato).

Ascolta solo su 127.0.0.1: e' un pannello di amministrazione senza login,
non deve essere raggiungibile dalla rete locale ne' da fuori casa.
"""
import json
import logging
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from paths import bundle_dir

log = logging.getLogger("hub-server")

DASHBOARD_DIR = bundle_dir() / "dashboard"
DASHBOARD_PORT = 8771

_status_provider = None
_action_handler = None


def _send_json(handler, obj, status=200):
    body = json.dumps(obj).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def start(status_provider, action_handler):
    """status_provider: funzione sincrona senza argomenti che ritorna lo
    stato corrente. action_handler(action: str, payload: dict) -> dict:
    esegue un comando lanciato da un pulsante della dashboard (fermare uno
    stream, gestire un dispositivo di 'Trova dispositivo'...)."""
    global _status_provider, _action_handler
    _status_provider = status_provider
    _action_handler = action_handler

    class DashboardHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

        def log_message(self, format, *args):
            pass  # la console e' gia' piena dei log del server principale

        def do_GET(self):
            if self.path == "/status.json":
                _send_json(self, _status_provider())
                return
            super().do_GET()

        def do_POST(self):
            if self.path != "/action":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                _send_json(self, {"ok": False, "error": "corpo non valido"}, status=400)
                return
            action = body.get("action")
            payload = body.get("payload") or {}
            try:
                result = _action_handler(action, payload)
            except Exception as e:
                log.warning(f"Dashboard: azione '{action}' fallita: {e}")
                _send_json(self, {"ok": False, "error": str(e)}, status=500)
                return
            _send_json(self, result)

    server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Dashboard PC in ascolto su http://127.0.0.1:{DASHBOARD_PORT}")
