"""Dove si sente l'audio del PC mentre la TV e' un secondo schermo.

Il suono viaggia sempre dentro il flusso video verso la TV (la cattura "loopback"
di Windows non dipende dal volume o dal muto degli altoparlanti). Questo modulo
decide solo cosa fanno gli altoparlanti del PC:

  tv  -> l'audio si sente sulla TV: gli altoparlanti del PC vengono silenziati
         finche' il secondo schermo e' acceso e poi ripristinati
  pc  -> l'audio resta sul PC: la TV riceve il video ma non lo riproduce

Se gli altoparlanti erano gia' muti per scelta dell'utente non vengono toccati ne'
"riaccesi". Un file marcatore permette di ripristinare il volume anche dopo un
arresto brusco (aggiornamento, crash)."""
import logging
import threading

from core.paths import app_dir

log = logging.getLogger("hub-server")

ROUTE_TV = "tv"
ROUTE_PC = "pc"
FLAG_NAME = "tv_audio_muted.flag"


class _CoreAudio:
    """Muto dell'uscita audio predefinita di Windows (Core Audio via pycaw)."""

    def _call(self, fn):
        import comtypes
        comtypes.CoInitialize()          # ogni thread che parla con COM deve inizializzarlo
        try:
            from pycaw.pycaw import AudioUtilities
            return fn(AudioUtilities.GetSpeakers().EndpointVolume)
        finally:
            comtypes.CoUninitialize()

    def get_mute(self):
        return bool(self._call(lambda vol: vol.GetMute()))

    def set_mute(self, muted):
        self._call(lambda vol: vol.SetMute(1 if muted else 0, None))


class AudioRoute:
    def __init__(self, backend=None, flag_path=None):
        self.route = ROUTE_TV
        self._backend = backend or _CoreAudio()
        self._flag = flag_path
        self._lock = threading.Lock()
        self._session = False       # secondo schermo acceso?
        self._we_muted = False      # il muto lo abbiamo messo noi?
        self._persist = False       # salva la scelta in ui_state.json (solo l'istanza vera)

    # --- file marcatore ---

    def _flag_path(self):
        return self._flag if self._flag is not None else app_dir() / FLAG_NAME

    def _write_flag(self):
        try:
            self._flag_path().write_text("1", encoding="utf-8")
        except OSError:
            pass

    def _clear_flag(self):
        try:
            self._flag_path().unlink()
        except OSError:
            pass

    # --- stato ---

    @property
    def pc_muted_by_us(self):
        return self._we_muted

    def state(self):
        return {"route": self.route, "pc_muted": self._we_muted}

    def set_route(self, route):
        if route not in (ROUTE_TV, ROUTE_PC):
            return self.route
        self.route = route
        if self._persist:
            try:
                from core import ui_state
                ui_state.set_pref("tv_audio_route", route)
            except Exception:
                pass
        self._apply()
        if route == ROUTE_PC and self._session:
            # scelta esplicita di sentirlo sul PC: se erano muti (da altri) si riaccendono
            with self._lock:
                try:
                    if self._backend.get_mute():
                        self._backend.set_mute(False)
                except Exception as e:
                    log.warning(f"Controllo altoparlanti non riuscito: {type(e).__name__}: {e}")
        return self.route

    def session(self, active):
        """Chiamata all'accensione e allo spegnimento del secondo schermo."""
        self._session = bool(active)
        self._apply()

    def restore_leftover(self):
        """All'avvio: se un'esecuzione precedente e' finita lasciando muti gli
        altoparlanti per colpa nostra, li riaccende."""
        if not self._flag_path().exists():
            return
        try:
            self._backend.set_mute(False)
            log.info("Altoparlanti del PC riattivati (rimasti muti da una sessione precedente)")
        except Exception as e:
            log.warning(f"Impossibile riattivare gli altoparlanti: {type(e).__name__}: {e}")
            return
        self._clear_flag()

    def _apply(self):
        want_mute = self._session and self.route == ROUTE_TV
        with self._lock:
            try:
                if want_mute and not self._we_muted:
                    if self._backend.get_mute():
                        return              # gia' muti per scelta dell'utente: non si tocca
                    self._write_flag()
                    self._backend.set_mute(True)
                    self._we_muted = True
                    log.info("Audio spostato sulla TV: altoparlanti del PC silenziati")
                elif not want_mute and self._we_muted:
                    self._backend.set_mute(False)
                    self._we_muted = False
                    self._clear_flag()
                    log.info("Audio di nuovo sul PC: altoparlanti riattivati")
            except Exception as e:
                log.warning(f"Controllo altoparlanti non riuscito: {type(e).__name__}: {e}")


route = AudioRoute()
route._persist = True
try:                                  # ricorda la scelta tra un riavvio e l'altro
    from core import ui_state as _ui_state
    if _ui_state.get_pref("tv_audio_route") in (ROUTE_TV, ROUTE_PC):
        route.route = _ui_state.get_pref("tv_audio_route")
except Exception:
    pass

import atexit  # noqa: E402
atexit.register(lambda: route.session(False))
