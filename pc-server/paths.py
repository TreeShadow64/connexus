"""Due cartelle diverse per due scopi diversi, che contano solo quando l'app
gira pacchettizzata come .exe (PyInstaller):

- app_dir(): dove vive l'eseguibile. Usarla per tutto cio' che deve
  sopravvivere a un riavvio (config, token di abbinamento, media caricati
  dall'utente) — MAI la cartella temporanea in cui PyInstaller estrae il
  bundle ad ogni avvio, che viene cancellata alla chiusura.
- bundle_dir(): dove vivono gli asset statici in sola lettura inclusi
  nell'exe (la dashboard HTML/CSS/JS).

In sviluppo (non pacchettizzato) coincidono entrambe con la cartella di
questo file."""
import sys
from pathlib import Path


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def bundle_dir():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).parent
