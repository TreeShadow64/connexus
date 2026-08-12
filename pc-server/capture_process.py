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


def main():
    target_fps = int(sys.argv[1])
    jpeg_quality = int(sys.argv[2])
    max_width = int(sys.argv[3])

    import virtual_display

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
    log(f"cattura del monitor principale avviata su {geometry['device_name']}")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
