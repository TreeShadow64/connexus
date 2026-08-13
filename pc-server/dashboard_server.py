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
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import file_browser
import ftp_client
from paths import bundle_dir

log = logging.getLogger("hub-server")

DASHBOARD_DIR = bundle_dir() / "dashboard"
DASHBOARD_PORT = 8771
TRANSFER_LOG_MAX = 200

_status_provider = None
_action_handler = None

# Solo in memoria: e' un'attivita' recente, non un registro permanente, e vive
# comunque solo su questo PC (dashboard locale, non sincronizzata altrove).
transfer_log = []


def _record_transfer(direction, filename, share_name, ok, error=None):
    transfer_log.append({
        "timestamp": time.time(),
        "direction": direction,  # "download" (dal telefono al PC) o "upload" (dal PC al telefono)
        "filename": filename,
        "share": share_name,
        "ok": ok,
        "error": error,
    })
    del transfer_log[:-TRANSFER_LOG_MAX]


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
            if self.path.startswith("/ftp/activity"):
                _send_json(self, {"ok": True, "entries": list(reversed(transfer_log))})
                return
            if self.path.startswith("/local/list"):
                self._local_list()
                return
            super().do_GET()

        def do_POST(self):
            if self.path == "/action":
                self._handle_action()
                return
            if self.path == "/local/upload-to-remote":
                self._upload_to_remote()
                return
            if self.path == "/local/download-from-remote":
                self._download_from_remote()
                return
            self.send_error(404)

        def _json_body(self):
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def _handle_action(self):
            try:
                body = self._json_body()
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

        def _local_list(self):
            query = parse_qs(urlparse(self.path).query)
            path = query.get("path", [""])[0]
            try:
                entries = file_browser.list_directory(path)
                _send_json(self, {"ok": True, "entries": entries, "parent": file_browser.parent_of(path)})
            except file_browser.FileBrowserError as e:
                _send_json(self, {"ok": False, "error": str(e)}, status=400)

        def _upload_to_remote(self):
            """Pannello FileZilla: carica un file gia' presente sul PC verso
            la condivisione del telefono, senza passare dal browser (il
            dashboard gira gia' sul PC, il file e' gia' li')."""
            body = None
            try:
                body = self._json_body()
                local_path = Path(body["local_path"])
                filename = local_path.name
                remote_dir = body.get("remote_path", "")
                remote_target = f"{remote_dir}/{filename}" if remote_dir else filename
                with open(local_path, "rb") as f:
                    ftp_client.upload_from_stream(body["ip"], int(body["port"]), body["password"], remote_target, f)
                _record_transfer("upload", filename, body.get("share_name", "?"), True)
                _send_json(self, {"ok": True})
            except Exception as e:
                _record_transfer("upload", (body or {}).get("local_path", "?"), "?", False, str(e))
                _send_json(self, {"ok": False, "error": str(e)}, status=502)

        def _download_from_remote(self):
            """Pannello FileZilla: scarica un file dalla condivisione del
            telefono direttamente nella cartella locale scelta nel pannello
            di sinistra, senza passare dal download del browser."""
            body = None
            try:
                body = self._json_body()
                remote_path = body["remote_path"]
                filename = remote_path.rsplit("/", 1)[-1]
                dest = Path(body["local_dir"]) / filename
                with open(dest, "wb") as f:
                    ftp_client.download_to_stream(body["ip"], int(body["port"]), body["password"], remote_path, f)
                _record_transfer("download", filename, body.get("share_name", "?"), True)
                _send_json(self, {"ok": True})
            except Exception as e:
                _record_transfer("download", (body or {}).get("remote_path", "?"), "?", False, str(e))
                _send_json(self, {"ok": False, "error": str(e)}, status=502)

    server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Dashboard PC in ascolto su http://127.0.0.1:{DASHBOARD_PORT}")
