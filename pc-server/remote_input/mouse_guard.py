"""Impedisce al mouse di sconfinare sul monitor virtuale.

Con due schermi capita di far scivolare il puntatore sul secondo monitor per
sbaglio: durante una partita a schermo intero questo fa perdere il focus (e la
partita). Qui usiamo ClipCursor di Windows per confinare il puntatore allo
schermo principale finche' il blocco e' attivo.

Nota: Windows azzera il confinamento in diversi casi (cambio di finestra in
primo piano, blocco schermo, altre applicazioni che chiamano ClipCursor a loro
volta). Per questo un thread lo riapplica periodicamente finche' il blocco resta
richiesto.
"""
import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger("hub-server")

user32 = ctypes.WinDLL("user32", use_last_error=True)

SM_CXSCREEN = 0
SM_CYSCREEN = 1

REAPPLY_INTERVAL_S = 0.25


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


user32.ClipCursor.restype = wintypes.BOOL
user32.ClipCursor.argtypes = [ctypes.POINTER(RECT)]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]


class CursorGuard:
    """Confina il puntatore allo schermo principale, su richiesta."""

    def __init__(self):
        self._locked = False
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    @property
    def locked(self):
        return self._locked

    def _primary_rect(self):
        return RECT(
            0, 0,
            user32.GetSystemMetrics(SM_CXSCREEN),
            user32.GetSystemMetrics(SM_CYSCREEN),
        )

    def _apply_loop(self):
        while not self._stop.is_set():
            rect = self._primary_rect()
            user32.ClipCursor(ctypes.byref(rect))
            self._stop.wait(REAPPLY_INTERVAL_S)
        user32.ClipCursor(None)  # libera il puntatore all'uscita

    def lock(self):
        """Blocca il puntatore sullo schermo principale."""
        with self._lock:
            if self._locked:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._apply_loop, name="cursor-guard", daemon=True
            )
            self._thread.start()
            self._locked = True
            log.info("Mouse bloccato sullo schermo principale")

    def unlock(self):
        """Libera il puntatore: puo' tornare a passare sul monitor virtuale."""
        with self._lock:
            if not self._locked:
                return
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=2)
                self._thread = None
            user32.ClipCursor(None)
            self._locked = False
            log.info("Mouse libero di passare tra gli schermi")

    def set_locked(self, enabled):
        if enabled:
            self.lock()
        else:
            self.unlock()
        return self._locked


guard = CursorGuard()
