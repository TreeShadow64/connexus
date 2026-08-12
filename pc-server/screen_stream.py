"""Schermo PC: monitor principale del PC catturato e trasmesso agli spettatori.

Pipeline: processo figlio (dxcam + JPEG) -> pipe -> StreamHub -> spettatori
(telefono via WebSocket binario, TV via MJPEG su HTTP).

Il tocco del telefono torna indietro come posizione assoluta del mouse sul
monitor principale, rendendolo di fatto un touchpad/schermo remoto.

La cattura vive in un processo separato (capture_process.py) perche' la
factory di dxcam e' un singleton creato all'import: isolarla in un processo
dedicato fa si' che ogni sessione riparta con una factory pulita.
"""
import json
import logging
import struct
import subprocess
import sys
import threading
from pathlib import Path

from pynput.mouse import Button
from pynput.mouse import Controller as MouseController

from paths import app_dir

log = logging.getLogger("hub-server")

mouse = MouseController()

MSG_GEOMETRY = 1
MSG_FRAME = 2


class ScreenStreamer:
    """Supervisiona il processo figlio che possiede monitor virtuale e cattura."""

    def __init__(self, target_fps=20, jpeg_quality=85, max_width=1920):
        self.target_fps = target_fps
        self.jpeg_quality = jpeg_quality
        self.max_width = max_width

        self._process = None
        self._reader = None
        self._geometry = None
        self._geometry_ready = threading.Event()
        self._on_frame = None

    @property
    def geometry(self):
        return self._geometry

    def start(self, on_frame):
        """Avvia il processo di cattura. on_frame(bytes) riceve i fotogrammi JPEG."""
        self._on_frame = on_frame
        self._geometry = None
        self._geometry_ready.clear()

        # Pacchettizzato (PyInstaller): non esiste un python.exe a cui passare
        # uno script sciolto, quindi si rilancia l'exe stesso con un flag
        # dedicato che desktop_app.py intercetta prima di avviare il resto
        # dell'app (stesso bundle, stessi moduli gia' inclusi).
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--capture-process", str(self.target_fps),
                     str(self.jpeg_quality), str(self.max_width)]
        else:
            script = Path(__file__).parent / "capture_process.py"
            args = [sys.executable, str(script), str(self.target_fps),
                     str(self.jpeg_quality), str(self.max_width)]
        self._process = subprocess.Popen(
            args,
            cwd=str(app_dir()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        threading.Thread(target=self._log_stderr, daemon=True).start()

        if not self._geometry_ready.wait(timeout=30):
            self.stop()
            raise RuntimeError("Timeout nell'avvio della cattura schermo")
        if self._geometry is None:
            self.stop()
            raise RuntimeError("Il processo di cattura non e' riuscito ad avviarsi")

        log.info(
            f"Cattura schermo PC attiva: {self._geometry['device_name']} "
            f"{self._geometry['width']}x{self._geometry['height']} "
            f"@ ({self._geometry['x']},{self._geometry['y']})"
        )

    def _read_frames(self, stdout):
        """Legge i messaggi: 1 byte tipo + 4 byte lunghezza big-endian + payload."""
        while True:
            header = stdout.read(5)
            if len(header) < 5:
                return
            kind, length = struct.unpack(">BI", header)
            payload = stdout.read(length)
            if len(payload) < length:
                return

            if kind == MSG_GEOMETRY:
                self._geometry = json.loads(payload.decode("utf-8"))
                self._geometry_ready.set()
            elif kind == MSG_FRAME and self._on_frame is not None:
                self._on_frame(payload)

    def _read_loop(self):
        try:
            self._read_frames(self._process.stdout)
        except Exception:
            pass
        finally:
            # Se il figlio muore da solo, sblocchiamo chi attende la geometria.
            self._geometry_ready.set()

    def _log_stderr(self):
        process = self._process
        if process is None:
            return
        for line in iter(process.stderr.readline, b""):
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                log.info(f"[cattura] {text}")

    def stop(self):
        """Termina il processo figlio: Windows rimuove da solo il monitor virtuale."""
        process, self._process = self._process, None
        self._on_frame = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        self._geometry = None
        log.info("Cattura schermo PC fermata")

    # --- input dal telefono ---

    def _to_absolute(self, norm_x, norm_y):
        geo = self._geometry
        x = geo["x"] + int(max(0.0, min(1.0, norm_x)) * (geo["width"] - 1))
        y = geo["y"] + int(max(0.0, min(1.0, norm_y)) * (geo["height"] - 1))
        return x, y

    def handle_touch(self, data):
        """Gestisce un evento di tocco con coordinate normalizzate [0,1]."""
        if self._geometry is None:
            return
        action = data.get("action")
        position = self._to_absolute(data.get("x", 0), data.get("y", 0))

        if action == "move":
            mouse.position = position
        elif action == "tap":
            mouse.position = position
            mouse.click(Button.left)
        elif action == "double_tap":
            mouse.position = position
            mouse.click(Button.right)


class StreamHub:
    """Un solo monitor virtuale condiviso da piu' spettatori.

    Il telefono (WebSocket binario) e la TV (MJPEG via HTTP) sono due
    "sottoscrittori" dello stesso flusso: il monitor virtuale nasce quando
    arriva il primo e viene rimosso quando se ne va l'ultimo.
    """

    def __init__(self):
        self._streamer = None
        self._subscribers = []
        self._latest = None
        self._lock = threading.RLock()

    def _dispatch(self, jpeg):
        with self._lock:
            self._latest = jpeg
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(jpeg)
            except Exception:
                pass  # uno spettatore lento non deve fermare gli altri

    def subscribe(self, callback):
        """Registra uno spettatore, avviando lo streaming se e' il primo."""
        with self._lock:
            is_first = not self._subscribers
            self._subscribers.append(callback)

            if is_first:
                streamer = ScreenStreamer()
                try:
                    streamer.start(self._dispatch)
                except Exception:
                    self._subscribers.remove(callback)
                    raise
                self._streamer = streamer
                self._latest = None
            elif self._latest is not None:
                # Chi arriva dopo non deve restare su schermo nero finche' il
                # desktop non cambia: gli diamo subito l'ultimo fotogramma.
                try:
                    callback(self._latest)
                except Exception:
                    pass

            return self._streamer.geometry

    def unsubscribe(self, callback):
        """Rimuove uno spettatore, fermando lo streaming se era l'ultimo."""
        streamer = None
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
            if not self._subscribers and self._streamer is not None:
                streamer, self._streamer = self._streamer, None
                self._latest = None
        # stop() fuori dal lock: il processo figlio potrebbe consegnare un
        # ultimo fotogramma, che passa da _dispatch e vuole lo stesso lock.
        if streamer is not None:
            streamer.stop()

    def handle_touch(self, data):
        streamer = self._streamer
        if streamer is not None:
            streamer.handle_touch(data)

    @property
    def viewers(self):
        with self._lock:
            return len(self._subscribers)


hub = StreamHub()
