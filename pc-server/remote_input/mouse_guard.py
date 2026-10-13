"""Blocca il puntatore su uno schermo.

Con due schermi (il secondo puo' essere la TV) il puntatore passa dall'uno
all'altro oltre il bordo. Qui lo si puo' confinare: libero, solo sullo schermo 1
(principale) oppure solo sullo schermo 2 (il monitor virtuale / TV).

Si usa ClipCursor di Windows. Windows azzera il confinamento in diversi casi
(cambio di finestra in primo piano, blocco schermo, altre applicazioni che chiamano
ClipCursor): per questo un thread lo riapplica periodicamente finche' il blocco
resta richiesto. Se lo schermo richiesto non esiste (es. la TV e' spenta) il
puntatore resta libero: non puo' mai restare intrappolato su uno schermo assente.
"""
import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger("hub-server")

user32 = ctypes.WinDLL("user32", use_last_error=True)

REAPPLY_INTERVAL_S = 0.25

MODE_FREE = "free"
MODE_PRIMARY = "primary"        # solo schermo 1
MODE_SECONDARY = "secondary"    # solo schermo 2 (monitor virtuale / TV)
MODES = (MODE_FREE, MODE_PRIMARY, MODE_SECONDARY)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


user32.ClipCursor.restype = wintypes.BOOL
user32.ClipCursor.argtypes = [ctypes.POINTER(RECT)]


def target_rect(mode, displays):
    """(left, top, right, bottom) in cui confinare il puntatore, o None se libero
    o se lo schermo richiesto non esiste. `displays` e' l'elenco di
    virtual_display.list_active_displays()."""
    if mode == MODE_FREE or not displays:
        return None
    primary = next((d for d in displays if d.get("primary")), displays[0])
    if mode == MODE_PRIMARY:
        chosen = primary
    elif mode == MODE_SECONDARY:
        others = [d for d in displays if d is not primary and not d.get("primary")]
        virtual = [d for d in others if "Parsec Virtual Display" in d.get("description", "")]
        chosen = (virtual or others or [None])[0]
        if chosen is None:
            return None
    else:
        return None
    return (chosen["x"], chosen["y"], chosen["x"] + chosen["width"], chosen["y"] + chosen["height"])


class CursorGuard:
    """Confina il puntatore allo schermo scelto, su richiesta."""

    def __init__(self):
        self._mode = MODE_FREE
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    @property
    def mode(self):
        return self._mode

    @property
    def locked(self):
        return self._mode != MODE_FREE

    def _apply_loop(self):
        from streaming import virtual_display
        while not self._stop.is_set():
            try:
                rect = target_rect(self._mode, virtual_display.list_active_displays())
            except Exception:
                rect = None
            if rect is None:
                user32.ClipCursor(None)
            else:
                clip = RECT(*rect)
                user32.ClipCursor(ctypes.byref(clip))
            self._stop.wait(REAPPLY_INTERVAL_S)
        user32.ClipCursor(None)  # libera il puntatore all'uscita

    def set_mode(self, mode):
        """Imposta libero / primary / secondary. Ritorna la modalita' attiva."""
        if mode not in MODES:
            mode = MODE_FREE
        with self._lock:
            if mode == self._mode:
                return self._mode
            if mode == MODE_FREE:
                self._stop.set()
                if self._thread is not None:
                    self._thread.join(timeout=2)
                    self._thread = None
                user32.ClipCursor(None)
                self._mode = MODE_FREE
                log.info("Mouse libero di passare tra gli schermi")
                return self._mode
            self._mode = mode
            if self._thread is None:
                self._stop.clear()
                self._thread = threading.Thread(target=self._apply_loop, name="cursor-guard", daemon=True)
                self._thread.start()
            log.info("Mouse bloccato sullo schermo " + ("1" if mode == MODE_PRIMARY else "2"))
            return self._mode

    # compatibilita' con il vecchio comando: acceso = solo schermo principale
    def set_locked(self, enabled):
        self.set_mode(MODE_PRIMARY if enabled else MODE_FREE)
        return self.locked


guard = CursorGuard()
