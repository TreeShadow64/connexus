"""Ultime cartelle scelte nella dashboard (condivisione TV e FTP), conservate in
ui_state.json accanto all'exe cosi' sopravvivono ai riavvii. Si salva solo il
percorso: nessun dato sensibile."""
import json
import os

from core.paths import app_dir

STATE_PATH = app_dir() / "ui_state.json"
KEYS = ("tv_folder", "ftp_folder")


def _load():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_paths():
    """Percorsi salvati; quello che non esiste piu' (disco scollegato...) torna vuoto."""
    data = _load()
    return {k: (data.get(k, "") if os.path.isdir(str(data.get(k, ""))) else "") for k in KEYS}


def get_pref(key, default=""):
    value = _load().get(key, default)
    return value if isinstance(value, str) else default


def set_pref(key, value):
    """Preferenza di testo corto (es. dove si sente l'audio del secondo schermo)."""
    if not isinstance(value, str) or len(value) > 40 or key not in ("tv_audio_route",):
        return False
    data = _load()
    data[key] = value
    try:
        STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def set_path(key, value):
    if key not in KEYS:
        return False
    value = str(value or "")
    if len(value) > 500 or "\x00" in value:
        return False
    data = _load()
    if data.get(key) == value:
        return True
    data[key] = value
    try:
        STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True
