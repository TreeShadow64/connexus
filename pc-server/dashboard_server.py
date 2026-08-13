"""Dashboard PC locale: serve l'interfaccia HTML/CSS/JS statica della cartella
'dashboard/' e un endpoint JSON di stato che la pagina interroga a intervalli
regolari (niente WebSocket per ora: i dati cambiano lentamente — spettatori,
condivisioni, stato dei servizi — un polling semplice basta ed e' molto meno
codice da mantenere di un canale push dedicato).

Ascolta solo su 127.0.0.1: e' un pannello di amministrazione senza login,
non deve essere raggiungibile dalla rete locale ne' da fuori casa.
"""
import io
import json
import logging
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import ftp_client
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
            if self.path.startswith("/ftp/list"):
                self._ftp_list()
                return
            if self.path.startswith("/ftp/download"):
                self._ftp_download()
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

        def do_PUT(self):
            if self.path.startswith("/ftp/upload"):
                self._ftp_upload()
                return
            self.send_error(405, "Metodo non supportato")

        def _ftp_params(self):
            query = parse_qs(urlparse(self.path).query)
            return {
                "host": query.get("ip", [""])[0],
                "port": int(query.get("port", ["2121"])[0]),
                "password": query.get("password", [""])[0],
                "path": query.get("path", [""])[0],
            }

        def _ftp_list(self):
            p = self._ftp_params()
            try:
                entries = ftp_client.list_dir(p["host"], p["port"], p["password"], p["path"])
                _send_json(self, {"ok": True, "entries": entries})
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)}, status=502)

        def _ftp_download(self):
            p = self._ftp_params()
            filename = p["path"].rsplit("/", 1)[-1] or "file"
            buffer = io.BytesIO()
            try:
                ftp_client.download_to_stream(p["host"], p["port"], p["password"], p["path"], buffer)
            except Exception as e:
                self.send_error(502, str(e))
                return
            data = buffer.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def _ftp_upload(self):
            p = self._ftp_params()
            length = int(self.headers.get("Content-Length", 0))
            buffer = io.BytesIO(self.rfile.read(length))
            try:
                ftp_client.upload_from_stream(p["host"], p["port"], p["password"], p["path"], buffer)
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)}, status=502)
                return
            _send_json(self, {"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Dashboard PC in ascolto su http://127.0.0.1:{DASHBOARD_PORT}")
