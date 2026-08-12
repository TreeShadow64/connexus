"""Sfoglia il filesystem del PC dal telefono e permette di scaricare i file.

Path vuoto/None = elenco delle unita' disco (C:\\, D:\\, ...).
Nessuna restrizione a una cartella radice: e' il PC dell'utente, protetto dal
token gia' richiesto su ogni canale. L'unica cosa da evitare sono crash su
cartelle non leggibili (permessi negati, unita' rimovibili assenti, ecc.).
"""
import string
from pathlib import Path


class FileBrowserError(Exception):
    pass


def list_drives():
    drives = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists():
            drives.append({"name": f"{letter}:\\", "is_dir": True, "size": 0})
    return drives


def list_directory(path):
    if not path:
        return list_drives()

    target = Path(path)
    if not target.exists():
        raise FileBrowserError(f"Percorso non trovato: {path}")
    if not target.is_dir():
        raise FileBrowserError(f"Non e' una cartella: {path}")

    entries = []
    try:
        children = list(target.iterdir())
    except PermissionError:
        raise FileBrowserError(f"Accesso negato: {path}")

    for child in children:
        try:
            is_dir = child.is_dir()
            size = 0 if is_dir else child.stat().st_size
        except (PermissionError, OSError):
            continue
        entries.append({"name": child.name, "is_dir": is_dir, "size": size})

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def parent_of(path):
    """Percorso della cartella superiore, o stringa vuota se si e' gia' alla radice delle unita'."""
    if not path:
        return ""
    target = Path(path)
    if target.parent == target:
        return ""  # es. "C:\\" -> torna all'elenco unita'
    return str(target.parent)


def resolve_file_for_download(path):
    """Verifica che il percorso sia un file esistente e leggibile, ritorna il Path."""
    target = Path(path)
    if not target.exists() or not target.is_file():
        raise FileBrowserError(f"File non trovato: {path}")
    return target
