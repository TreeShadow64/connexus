"""Auto-aggiornamento del PC: scarica l'ultima release da GitHub (stesso
posto da cui si aggiorna il telefono) invece di dipendere da un altro PC o
da me che rimando i file a mano.

Ha senso solo per l'app pacchettizzata (.exe): in sviluppo (python
desktop_app.py) non c'e' nulla da sostituire, quindi si disattiva da sola.

Il file eseguibile e la cartella _internal/ (codice/asset di sola lettura)
vengono sostituiti; i file di configurazione nella stessa cartella
(account_config.json, auth_config.json, media/...) restano intatti perche'
l'updater non li tocca per niente.

Un .exe non puo' sovrascrivere se stesso mentre gira: la sostituzione vera
la fa un piccolo script .bat lanciato a parte, che aspetta che il processo
sia uscito (robocopy ritenta da solo finche' il file non e' piu' bloccato),
copia i file nuovi e riavvia l'app.
"""
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from paths import app_dir

log = logging.getLogger("hub-server")

APP_VERSION = "0.33"
RELEASES_API = "https://api.github.com/repos/TreeShadow64/connexus/releases/latest"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ConnexusPC/1.0"


def _version_tuple(tag):
    return tuple(int(p) for p in re.sub(r"^v", "", tag).split(".") if p.isdigit())


def check_latest():
    """Ritorna {version, zip_url, update_available} o None se il controllo fallisce
    (rete assente, GitHub irraggiungibile...) — non deve mai bloccare l'avvio."""
    req = urllib.request.Request(RELEASES_API, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        log.info(f"Controllo aggiornamenti PC fallito (non grave): {e}")
        return None

    tag = data.get("tag_name", "")
    zip_url = None
    for asset in data.get("assets", []):
        if asset.get("name", "").endswith(".zip"):
            zip_url = asset.get("browser_download_url")
            break
    if not zip_url:
        return None

    try:
        newer = _version_tuple(tag) > _version_tuple(APP_VERSION)
    except ValueError:
        newer = tag != APP_VERSION

    return {"version": tag, "zip_url": zip_url, "update_available": newer}


def apply_update(zip_url, on_restart):
    """Scarica ed estrae la nuova versione, prepara ed avvia l'updater
    esterno, poi chiama on_restart() per chiudere l'app corrente (i file
    vanno rilasciati prima che il .bat possa sovrascriverli)."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("l'aggiornamento automatico funziona solo nella versione .exe, non in sviluppo")

    install_dir = app_dir()
    tmp_root = Path(tempfile.mkdtemp(prefix="connexus-update-"))
    zip_path = tmp_root / "update.zip"

    req = urllib.request.Request(zip_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, open(zip_path, "wb") as f:
        shutil.copyfileobj(resp, f)

    extract_dir = tmp_root / "extracted"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    # Lo zip contiene una singola cartella di primo livello (es. "Connexus-PC-Hub"):
    entries = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(entries) != 1:
        raise RuntimeError("formato dello zip di aggiornamento inatteso")
    new_dir = entries[0]

    bat_path = tmp_root / "apply_update.bat"
    bat_path.write_text(
        "@echo off\r\n"
        f'robocopy "{new_dir}" "{install_dir}" Connexus-PC-Hub.exe /R:20 /W:2 >nul\r\n'
        f'robocopy "{new_dir}\\_internal" "{install_dir}\\_internal" /MIR /R:20 /W:2 >nul\r\n'
        f'start "" "{install_dir}\\Connexus-PC-Hub.exe"\r\n'
        f'rmdir /S /Q "{tmp_root}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )

    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    on_restart()
