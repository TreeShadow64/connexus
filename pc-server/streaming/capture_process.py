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
import time
from ctypes import wintypes

MSG_GEOMETRY = 1
MSG_FRAME = 2
MSG_VIDEO = 3      # byte del flusso MP4 frammentato (H.264)

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


def write_message(kind, payload):
    sys.stdout.buffer.write(struct.pack(">BI", kind, len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


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
    """'h264=1920,30,26' -> (larghezza, fps, crf)."""
    for token in args:
        if token.startswith("h264="):
            width, fps, crf = (int(v) for v in token[5:].split(","))
            return width, fps, crf
    return None


def _open_h264(av, geometry, width_limit, fps, crf):
    """Codificatore H.264 + muxer MP4 frammentato. Ritorna (container, stream, w, h)."""
    from fractions import Fraction

    src_w, src_h = geometry["width"], geometry["height"]
    out_w = min(src_w, width_limit)
    out_h = int(src_h * out_w / src_w)
    out_w -= out_w % 2
    out_h -= out_h % 2
    gop = max(2, fps // 5)          # un fotogramma chiave ogni ~200 ms: frammenti brevi, bassa latenza

    container = av.open(
        _VideoSink(), mode="w", format="mp4",
        options={"movflags": "empty_moov+default_base_moof+frag_keyframe"},
    )
    options = {
        "preset": "ultrafast", "tune": "zerolatency", "profile": "baseline",
        "crf": str(crf), "maxrate": "12M", "bufsize": "4M",
        "x264-params": f"keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0",
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
        return _run_h264(camera, geometry, h264, target_fps, jpeg_quality, max_width, manager)

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


def _run_h264(camera, geometry, h264, jpeg_fps, jpeg_quality, jpeg_width, manager):
    """Ciclo di cattura con video H.264 (per la TV) e, in parallelo, JPEG a bassa
    frequenza come ripiego per i browser che non sanno riprodurre il video."""
    import av
    import cv2
    import numpy as np

    width_limit, fps, crf = h264
    container, stream, out_w, out_h = _open_h264(av, geometry, width_limit, fps, crf)
    log(f"video H.264 {out_w}x{out_h} a {fps} fps (crf {crf})")

    camera.start(target_fps=fps, video_mode=True)
    scale = out_w / geometry["width"]
    jpeg_interval = 1.0 / max(1, jpeg_fps)
    last_jpeg = 0.0
    last_pts = -1
    t0 = time.time()

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

            if now - last_jpeg >= jpeg_interval:
                last_jpeg = now
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
            for packet in stream.encode(av_frame):
                container.mux(packet)
    except (BrokenPipeError, OSError):
        pass
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
