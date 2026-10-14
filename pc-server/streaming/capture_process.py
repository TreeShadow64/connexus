"""Processo figlio che cattura il monitor principale del PC via DirectX.

Perche' un processo separato e non un thread: la factory di dxcam e' un
singleton creato all'import ed enumera li' gli output video ("Only 1 instance
of DXFactory is allowed"). Isolando la cattura qui, ogni sessione riparte con
una factory pulita invece di riusarne una che potrebbe puntare a un output
non piu' valido.

Protocollo su stdout (binario): 1 byte tipo + 4 byte lunghezza big-endian + payload
  tipo 1 = geometria del monitor (JSON)
  tipo 2 = fotogramma JPEG
"""
import ctypes
import json
import struct
import sys
import threading
import time
from ctypes import wintypes

MSG_GEOMETRY = 1
MSG_FRAME = 2
MSG_VIDEO = 3      # byte del flusso MP4 frammentato (H.264)
MSG_RTC = 4        # risposta SDP alla segnalazione WebRTC (JSON)

KEEPALIVE_S = 1.0

user32 = ctypes.WinDLL("user32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.GetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]

# Sagoma di una freccia da puntatore, in coordinate relative alla punta.
CURSOR_SHAPE = [
    (0, 0), (0, 16), (4, 12), (7, 19), (10, 18), (7, 11), (12, 11),
]


def get_cursor_on_display(geometry):
    """Posizione del cursore relativa al monitor virtuale, o None se e' altrove.

    La Desktop Duplication API non include il puntatore nell'immagine catturata:
    senza questo, sulla TV e sul telefono il mouse sarebbe invisibile.
    """
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    x = point.x - geometry["x"]
    y = point.y - geometry["y"]
    if 0 <= x < geometry["width"] and 0 <= y < geometry["height"]:
        return x, y
    return None


def draw_cursor(frame, position, scale, cv2, np):
    """Disegna la freccia del puntatore sul fotogramma (bianca con bordo nero)."""
    x = int(position[0] * scale)
    y = int(position[1] * scale)
    points = np.array(
        [[x + int(dx * scale * 1.6), y + int(dy * scale * 1.6)] for dx, dy in CURSOR_SHAPE],
        dtype=np.int32,
    )
    cv2.fillPoly(frame, [points], (255, 255, 255))
    cv2.polylines(frame, [points], True, (0, 0, 0), 1, cv2.LINE_AA)


def log(message):
    print(message, file=sys.stderr, flush=True)


_out_lock = threading.Lock()


def write_message(kind, payload):
    # Chiamata da piu' thread (video, audio, JPEG di ripiego): intestazione e
    # payload devono uscire insieme, altrimenti i messaggi si mescolano e il
    # processo principale perde la sincronia del flusso.
    with _out_lock:
        out = sys.stdout.buffer
        out.write(struct.pack(">BI", kind, len(payload)))
        out.write(payload)
        out.flush()


def _open_virtual_monitor(virtual_display):
    """Crea (o riusa) un monitor virtuale Parsec e ne ritorna geometria e gestore.

    Il monitor vive finche' questo processo tiene aperto il gestore: se il
    processo termina, Windows lo rimuove da solo e le finestre tornano sul
    monitor principale."""
    existing = virtual_display.find_virtual_display()
    manager = None
    if existing is None:
        manager = virtual_display.VirtualDisplay()
        manager.add_display()
        deadline = time.time() + 8
        existing = virtual_display.find_virtual_display()
        while existing is None and time.time() < deadline:
            time.sleep(0.5)
            existing = virtual_display.find_virtual_display()
    return existing, manager


class _VideoSink:
    """File virtuale su cui il muxer MP4 scrive: ogni pezzo parte subito verso il
    processo principale. Non e' ricercabile: serve l'MP4 frammentato."""

    def __init__(self):
        self.position = 0

    def write(self, data):
        data = bytes(data)
        write_message(MSG_VIDEO, data)
        self.position += len(data)
        return len(data)

    def flush(self):
        pass

    def seekable(self):
        return False

    def tell(self):
        return self.position


def _parse_h264_option(args):
    """'h264=1920,30,26[,12000]' -> (larghezza, fps, crf, kbit/s o 0). Con il quarto
    valore il flusso ha un bitrate costante (CBR) invece della qualita' costante."""
    for token in args:
        if token.startswith("h264="):
            values = [int(v) for v in token[5:].split(",")]
            width, fps, crf = values[:3]
            return width, fps, crf, (values[3] if len(values) > 3 else 0)
    return None


def _open_h264(av, geometry, width_limit, fps, crf, kbps=0):
    """Codificatore H.264 + muxer MP4 frammentato. Ritorna (container, stream, w, h)."""
    from fractions import Fraction

    src_w, src_h = geometry["width"], geometry["height"]
    out_w = min(src_w, width_limit)
    out_h = int(src_h * out_w / src_w)
    out_w -= out_w % 2
    out_h -= out_h % 2
    gop = max(2, fps // 10)         # un fotogramma chiave ogni ~100 ms: il muxer emette un frammento
                                    # a ogni chiave, quindi e' anche il ritardo minimo di consegna

    container = av.open(
        _VideoSink(), mode="w", format="mp4",
        options={"movflags": "empty_moov+default_base_moof+frag_keyframe"},
    )
    params = f"keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0"
    if kbps:
        # bitrate costante: la TV tiene da parte una quantita' fissa di BYTE prima di
        # mostrare, quindi piu' bit al secondo = meno secondi di ritardo, anche con
        # il desktop fermo (i byte di riempimento li produce l'encoder stesso)
        options = {
            "preset": "ultrafast", "tune": "zerolatency", "profile": "baseline",
            "x264-params": params + f":bitrate={kbps}:vbv-maxrate={kbps}:vbv-bufsize={max(1, kbps // 8)}:nal-hrd=cbr",
        }
    else:
        options = {
            "preset": "ultrafast", "tune": "zerolatency", "profile": "baseline",
            "crf": str(crf), "maxrate": "12M", "bufsize": "4M",
            "x264-params": params,
        }
    stream = container.add_stream("libx264", rate=Fraction(fps, 1), options=options)
    stream.width, stream.height = out_w, out_h
    stream.pix_fmt = "yuv420p"
    return container, stream, out_w, out_h


def main():
    target_fps = int(sys.argv[1])
    jpeg_quality = int(sys.argv[2])
    max_width = int(sys.argv[3])
    virtual = len(sys.argv) > 4 and sys.argv[4] == "virtual"

    from streaming import virtual_display

    manager = None
    if virtual:
        try:
            geometry, manager = _open_virtual_monitor(virtual_display)
        except Exception as e:
            log(f"ERRORE: monitor virtuale non creato: {e}")
            return 1
        if geometry is None:
            log("ERRORE: il monitor virtuale non e' comparso (driver Parsec installato?)")
            return 1
    else:
        geometry = virtual_display.find_primary_display()
        if geometry is None:
            log("ERRORE: monitor principale non trovato")
            return 1

    import cv2
    import numpy as np
    import dxcam

    camera = None
    for device_idx, outputs in enumerate(dxcam.__factory.outputs):
        for output_idx, output in enumerate(outputs):
            if output.devicename == geometry["device_name"]:
                camera = dxcam.create(
                    device_idx=device_idx, output_idx=output_idx, output_color="BGR"
                )
    if camera is None:
        log(f"ERRORE: dxcam non trova l'output {geometry['device_name']}")
        return 1

    write_message(MSG_GEOMETRY, json.dumps(geometry).encode("utf-8"))
    log(f"cattura del monitor {'virtuale' if virtual else 'principale'} avviata su {geometry['device_name']}")

    h264 = _parse_h264_option(sys.argv[4:])
    if h264 is not None:
        return _run_h264(camera, geometry, h264, target_fps, jpeg_quality, max_width, manager,
                         with_audio="audio=1" in sys.argv[4:])

    camera.start(target_fps=target_fps, video_mode=True)

    previous = None
    previous_cursor = None
    last_encoded = None
    last_sent = 0.0

    try:
        while True:
            frame = camera.get_latest_frame()
            if frame is None:
                continue

            height, width = frame.shape[:2]
            scale = 1.0
            if width > max_width:
                scale = max_width / width
                frame = cv2.resize(
                    frame, (max_width, int(height * scale)), interpolation=cv2.INTER_AREA
                )

            cursor = get_cursor_on_display(geometry)
            now = time.time()

            # Il confronto avviene sull'immagine senza puntatore, ma anche un
            # semplice spostamento del mouse deve produrre un nuovo fotogramma.
            unchanged = (
                previous is not None
                and cursor == previous_cursor
                and np.array_equal(frame, previous)
            )
            if unchanged:
                # Desktop fermo: ritrasmettiamo comunque ogni tanto, cosi' chi si
                # collega adesso vede subito qualcosa e chi e' sparito viene notato.
                if last_encoded is not None and now - last_sent >= KEEPALIVE_S:
                    last_sent = now
                    write_message(MSG_FRAME, last_encoded)
                else:
                    time.sleep(1 / target_fps)
                continue

            previous = frame
            previous_cursor = cursor

            if cursor is not None:
                frame = frame.copy()
                draw_cursor(frame, cursor, scale, cv2, np)

            ok, encoded = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
            )
            if not ok:
                continue

            last_encoded = encoded.tobytes()
            last_sent = now
            write_message(MSG_FRAME, last_encoded)
    except (BrokenPipeError, OSError):
        pass  # il server ha chiuso la pipe: e' il segnale di terminare
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass
    return 0


_rtc_hub = None      # RtcHub se la sessione e' in modalita' WebRTC


def _start_rtc(width, height, with_audio):
    """Crea l'hub WebRTC e il thread che legge le offerte SDP da stdin."""
    global _rtc_hub
    sys.setswitchinterval(0.001)     # il thread WebRTC non deve aspettare 5 ms il GIL: avrebbe jitter
    try:
        from streaming.rtc_peer import RtcHub
        import os
        _rtc_hub = RtcHub(width, height, log=log, with_audio=with_audio and os.environ.get('CONNEXUS_RTC_AUDIO') != '0')
    except Exception as e:
        log(f"WebRTC non disponibile ({type(e).__name__}: {e}): resta il flusso video classico")
        write_message(MSG_RTC, json.dumps({"ready": False}).encode("utf-8"))
        return

    def control():
        for raw in iter(sys.stdin.buffer.readline, b""):
            try:
                msg = json.loads(raw.decode("utf-8"))
                if msg.get("type") != "rtc_offer":
                    continue
                try:
                    answer = _rtc_hub.offer(msg["sdp"], msg.get("sdp_type", "offer"))
                    reply = {"id": msg["id"], **answer}
                except Exception as e:
                    reply = {"id": msg.get("id"), "error": f"{type(e).__name__}: {e}"}
                write_message(MSG_RTC, json.dumps(reply).encode("utf-8"))
            except Exception as e:
                log(f"segnalazione WebRTC: {type(e).__name__}: {e}")

    threading.Thread(target=control, name="rtc-control", daemon=True).start()
    write_message(MSG_RTC, json.dumps({"ready": True}).encode("utf-8"))
    log("WebRTC pronto: la TV puo' collegarsi con ritardo minimo")


class _AudioMux:
    """Cattura l'audio di sistema (loopback WASAPI), lo porta a 48 kHz stereo e lo
    codifica in AAC dentro lo stesso MP4 del video, con la stessa base dei tempi:
    cosi' la TV li riproduce sempre sincronizzati. Quando non suona nulla WASAPI non
    produce dati: il silenzio viene riempito per mantenere la linea del tempo."""

    RATE = 48000

    def __init__(self, av, container, lock, t0):
        import queue
        import threading
        from fractions import Fraction

        self.av, self.container, self.lock, self.t0 = av, container, lock, t0
        self.stream = container.add_stream("aac", rate=self.RATE, layout="stereo")
        self.stream.time_base = Fraction(1, self.RATE)
        self.q = queue.Queue(maxsize=400)
        self.written = 0
        self.stop_event = threading.Event()
        self.resampler = None
        self.src_rate = 0
        self._pa = None
        self._stream = None
        self._thread = threading.Thread(target=self._worker, daemon=True)

    def start(self):
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        spk = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        dev = next(d for d in pa.get_loopback_device_info_generator() if spk["name"] in d["name"])
        self.channels = int(dev["maxInputChannels"])
        self.src_rate = int(dev["defaultSampleRate"])
        self.resampler = self.av.AudioResampler(format="fltp", layout="stereo", rate=self.RATE)

        def on_audio(data, frames, t, status):
            try:
                self.q.put_nowait((time.time(), data))
            except Exception:
                pass
            hub = _rtc_hub
            if hub is not None:
                hub.push_audio(data, self.src_rate, self.channels)
            return (None, pyaudio.paContinue)

        self._stream = pa.open(format=pyaudio.paInt16, channels=self.channels, rate=self.src_rate,
                               input=True, input_device_index=dev["index"],
                               frames_per_buffer=512, stream_callback=on_audio)
        self._pa = pa
        self._thread.start()
        log(f"audio AAC nel flusso: {dev['name']} {self.src_rate} Hz")

    def stop(self):
        self.stop_event.set()
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass

    def _emit(self, frames):
        from fractions import Fraction
        for f in frames:
            f.pts = self.written
            f.time_base = Fraction(1, self.RATE)
            self.written += f.samples
            with self.lock:
                for packet in self.stream.encode(f):
                    self.container.mux(packet)

    def _worker(self):
        import queue
        import numpy as np
        try:
            while not self.stop_event.is_set():
                try:
                    ts, data = self.q.get(timeout=0.1)
                except queue.Empty:
                    # nulla in riproduzione: tieni la linea del tempo con silenzio
                    expected = int((time.time() - self.t0) * self.RATE)
                    if expected - self.written > self.RATE // 10:
                        self._silence_48k(expected - self.written - self.RATE // 20)
                    continue
                arr = np.frombuffer(data, dtype=np.int16)
                if self.channels != 2:
                    arr = arr.reshape(-1, self.channels)[:, :2].reshape(-1)
                n = len(arr) // 2
                start = int((ts - self.t0) * self.RATE) - int(n * self.RATE / self.src_rate)
                if start - self.written > self.RATE // 20:      # buco: riempi col silenzio
                    self._silence_48k(start - self.written)
                frame = self.av.AudioFrame.from_ndarray(arr.reshape(1, -1), format="s16", layout="stereo")
                frame.sample_rate = self.src_rate
                self._emit(self.resampler.resample(frame))
        except Exception as e:
            log(f"ERRORE audio: {type(e).__name__}: {e}")

    def _silence_48k(self, samples):
        """Silenzio gia' nel formato di uscita (48 kHz, float planare): non passa
        dal ricampionatore, che e' configurato sul formato della scheda audio."""
        import numpy as np
        while samples > 0:
            n = min(samples, 4800)
            frame = self.av.AudioFrame.from_ndarray(
                np.zeros((2, n), dtype=np.float32), format="fltp", layout="stereo")
            frame.sample_rate = self.RATE
            self._emit([frame])
            samples -= n


def _run_h264(camera, geometry, h264, jpeg_fps, jpeg_quality, jpeg_width, manager, with_audio=False):
    """Ciclo di cattura con video H.264 (per la TV) e, in parallelo, JPEG a bassa
    frequenza come ripiego per i browser che non sanno riprodurre il video."""
    import av
    import cv2
    import numpy as np

    width_limit, fps, crf, kbps = h264
    container, stream, out_w, out_h = _open_h264(av, geometry, width_limit, fps, crf, kbps)
    if "rtc=1" in sys.argv[4:]:
        _start_rtc(out_w, out_h, with_audio)
    log(f"video H.264 {out_w}x{out_h} a {fps} fps (" + (f"bitrate costante {kbps} kbit/s" if kbps else f"crf {crf}") + ")")

    import threading
    mux_lock = threading.Lock()
    camera.start(target_fps=fps, video_mode=True)
    scale = out_w / geometry["width"]
    jpeg_interval = 1.0 / max(1, jpeg_fps)
    last_jpeg = 0.0
    last_pts = -1
    t0 = time.time()
    audio = None
    if with_audio:
        try:
            audio = _AudioMux(av, container, mux_lock, t0)
            audio.start()
        except Exception as e:
            log(f"audio non disponibile ({type(e).__name__}: {e}): video senza suono nel flusso")
            audio = None

    try:
        while True:
            frame = camera.get_latest_frame()
            if frame is None:
                continue
            now = time.time()
            if frame.shape[1] != out_w or frame.shape[0] != out_h:
                frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)
            else:
                frame = frame.copy()

            cursor = get_cursor_on_display(geometry)
            if cursor is not None:
                draw_cursor(frame, cursor, scale, cv2, np)

            hub = _rtc_hub
            if hub is not None:
                hub.push_video(frame)

            if now - last_jpeg >= jpeg_interval and not (hub is not None and hub.connected):
                last_jpeg = now          # (il JPEG di ripiego serve solo se WebRTC non e' collegato)
                small = frame
                if out_w > jpeg_width:
                    small = cv2.resize(frame, (jpeg_width, int(out_h * jpeg_width / out_w)),
                                       interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
                if ok:
                    write_message(MSG_FRAME, encoded.tobytes())

            pts = int((now - t0) * fps)
            if pts <= last_pts:
                continue                         # stesso istante: un solo fotogramma per tick
            last_pts = pts
            av_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
            av_frame = av_frame.reformat(format="yuv420p")
            av_frame.pts = pts
            with mux_lock:
                for packet in stream.encode(av_frame):
                    container.mux(packet)
    except (BrokenPipeError, OSError):
        pass
    finally:
        if audio is not None:
            audio.stop()
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
