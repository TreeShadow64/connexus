"""Programmi esterni che il telefono puo' avviare sul PC: Parsec e ProtonVPN.

I percorsi vengono rilevati da soli nelle cartelle di installazione standard e
possono essere cambiati dall'utente (telefono o dashboard). Per sicurezza si
puo' impostare solo un file .exe che si chiami davvero come il programma
atteso: chi ha il token non puo' cosi' far avviare un eseguibile qualsiasi.

Impostazioni in external_apps.json accanto all'exe; l'ID Parsec di vecchie
installazioni (parsec_config.json) viene letto come ripiego.
"""
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from core.paths import app_dir

log = logging.getLogger("hub-server")

CONFIG_PATH = app_dir() / "external_apps.json"
LEGACY_PARSEC_PATH = app_dir() / "parsec_config.json"

ALLOWED_NAMES = {
    "parsec": {"parsecd.exe", "parsec.exe"},
    "protonvpn": {"protonvpn.launcher.exe", "protonvpn.exe"},
}
PEER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _env(name):
    return os.environ.get(name, "")


def _candidates(app):
    pf, pf86, local = _env("ProgramFiles"), _env("ProgramFiles(x86)"), _env("LOCALAPPDATA")
    if app == "parsec":
        raw = [f"{pf}\\Parsec\\parsecd.exe", f"{pf86}\\Parsec\\parsecd.exe",
               f"{local}\\Parsec\\parsecd.exe"]
    else:
        raw = [f"{pf}\\Proton\\VPN\\ProtonVPN.Launcher.exe", f"{pf}\\Proton\\VPN\\ProtonVPN.exe",
               f"{local}\\Programs\\Proton\\VPN\\ProtonVPN.Launcher.exe",
               f"{pf86}\\Proton\\VPN\\ProtonVPN.Launcher.exe"]
    return [Path(p) for p in raw if p and not p.startswith("\\")]


def detect(app):
    """Primo percorso standard in cui il programma esiste, o stringa vuota."""
    for path in _candidates(app):
        if path.is_file():
            return str(path)
    return ""


def _load():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(config):
    CONFIG_PATH.write_text(json.dumps(config, indent=4), encoding="utf-8")


def _peer_id(config):
    peer = str(config.get("parsec_peer_id", "")).strip()
    if peer:
        return peer
    try:
        return str(json.loads(LEGACY_PARSEC_PATH.read_text()).get("peer_id", "")).strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def _resolve(app, config):
    """Percorso scelto dall'utente se esiste ancora, altrimenti quello rilevato."""
    custom = str(config.get(f"{app}_path", "")).strip()
    if custom and Path(custom).is_file():
        return custom
    return detect(app)


def get_config():
    config = _load()
    parsec = _resolve("parsec", config)
    vpn = _resolve("protonvpn", config)
    return {
        "parsec": {"path": parsec, "found": bool(parsec),
                   "custom_path": str(config.get("parsec_path", "")),
                   "peer_id": _peer_id(config)},
        "protonvpn": {"path": vpn, "found": bool(vpn),
                      "custom_path": str(config.get("protonvpn_path", ""))},
    }


def _validate_path(app, value):
    path = Path(value)
    if not path.is_file():
        return "il file non esiste"
    if path.name.lower() not in ALLOWED_NAMES[app]:
        nomi = " o ".join(sorted(ALLOWED_NAMES[app]))
        return f"il file deve chiamarsi {nomi}"
    return None


def set_config(parsec_path=None, parsec_peer_id=None, protonvpn_path=None):
    """None = lascia com'e'; stringa vuota = torna al rilevamento automatico.
    Ritorna (ok, messaggio)."""
    config = _load()
    for app, value, key in (("parsec", parsec_path, "parsec_path"),
                            ("protonvpn", protonvpn_path, "protonvpn_path")):
        if value is None:
            continue
        value = str(value).strip().strip('"')
        if value:
            problem = _validate_path(app, value)
            if problem:
                return False, f"Percorso {app} non valido: {problem}"
            config[key] = value
        else:
            config.pop(key, None)
    if parsec_peer_id is not None:
        peer = str(parsec_peer_id).strip()
        if peer and not PEER_ID_RE.match(peer):
            return False, "ID Parsec non valido: servono 8-64 caratteri tra lettere, numeri, - e _"
        if peer:
            config["parsec_peer_id"] = peer
        else:
            config.pop("parsec_peer_id", None)
    _save(config)
    return True, "Impostazioni salvate"


def launch_parsec():
    config = _load()
    exe = _resolve("parsec", config)
    if not exe:
        return False, "Parsec non trovato sul PC: installalo o imposta il percorso"
    peer = _peer_id(config)
    if not peer:
        return False, "Nessun ID Parsec impostato: inseriscilo nelle impostazioni"
    subprocess.Popen([exe, f"peer_id={peer}"])
    log.info("Avviato Parsec")
    return True, "Parsec avviato"


def launch_vpn():
    exe = _resolve("protonvpn", _load())
    if not exe:
        return False, "ProtonVPN non trovato sul PC: installalo o imposta il percorso"
    subprocess.Popen([exe])
    log.info("Avviato ProtonVPN")
    return True, "ProtonVPN avviato sul PC"
