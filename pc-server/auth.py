"""Token di autenticazione per l'accesso al server.

In rete locale un intruso deve prima entrare nella tua LAN. Ma appena il PC
diventa raggiungibile anche da fuori casa (Fase 9, tramite Tailscale), chiunque
conosca l'indirizzo puo' altrimenti controllare il PC, leggere lo schermo e
comandare la TV senza alcuna verifica. Da qui in poi ogni comando richiede
questo token.

Il file va creato una sola volta (generato automaticamente al primo avvio) e
non va mai committato in un repository pubblico.
"""
import json
import secrets

from paths import app_dir

AUTH_CONFIG_PATH = app_dir() / "auth_config.json"


def _load_or_create():
    if AUTH_CONFIG_PATH.exists():
        config = json.loads(AUTH_CONFIG_PATH.read_text())
        if config.get("token"):
            return config

    config = {"token": secrets.token_hex(16)}
    AUTH_CONFIG_PATH.write_text(json.dumps(config, indent=4))
    return config


_config = _load_or_create()
TOKEN = _config["token"]


def is_valid(candidate):
    """Confronto a tempo costante: evita di rivelare il token tramite timing."""
    return isinstance(candidate, str) and secrets.compare_digest(candidate, TOKEN)
