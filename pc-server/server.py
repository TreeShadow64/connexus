import asyncio
import json
import logging
import mimetypes
import platform
import queue
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
import pyvirtualcam
import websockets
from websockets.asyncio.server import serve
from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key
from pynput.mouse import Button
from pynput.mouse import Controller as MouseController

import psutil

import auth
import dashboard_server
import dlna_cast
import file_browser
import firebase_relay
import service_client
import wol
import mouse_guard
import screen_stream
import webos_remote
from paths import app_dir

HOST = "0.0.0.0"
PORT = 8765
MEDIA_HTTP_PORT = 8766
SCREEN_PORT = 8767
PROJECTOR_PORT = 8768
VIRTUALCAM_PORT = 8769
UVCCAM_PORT = 8770

PARSEC_EXE = r"C:\Program Files\Parsec\parsecd.exe"
PROTONVPN_EXE = r"C:\Program Files\Proton\VPN\ProtonVPN.Launcher.exe"
PARSEC_CONFIG_PATH = app_dir() / "parsec_config.json"
WEBOS_CONFIG_PATH = app_dir() / "webos_config.json"
MEDIA_DIR = app_dir() / "media"
MEDIA_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("hub-server")

mouse = MouseController()
keyboard = KeyboardController()

SPECIAL_KEYS = {
    "enter": Key.enter,
    "backspace": Key.backspace,
    "space": Key.space,
    "volume_up": Key.media_volume_up,
    "volume_down": Key.media_volume_down,
    "volume_mute": Key.media_volume_mute,
}

last_renderers = []

SERVER_START_TIME = time.time()

# Connessioni attive sul canale principale (mouse/tastiera/TV/file...): usato
# solo dalla dashboard PC per mostrare "quanti dispositivi sono collegati ora".
connected_clients = set()

# Registro delle cartelle condivise attualmente attive: chiave = id della
# connessione websocket che condivide, cosi' si rimuove da sola alla
# disconnessione. Serve solo a rendere visibile agli altri device chi sta
# condividendo cosa in questo momento (non trasporta i file: quelli passano
# per l'FTP del telefono).
shared_devices = {}
shared_devices_lock = threading.Lock()

# Stato della webcam UVC visto dalla dashboard PC: e' l'unico dei tre stream
# (schermo/projector/virtualcam) il cui oggetto vive per la durata di una
# singola connessione invece che come singleton di modulo, quindi serve un
# posto separato dove renderlo visibile dall'esterno.
uvccam_status = {"active": False, "device": None}
active_uvccam_streamer = None

INPUT_COMMANDS = {"move", "click", "text", "key", "scroll"}


def handle_input_command(data):
    """Mouse/tastiera: prova prima il servizio elevato (funziona anche su
    finestre UAC e schermata di blocco), poi ricade su pynput se il
    servizio non e' installato o non risponde."""
    try:
        service_client.send_command(data)
        return
    except service_client.ServiceUnavailable:
        pass

    cmd = data.get("type")
    if cmd == "move":
        mouse.move(data.get("dx", 0), data.get("dy", 0))
    elif cmd == "click":
        buttons = {
            "right": Button.right, "middle": Button.middle,
            "back": Button.x1, "forward": Button.x2,
        }
        mouse.click(buttons.get(data.get("button"), Button.left))
    elif cmd == "scroll":
        mouse.scroll(0, data.get("amount", 0))
    elif cmd == "text":
        keyboard.type(data.get("value", ""))
    elif cmd == "key":
        key = SPECIAL_KEYS.get(data.get("value"))
        if key is not None:
            keyboard.press(key)
            keyboard.release(key)


_SENSITIVE_LOG_KEYS = {"custom_token", "token"}


def _redact_for_log(data):
    """I comandi via WebSocket includono token (auth, custom_token per
    'Trova dispositivo'): non vanno mai scritti per intero nei log."""
    if not isinstance(data, dict):
        return data
    return {k: ("***" if k in _SENSITIVE_LOG_KEYS else v) for k, v in data.items()}


def handle_command(data, conn_key=None, peer_ip=None):
    """Esegue un comando ricevuto dal telefono. Ritorna una risposta opzionale da rimandare al client."""
    cmd = data.get("type")
    if cmd in INPUT_COMMANDS:
        handle_input_command(data)
    elif cmd == "share_status":
        with shared_devices_lock:
            if data.get("sharing"):
                shared_devices[conn_key] = {
                    "name": data.get("name", "dispositivo"),
                    "folder": data.get("folder", ""),
                    "ip": peer_ip,
                    "ftp_port": data.get("ftp_port", 0),
                }
            else:
                shared_devices.pop(conn_key, None)
        return {"type": "share_ok"}
    elif cmd == "list_shares":
        with shared_devices_lock:
            others = [v for k, v in shared_devices.items() if k != conn_key]
        return {"type": "share_list", "devices": others}
    elif cmd == "list_processes":
        return {"type": "process_list", "processes": list_processes()}
    elif cmd == "kill_process":
        return kill_process(data.get("pid"))
    elif cmd == "uac_accept":
        try:
            service_client.send_command(data)
            return {"type": "tv_ok", "message": "Comando UAC inviato"}
        except service_client.ServiceUnavailable as e:
            return {"type": "tv_error", "message": f"Servizio non disponibile: {e}"}
    elif cmd == "launch_parsec":
        launch_parsec()
    elif cmd == "launch_vpn":
        ok = launch_vpn()
        return {"type": "vpn_ok", "message": "ProtonVPN avviato"} if ok else \
               {"type": "vpn_error", "message": "ProtonVPN non trovato sul PC"}
    elif cmd == "system_power":
        return system_power(data.get("action", ""))
    elif cmd == "get_mac_address":
        mac = get_mac_address()
        return {"type": "mac_address", "mac": mac} if mac else \
               {"type": "vpn_error", "message": "Impossibile leggere l'indirizzo MAC"}
    elif cmd == "cast_discover":
        return cast_discover()
    elif cmd == "cast_play":
        return cast_play(data.get("file", ""), data.get("renderer_index", 0))
    elif cmd == "service_status":
        installed = service_client.is_installed()
        return {
            "type": "service_status_result",
            "installed": installed,
            "message": (
                "Servizio UAC/lock screen attivo" if installed
                else "Servizio non installato: mouse/tastiera funzionano solo nel desktop normale"
            ),
        }
    elif cmd == "fs_list":
        path = data.get("path", "")
        try:
            entries = file_browser.list_directory(path)
            return {
                "type": "fs_list_result",
                "path": path,
                "parent": file_browser.parent_of(path),
                "entries": entries,
            }
        except file_browser.FileBrowserError as e:
            return {"type": "fs_error", "message": str(e)}
    elif cmd == "mouse_lock":
        locked = mouse_guard.guard.set_locked(bool(data.get("enabled")))
        message = (
            "Mouse bloccato sullo schermo principale" if locked
            else "Mouse libero di passare tra gli schermi"
        )
        return {"type": "mouse_lock_state", "locked": locked, "message": message}
    elif cmd == "register_account":
        return firebase_relay.register_account(data.get("custom_token", ""))
    return None


def _load_webos_config():
    return json.loads(WEBOS_CONFIG_PATH.read_text())


def _save_webos_client_key(client_key):
    config = _load_webos_config()
    config["client_key"] = client_key
    WEBOS_CONFIG_PATH.write_text(json.dumps(config, indent=4))


async def handle_async_command(data):
    """Comandi che richiedono I/O di rete asincrono (telecomando TV via webOS)."""
    cmd = data.get("type")
    config = _load_webos_config()
    tv_ip = config.get("tv_ip", "").strip()

    if not tv_ip:
        return {"type": "tv_error", "message": "Nessun IP TV configurato in webos_config.json"}

    if cmd == "tv_pair":
        try:
            client_key = await webos_remote.pair(tv_ip, config.get("client_key") or None)
            _save_webos_client_key(client_key)
            log.info(f"TV {tv_ip} abbinata con successo")
            return {"type": "tv_ok", "message": "TV abbinata con successo"}
        except Exception as e:
            log.warning(f"Abbinamento TV fallito: {e}")
            return {"type": "tv_error", "message": f"Abbinamento fallito: {e}"}

    elif cmd == "tv_command":
        action = data.get("action", "")
        uri = webos_remote.BUTTON_URIS.get(action)
        if not uri:
            return {"type": "tv_error", "message": f"Comando sconosciuto: {action}"}
        try:
            await webos_remote.send_request(tv_ip, config.get("client_key"), uri)
            return {"type": "tv_ok", "message": f"Comando inviato: {action}"}
        except Exception as e:
            log.warning(f"Comando TV fallito ({action}): {e}")
            return {"type": "tv_error", "message": f"Comando fallito: {e}"}

    elif cmd == "tv_dpad":
        direction = data.get("direction", "")
        button_name = webos_remote.DPAD_BUTTON_NAMES.get(direction)
        if not button_name:
            return {"type": "tv_error", "message": f"Direzione sconosciuta: {direction}"}
        try:
            await webos_remote.send_button(tv_ip, config.get("client_key"), button_name)
            return {"type": "tv_ok", "message": f"D-pad: {direction}"}
        except Exception as e:
            log.warning(f"D-pad TV fallito ({direction}): {e}")
            return {"type": "tv_error", "message": f"D-pad fallito: {e}"}

    elif cmd == "tv_button":
        name = data.get("name", "")
        button_name = webos_remote.REMOTE_BUTTON_NAMES.get(name)
        if not button_name:
            return {"type": "tv_error", "message": f"Tasto sconosciuto: {name}"}
        try:
            await webos_remote.send_button(tv_ip, config.get("client_key"), button_name)
            return {"type": "tv_ok", "message": f"Tasto: {name}"}
        except Exception as e:
            log.warning(f"Tasto TV fallito ({name}): {e}")
            return {"type": "tv_error", "message": f"Tasto fallito: {e}"}

    elif cmd == "tv_list_apps":
        try:
            apps = await webos_remote.list_apps(tv_ip, config.get("client_key"))
            return {"type": "tv_apps", "apps": apps}
        except Exception as e:
            log.warning(f"Elenco app TV fallito: {e}")
            return {"type": "tv_error", "message": f"Elenco app fallito: {e}"}

    elif cmd == "tv_launch_app":
        app_id = data.get("app_id", "")
        try:
            await webos_remote.launch_app(tv_ip, config.get("client_key"), app_id)
            return {"type": "tv_ok", "message": f"Avviata app: {app_id}"}
        except Exception as e:
            log.warning(f"Avvio app TV fallito ({app_id}): {e}")
            return {"type": "tv_error", "message": f"Avvio app fallito: {e}"}

    elif cmd == "tv_list_inputs":
        try:
            inputs = await webos_remote.list_inputs(tv_ip, config.get("client_key"))
            return {"type": "tv_inputs", "inputs": inputs}
        except Exception as e:
            log.warning(f"Elenco input TV fallito: {e}")
            return {"type": "tv_error", "message": f"Elenco input fallito: {e}"}

    elif cmd == "tv_switch_input":
        input_id = data.get("input_id", "")
        try:
            await webos_remote.switch_input(tv_ip, config.get("client_key"), input_id)
            return {"type": "tv_ok", "message": f"Sorgente: {input_id}"}
        except Exception as e:
            log.warning(f"Cambio input TV fallito ({input_id}): {e}")
            return {"type": "tv_error", "message": f"Cambio input fallito: {e}"}

    return None


ASYNC_COMMANDS = {
    "tv_pair", "tv_command", "tv_dpad", "tv_button",
    "tv_list_apps", "tv_launch_app", "tv_list_inputs", "tv_switch_input",
}


def launch_parsec():
    if not Path(PARSEC_EXE).exists():
        log.warning(f"Parsec non trovato in {PARSEC_EXE}")
        return
    peer_id = json.loads(PARSEC_CONFIG_PATH.read_text()).get("peer_id", "").strip()
    if not peer_id:
        log.warning("Nessun peer_id configurato in parsec_config.json")
        return
    subprocess.Popen([PARSEC_EXE, f"peer_id={peer_id}"])
    log.info(f"Avviato Parsec verso peer_id={peer_id}")


def launch_vpn():
    if not Path(PROTONVPN_EXE).exists():
        log.warning(f"ProtonVPN non trovato in {PROTONVPN_EXE}")
        return False
    subprocess.Popen([PROTONVPN_EXE])
    log.info("Avviato ProtonVPN")
    return True


def get_mac_address():
    return wol.get_local_mac()


def list_processes():
    """Elenco dei processi in esecuzione, ordinati per uso di memoria (i piu'
    pesanti prima, cosi' l'utente vede subito cosa vale la pena chiudere)."""
    processes = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            memory_mb = (info["memory_info"].rss / (1024 * 1024)) if info["memory_info"] else 0
            processes.append({"pid": info["pid"], "name": info["name"] or "?", "memory_mb": round(memory_mb, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    processes.sort(key=lambda p: p["memory_mb"], reverse=True)
    return processes[:100]


def kill_process(pid):
    try:
        pid = int(pid)
        proc = psutil.Process(pid)
        name = proc.name()
        proc.terminate()
        log.info(f"Processo terminato: {name} (pid {pid})")
        return {"type": "kill_result", "pid": pid, "ok": True, "message": f"{name} terminato"}
    except (TypeError, ValueError):
        return {"type": "kill_result", "pid": pid, "ok": False, "message": "PID non valido"}
    except psutil.NoSuchProcess:
        return {"type": "kill_result", "pid": pid, "ok": False, "message": "Processo gia' terminato"}
    except psutil.AccessDenied:
        return {"type": "kill_result", "pid": pid, "ok": False, "message": "Permesso negato (processo di sistema?)"}
    except Exception as e:
        return {"type": "kill_result", "pid": pid, "ok": False, "message": str(e)}


def system_power(action):
    """Spegnimento/riavvio/sospensione del PC, richiesti dal telefono."""
    try:
        if action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "0"])
            message = "PC in spegnimento"
        elif action == "restart":
            subprocess.Popen(["shutdown", "/r", "/t", "0"])
            message = "PC in riavvio"
        elif action == "sleep":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"])
            message = "PC in sospensione"
        else:
            return {"type": "power_error", "message": f"Azione sconosciuta: {action}"}
        log.info(f"Comando alimentazione PC: {action}")
        return {"type": "power_ok", "message": message}
    except Exception as e:
        log.warning(f"Comando alimentazione fallito ({action}): {e}")
        return {"type": "power_error", "message": str(e)}


def cast_discover():
    global last_renderers
    last_renderers = dlna_cast.discover_renderers()
    names = [r["name"] for r in last_renderers]
    log.info(f"Renderer trovati: {names}")
    return {"type": "cast_discover_result", "renderers": names}


def cast_play(filename, renderer_index):
    if renderer_index >= len(last_renderers):
        return {"type": "cast_error", "message": "Nessun TV trovata, premi prima CERCA TV"}

    file_path = MEDIA_DIR / filename
    if not file_path.is_file():
        return {"type": "cast_error", "message": f"File non trovato in media/: {filename}"}

    mime = mimetypes.guess_type(str(file_path))[0] or "video/mp4"
    media_url = f"http://{dlna_cast.get_local_ip()}:{MEDIA_HTTP_PORT}/{filename}"
    renderer = last_renderers[renderer_index]
    control_url = renderer["control_url"]

    try:
        dlna_cast.set_av_transport_uri(control_url, media_url, filename, mime)
    except Exception as e:
        log.warning(f"SetAVTransportURI fallito: {e}")
        return {"type": "cast_error", "message": f"Impossibile caricare il file su {renderer['name']}"}

    try:
        dlna_cast.play(control_url)
    except Exception as e:
        # Alcuni TV LG accettano il comando ma non rispondono in tempo: verifichiamo lo stato reale.
        log.info(f"Play senza risposta ({e}), verifico lo stato reale...")

    try:
        state = dlna_cast.get_transport_info(control_url)
    except Exception:
        state = "SCONOSCIUTO"

    if state in ("PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING"):
        log.info(f"Riproduzione di {filename} confermata su {renderer['name']}")
        return {"type": "cast_ok", "message": f"In riproduzione su {renderer['name']}"}
    else:
        log.warning(f"Stato dopo Play: {state}")
        return {"type": "cast_error", "message": f"Comando inviato ma stato TV: {state}"}


def _token_from_path(path):
    return parse_qs(urlparse(path).query).get("token", [None])[0]


class HubHttpHandler(SimpleHTTPRequestHandler):
    """Serve i file media (casting DLNA) e lo stream MJPEG per la TV."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(MEDIA_DIR), **kwargs)

    def log_message(self, format, *args):
        pass  # evita di inondare la console con una riga per fotogramma

    def do_GET(self):
        if self.path.startswith("/fs/download"):
            self._serve_download()
            return
        super().do_GET()

    def _serve_download(self):
        query = parse_qs(urlparse(self.path).query)
        token = query.get("token", [None])[0]
        if not auth.is_valid(token):
            self.send_error(401, "Token mancante o non valido")
            return

        raw_path = query.get("path", [None])[0]
        if not raw_path:
            self.send_error(400, "Parametro 'path' mancante")
            return

        try:
            file_path = file_browser.resolve_file_for_download(raw_path)
        except file_browser.FileBrowserError as e:
            self.send_error(404, str(e))
            return

        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        try:
            size = file_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{file_path.name}"'
            )
            self.end_headers()
            with open(file_path, "rb") as f:
                while chunk := f.read(1024 * 256):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # il telefono ha interrotto il download


def start_media_http_server():
    httpd = ThreadingHTTPServer((HOST, MEDIA_HTTP_PORT), HubHttpHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info(f"Server HTTP in ascolto su {HOST}:{MEDIA_HTTP_PORT} (media: {MEDIA_DIR})")


async def _authenticate(websocket, peer, timeout=10):
    """Il primo messaggio deve essere {"type":"auth","token":...}. Ritorna True se valido."""
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    except (asyncio.TimeoutError, websockets.ConnectionClosed):
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if data.get("type") == "auth" and auth.is_valid(data.get("token")):
        await websocket.send(json.dumps({"type": "auth_ok"}))
        return True
    log.warning(f"Autenticazione fallita da {peer}")
    await websocket.send(json.dumps({"type": "auth_error", "message": "Token non valido"}))
    return False


async def handler(websocket):
    peer = websocket.remote_address
    if not await _authenticate(websocket, peer):
        await websocket.close(code=4001, reason="Autenticazione richiesta")
        return

    log.info(f"Connesso: {peer}")
    connected_clients.add(id(websocket))
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                log.info(f"Messaggio non JSON da {peer}: {message}")
                continue
            try:
                if data.get("type") in ASYNC_COMMANDS:
                    response = await handle_async_command(data)
                else:
                    response = await asyncio.to_thread(handle_command, data, id(websocket), peer[0])
            except Exception as e:
                log.warning(f"Comando fallito ({_redact_for_log(data)}): {e}")
                response = {"type": "cast_error", "message": "Errore interno nell'esecuzione del comando"}
            log.info(f"Comando da {peer}: {_redact_for_log(data)}")
            if response is not None:
                await websocket.send(json.dumps(response))
    except websockets.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(id(websocket))
        with shared_devices_lock:
            shared_devices.pop(id(websocket), None)
        log.info(f"Disconnesso: {peer}")


async def screen_handler(websocket):
    """Canale dedicato allo schermo esteso del telefono: video in uscita, tocco in entrata.

    Il monitor virtuale vive finche' c'e' almeno uno spettatore (telefono o TV):
    quando l'ultimo si disconnette viene rimosso automaticamente.
    """
    peer = websocket.remote_address
    token = _token_from_path(websocket.request.path)
    if not auth.is_valid(token):
        log.warning(f"Autenticazione schermo esteso fallita da {peer}")
        await websocket.close(code=4001, reason="Autenticazione richiesta")
        return

    log.info(f"Schermo esteso richiesto da {peer}")

    loop = asyncio.get_running_loop()
    frames = asyncio.Queue(maxsize=2)

    def enqueue(data):
        # Se il telefono non sta al passo scartiamo il fotogramma vecchio:
        # meglio perdere frame che accumulare ritardo.
        if frames.full():
            try:
                frames.get_nowait()
            except asyncio.QueueEmpty:
                pass
        frames.put_nowait(data)

    def on_frame(data):
        loop.call_soon_threadsafe(enqueue, data)

    try:
        geo = await asyncio.to_thread(screen_stream.hub.subscribe, on_frame)
    except Exception as e:
        log.warning(f"Avvio schermo esteso fallito: {e}")
        await websocket.send(json.dumps({"type": "screen_error", "message": str(e)}))
        return

    await websocket.send(json.dumps({
        "type": "screen_ready",
        "width": geo["width"],
        "height": geo["height"],
    }))

    async def send_frames():
        while True:
            data = await frames.get()
            await websocket.send(data)

    async def receive_touch():
        async for message in websocket:
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                continue
            if data.get("type") == "touch":
                await asyncio.to_thread(screen_stream.hub.handle_touch, data)
            elif data.get("type") == "uac_accept":
                try:
                    await asyncio.to_thread(service_client.send_command, data)
                except service_client.ServiceUnavailable as e:
                    log.warning(f"Comando UAC fallito: {e}")

    sender = asyncio.create_task(send_frames())
    receiver = asyncio.create_task(receive_touch())
    try:
        _done, pending = await asyncio.wait(
            [sender, receiver], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    except websockets.ConnectionClosed:
        pass
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.to_thread(screen_stream.hub.unsubscribe, on_frame)
        log.info(f"Schermo esteso chiuso da {peer}")


class ProjectorViewer:
    """Mostra in una finestra nativa i fotogrammi JPEG mandati dal telefono
    (Projector: l'opposto di "Schermo PC", qui e' il telefono a trasmettere).

    Tutte le chiamate a cv2 (imshow/waitKey/destroyWindow) girano sempre sullo
    stesso thread dedicato: e' l'uso corretto di OpenCV HighGUI, che non e'
    pensato per essere pilotato da thread diversi in momenti diversi.
    """

    WINDOW_NAME = "Specchio telefono"

    def __init__(self):
        self._frames = queue.Queue(maxsize=2)
        self._thread = None
        self._running = False

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def push_frame(self, jpeg_bytes):
        if self._frames.full():
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
        self._frames.put_nowait(jpeg_bytes)

    def stop(self):
        self._running = False

    @property
    def is_active(self):
        return self._thread is not None and self._thread.is_alive()

    def _loop(self):
        shown = False
        try:
            while self._running:
                try:
                    jpeg = self._frames.get(timeout=0.5)
                except queue.Empty:
                    continue
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    cv2.imshow(self.WINDOW_NAME, frame)
                    shown = True
                if cv2.waitKey(1) & 0xFF == 27:  # ESC chiude in anticipo
                    break
        finally:
            if shown:
                try:
                    cv2.destroyWindow(self.WINDOW_NAME)
                except cv2.error:
                    pass


projector_viewer = ProjectorViewer()


async def projector_handler(websocket):
    """Canale dedicato al Projector: il telefono manda qui i fotogrammi
    JPEG del proprio schermo, il PC li mostra in una finestra."""
    peer = websocket.remote_address
    token = _token_from_path(websocket.request.path)
    if not auth.is_valid(token):
        log.warning(f"Autenticazione projector fallita da {peer}")
        await websocket.close(code=4001, reason="Autenticazione richiesta")
        return

    log.info(f"Projector connesso da {peer}")
    projector_viewer.start()
    try:
        async for message in websocket:
            if isinstance(message, (bytes, bytearray)):
                projector_viewer.push_frame(message)
    except websockets.ConnectionClosed:
        pass
    finally:
        projector_viewer.stop()
        log.info(f"Projector disconnesso da {peer}")


class VirtualCamOutput:
    """Inoltra i fotogrammi JPEG mandati dal telefono verso una webcam
    virtuale di sistema (driver "OBS Virtual Camera"), cosi' qualunque altro
    programma (Zoom, Teams, Discord, browser...) puo' selezionare il telefono
    come webcam. Gira su un thread dedicato: pyvirtualcam.Camera tiene aperto
    un device di sistema e va ricreato se cambiano le dimensioni del frame."""

    FPS = 25

    def __init__(self):
        self._frames = queue.Queue(maxsize=2)
        self._thread = None
        self._running = False
        self._error = None

    def start(self):
        self._error = None
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def push_frame(self, jpeg_bytes):
        if self._frames.full():
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
        self._frames.put_nowait(jpeg_bytes)

    def stop(self):
        self._running = False

    @property
    def error(self):
        return self._error

    @property
    def is_active(self):
        return self._thread is not None and self._thread.is_alive()

    def _loop(self):
        cam = None
        cam_size = None
        try:
            while self._running:
                try:
                    jpeg = self._frames.get(timeout=0.5)
                except queue.Empty:
                    continue
                frame_bgr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame_bgr is None:
                    continue
                height, width = frame_bgr.shape[:2]
                if cam is None or cam_size != (width, height):
                    if cam is not None:
                        cam.close()
                    try:
                        cam = pyvirtualcam.Camera(width=width, height=height, fps=self.FPS, backend="obs")
                    except Exception as e:
                        self._error = str(e)
                        log.warning(f"Virtual camera non disponibile: {e}")
                        break
                    cam_size = (width, height)
                    log.info(f"Virtual camera attiva: {width}x{height}")
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                cam.send(frame_rgb)
        finally:
            if cam is not None:
                cam.close()


virtualcam_output = VirtualCamOutput()


async def virtualcam_handler(websocket):
    """Canale dedicato alla Virtual Camera: il telefono manda qui i fotogrammi
    JPEG della propria fotocamera, il PC li inoltra alla webcam virtuale."""
    peer = websocket.remote_address
    token = _token_from_path(websocket.request.path)
    if not auth.is_valid(token):
        log.warning(f"Autenticazione virtual camera fallita da {peer}")
        await websocket.close(code=4001, reason="Autenticazione richiesta")
        return

    log.info(f"Virtual camera connessa da {peer}")
    virtualcam_output.start()
    try:
        async for message in websocket:
            if isinstance(message, (bytes, bytearray)):
                virtualcam_output.push_frame(message)
            else:
                try:
                    data = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue
                if data.get("type") == "status_request":
                    await websocket.send(json.dumps({
                        "type": "status",
                        "error": virtualcam_output.error,
                    }))
    except websockets.ConnectionClosed:
        pass
    finally:
        virtualcam_output.stop()
        log.info(f"Virtual camera disconnessa da {peer}")


class UvcCameraStreamer:
    """Cattura una webcam USB gia' collegata al PC (Camera UVC: il contrario
    della Virtual Camera, qui e' il PC a trasmettere una webcam fisica al
    telefono). Gira su un thread dedicato: cv2.VideoCapture non e' pensato
    per essere aperto/letto da thread diversi in momenti diversi."""

    MAX_WIDTH = 960
    JPEG_QUALITY = 70

    CONTROLS = {
        "brightness": cv2.CAP_PROP_BRIGHTNESS,
        "contrast": cv2.CAP_PROP_CONTRAST,
        "zoom": cv2.CAP_PROP_ZOOM,
    }

    def __init__(self):
        self._cap = None
        self._thread = None
        self._running = False
        self._frames = queue.Queue(maxsize=2)

    def list_devices(self, max_index=5):
        devices = []
        for i in range(max_index):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            try:
                if cap.isOpened():
                    ok, _ = cap.read()
                    if ok:
                        devices.append(i)
            finally:
                cap.release()
        return devices

    def start(self, device):
        self.stop()
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(device,), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._thread = None
        self._cap = None

    def set_control(self, name, value):
        prop = self.CONTROLS.get(name)
        if prop is None or self._cap is None:
            return
        try:
            self._cap.set(prop, float(value))
        except (TypeError, ValueError):
            pass

    def get_frame(self, timeout=0.5):
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def _loop(self, device):
        cap = cv2.VideoCapture(device, cv2.CAP_DSHOW)
        self._cap = cap
        try:
            if not cap.isOpened():
                return
            while self._running:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                height, width = frame.shape[:2]
                if width > self.MAX_WIDTH:
                    scale = self.MAX_WIDTH / width
                    frame = cv2.resize(frame, (self.MAX_WIDTH, int(height * scale)))
                ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY])
                if not ok2:
                    continue
                data = buf.tobytes()
                if self._frames.full():
                    try:
                        self._frames.get_nowait()
                    except queue.Empty:
                        pass
                self._frames.put_nowait(data)
        finally:
            cap.release()
            self._cap = None


async def uvccam_handler(websocket):
    """Canale dedicato alla Camera UVC: il PC manda al telefono i fotogrammi
    di una webcam fisica gia' collegata, il telefono puo' sceglierla e
    regolarne luminosita'/contrasto/zoom (se supportati dal driver)."""
    peer = websocket.remote_address
    token = _token_from_path(websocket.request.path)
    if not auth.is_valid(token):
        log.warning(f"Autenticazione camera UVC fallita da {peer}")
        await websocket.close(code=4001, reason="Autenticazione richiesta")
        return

    log.info(f"Camera UVC connessa da {peer}")
    streamer = UvcCameraStreamer()
    global active_uvccam_streamer
    active_uvccam_streamer = streamer

    async def send_frames():
        while True:
            data = await asyncio.to_thread(streamer.get_frame, 0.5)
            if data is not None:
                await websocket.send(data)

    async def receive_commands():
        async for message in websocket:
            if isinstance(message, (bytes, bytearray)):
                continue
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                continue
            cmd = data.get("type")
            if cmd == "list_devices":
                devices = await asyncio.to_thread(streamer.list_devices)
                await websocket.send(json.dumps({"type": "device_list", "devices": devices}))
            elif cmd == "start":
                device = data.get("device", 0)
                await asyncio.to_thread(streamer.start, device)
                uvccam_status["active"] = True
                uvccam_status["device"] = device
            elif cmd == "stop":
                await asyncio.to_thread(streamer.stop)
                uvccam_status["active"] = False
                uvccam_status["device"] = None
            elif cmd == "set_control":
                await asyncio.to_thread(streamer.set_control, data.get("name"), data.get("value"))

    sender = asyncio.create_task(send_frames())
    receiver = asyncio.create_task(receive_commands())
    try:
        _done, pending = await asyncio.wait(
            [sender, receiver], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    except websockets.ConnectionClosed:
        pass
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.to_thread(streamer.stop)
        uvccam_status["active"] = False
        uvccam_status["device"] = None
        if active_uvccam_streamer is streamer:
            active_uvccam_streamer = None
        log.info(f"Camera UVC disconnessa da {peer}")


def collect_dashboard_status():
    """Chiamato dal thread del server HTTP della dashboard (sincrono, non
    asyncio): raccoglie lo stato corrente di tutti i moduli in un dict
    semplice da serializzare in JSON."""
    with shared_devices_lock:
        shares = list(shared_devices.values())
    return {
        "hostname": platform.node() or "PC",
        "uptime_seconds": time.time() - SERVER_START_TIME,
        "connected_clients": len(connected_clients),
        "screen_viewers": screen_stream.hub.viewers,
        "projector_active": projector_viewer.is_active,
        "virtualcam_active": virtualcam_output.is_active,
        "virtualcam_error": virtualcam_output.error,
        "uvccam_active": uvccam_status["active"],
        "uvccam_device": uvccam_status["device"],
        "shares": shares,
        "service_installed": service_client.is_installed(),
        "account_paired": firebase_relay.is_enabled(),
        "own_device_id": firebase_relay.own_device_id(),
    }


def handle_dashboard_action(action, payload):
    """Comandi che i pulsanti della dashboard PC possono eseguire. Gira sul
    thread del server HTTP della dashboard (sincrono): tutte le chiamate qui
    dentro sono gia' pensate per essere sicure da un thread qualsiasi (nessun
    oggetto asyncio coinvolto)."""
    if action == "stop_projector":
        projector_viewer.stop()
        return {"ok": True}
    if action == "stop_virtualcam":
        virtualcam_output.stop()
        return {"ok": True}
    if action == "stop_uvccam":
        if active_uvccam_streamer is not None:
            active_uvccam_streamer.stop()
        uvccam_status["active"] = False
        uvccam_status["device"] = None
        return {"ok": True}
    if action == "list_devices":
        return {"devices": firebase_relay.list_devices()}
    if action == "device_command":
        firebase_relay.send_command(payload.get("device_id"), payload.get("command"))
        return {"ok": True}
    if action == "rename_device":
        firebase_relay.rename_device(payload.get("device_id"), payload.get("name"))
        return {"ok": True}
    if action == "remove_device":
        firebase_relay.remove_device(payload.get("device_id"))
        return {"ok": True}
    return {"ok": False, "error": "azione sconosciuta"}


async def main():
    print("=" * 60, flush=True)
    print("  TOKEN DI ACCESSO (da inserire una volta nell'app):", flush=True)
    print(f"  {auth.TOKEN}", flush=True)
    print("=" * 60, flush=True)
    start_media_http_server()
    dashboard_server.start(status_provider=collect_dashboard_status, action_handler=handle_dashboard_action)
    firebase_relay.start()
    async with (
        serve(handler, HOST, PORT),
        serve(screen_handler, HOST, SCREEN_PORT, max_size=None),
        serve(projector_handler, HOST, PROJECTOR_PORT, max_size=None),
        serve(virtualcam_handler, HOST, VIRTUALCAM_PORT, max_size=None),
        serve(uvccam_handler, HOST, UVCCAM_PORT, max_size=None),
    ):
        log.info(f"Server WebSocket in ascolto su {HOST}:{PORT}")
        log.info(f"Schermo esteso in ascolto su {HOST}:{SCREEN_PORT}")
        log.info(f"Projector in ascolto su {HOST}:{PROJECTOR_PORT}")
        log.info(f"Virtual camera in ascolto su {HOST}:{VIRTUALCAM_PORT}")
        log.info(f"Camera UVC in ascolto su {HOST}:{UVCCAM_PORT}")
        await asyncio.Future()  # resta in esecuzione indefinitamente


if __name__ == "__main__":
    asyncio.run(main())
