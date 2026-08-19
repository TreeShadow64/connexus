"""Relay per "Trova dispositivo": fa da ponte tra Cloud Firestore (registro
dispositivi + comandi) e le notifiche push FCM verso i telefoni.

Il PC NON tiene piu' nessuna credenziale admin (niente Admin SDK, niente
firebase-service-account.json): quella chiave vive solo nel Worker
Cloudflare (cloudflare-relay/), l'unico posto fidato per mandare push e
firmare token per conto di un utente. Il PC si autentica invece come un
client normale, con gli stessi diritti (e le stesse regole di sicurezza) di
un telefono:

1. La prima volta che il telefono apre "Trova dispositivo", manda al PC (sul
   canale WebSocket LAN gia' autenticato) un custom token ottenuto dal
   Worker tramite il proprio idToken Firebase gia' loggato.
2. Il PC scambia quel custom token con un vero idToken Firebase (via
   Identity Toolkit REST) e lo usa come Bearer per parlare con Firestore via
   REST — rispettando le security rules, nessun accesso admin.
3. Per "svegliare" un telefono (che i client Firebase non possono fare da
   soli tra loro) il PC chiede al Worker di mandare la push per suo conto,
   passandogli il proprio idToken appena ottenuto.

Senza un Listen API realtime disponibile senza SDK admin, il PC controlla i
propri comandi in polling (ogni POLL_INTERVAL_S secondi) invece che in tempo
reale — sufficiente per un uso a gruppo di amici, e pensato per restare ben
sotto la quota gratuita di letture Firestore.
"""
import base64
import json
import logging
import platform
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import winsound
from datetime import datetime, timezone

from paths import app_dir

log = logging.getLogger("hub-server")

ACCOUNT_CONFIG_PATH = app_dir() / "account_config.json"
DEVICE_ID_PATH = app_dir() / "device_id.json"

FIREBASE_PROJECT_ID = "home-connexus"
FIREBASE_API_KEY = "AIzaSyAAUldk_FeffLZQ_pXBGKeStdmIPB7vu5E"
RELAY_BASE_URL = "https://connexus-relay.homeconnexus.workers.dev"
FIRESTORE_BASE = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"

ALARM_TIMEOUT_S = 120
HEARTBEAT_INTERVAL_S = 60
# 6s (il valore originale) genera ~14.400 letture/giorno per PC lasciato
# acceso in tray: con piu' di un PC nel gruppo, o l'app aperta per piu'
# giorni di fila, basta da sola a mangiare la quota gratuita giornaliera
# di Firestore (50.000 letture) senza che nessuno abbia davvero usato
# "Trova dispositivo" — da qui gli errori 429 visti nei log. 20s scala il
# consumo di ~3 volte restando comunque reattivo per un allarme/localizza.
POLL_INTERVAL_S = 20

_enabled = False
_uid = None
_device_id = None
_id_token = None
_refresh_token = None
_token_expiry = 0.0
_alarm_thread = None
_alarm_stop = threading.Event()


class _Ts(str):
    """Marcatore: un valore di questo tipo va serializzato come timestampValue
    di Firestore invece che come stringa qualunque."""


def _now_iso():
    return _Ts(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


# ---------------------------------------------------------------------------
# Persistenza locale (uid + refresh token, cosi' non serve ripetere
# l'abbinamento dal telefono ad ogni riavvio del PC)
# ---------------------------------------------------------------------------

def _load_account():
    if not ACCOUNT_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(ACCOUNT_CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    uid, refresh_token = data.get("uid"), data.get("refresh_token")
    if not uid or not refresh_token:
        return None
    return uid, refresh_token


def _save_account(uid, refresh_token):
    ACCOUNT_CONFIG_PATH.write_text(json.dumps({"uid": uid, "refresh_token": refresh_token}, indent=4))


def _load_or_create_device_id():
    if DEVICE_ID_PATH.exists():
        try:
            existing = json.loads(DEVICE_ID_PATH.read_text()).get("device_id")
            if existing:
                return existing
        except (json.JSONDecodeError, OSError):
            pass
    device_id = uuid.uuid4().hex
    DEVICE_ID_PATH.write_text(json.dumps({"device_id": device_id}, indent=4))
    return device_id


def _decode_uid_from_jwt(token):
    """Legge il claim 'uid' dal payload del custom token senza verificarne la
    firma: il canale da cui arriva (WebSocket LAN gia' autenticato) e' gia'
    fidato allo stesso modo in cui lo era prima con l'uid mandato in chiaro."""
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    uid = payload.get("uid")
    if not uid:
        raise ValueError("uid assente nel token")
    return uid


# ---------------------------------------------------------------------------
# HTTP di basso livello
# ---------------------------------------------------------------------------

def _http_json(method, url, body=None, headers=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    # Lo User-Agent di default di urllib viene bloccato (403) dalla protezione
    # anti-bot di Cloudflare sul Worker: ne serve uno che non sembri uno script.
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ConnexusPC/1.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {raw[:300]}") from None


# ---------------------------------------------------------------------------
# Autenticazione: custom token -> idToken, con refresh automatico
# ---------------------------------------------------------------------------

def _signin_with_custom_token(custom_token):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={FIREBASE_API_KEY}"
    data = _http_json("POST", url, body={"token": custom_token, "returnSecureToken": True})
    expiry = time.time() + float(data["expiresIn"])
    return data["idToken"], data["refreshToken"], expiry


def _refresh_id_token(refresh_token):
    url = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}"
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"refresh HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None
    expiry = time.time() + float(data["expires_in"])
    return data["id_token"], data["refresh_token"], expiry


def _ensure_fresh_id_token():
    global _id_token, _refresh_token, _token_expiry
    if _id_token and time.time() < _token_expiry - 120:
        return _id_token
    if not _refresh_token:
        raise RuntimeError("nessun refresh token disponibile, serve un nuovo abbinamento dal telefono")
    _id_token, _refresh_token, _token_expiry = _refresh_id_token(_refresh_token)
    _save_account(_uid, _refresh_token)
    return _id_token


# ---------------------------------------------------------------------------
# Firestore via REST, con l'idToken come Bearer (rispetta le security rules)
# ---------------------------------------------------------------------------

def _to_value(v):
    if isinstance(v, _Ts):
        return {"timestampValue": str(v)}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, dict):
        return {"mapValue": {"fields": _to_fields(v)}}
    if v is None:
        return {"nullValue": None}
    raise TypeError(f"tipo non supportato per Firestore: {type(v)}")


def _to_fields(d):
    return {k: _to_value(v) for k, v in d.items()}


def _from_value(v):
    if "stringValue" in v:
        return v["stringValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "timestampValue" in v:
        return v["timestampValue"]
    if "mapValue" in v:
        return _from_fields(v["mapValue"].get("fields", {}))
    return None


def _from_fields(fields):
    return {k: _from_value(v) for k, v in fields.items()}


def _doc_id(full_name):
    return full_name.rsplit("/", 1)[-1]


def _fs_request(method, path, body=None, update_mask_fields=None):
    token = _ensure_fresh_id_token()
    url = f"{FIRESTORE_BASE}/{path}"
    if update_mask_fields:
        url += "?" + "&".join(f"updateMask.fieldPaths={f}" for f in update_mask_fields)
    return _http_json(method, url, body=body, headers={"Authorization": f"Bearer {token}"})


def _fs_set_merge(path, data):
    _fs_request("PATCH", path, body={"fields": _to_fields(data)}, update_mask_fields=list(data.keys()))


def _device_path(device_id=None):
    return f"users/{_uid}/devices/{device_id or _device_id}"


# ---------------------------------------------------------------------------
# API pubblica usata da server.py / dashboard
# ---------------------------------------------------------------------------

def register_account(custom_token):
    """Chiamato dal telefono (canale WebSocket gia' autenticato) la prima
    volta che apre "Trova dispositivo": non manda piu' un uid nudo, ma un
    custom token gia' pronto per essere scambiato con un idToken vero."""
    global _uid, _id_token, _refresh_token, _token_expiry
    if not custom_token:
        return {"type": "account_error", "message": "token mancante"}
    try:
        claimed_uid = _decode_uid_from_jwt(custom_token)
    except Exception:
        return {"type": "account_error", "message": "token non valido"}

    if _enabled and claimed_uid != _uid:
        _stop()
    if claimed_uid != _uid:
        _id_token, _refresh_token, _token_expiry = None, None, 0.0

    try:
        id_token, refresh_token, expiry = _signin_with_custom_token(custom_token)
    except Exception as e:
        log.warning(f"Trova dispositivo: scambio del token col relay fallito: {e}")
        return {"type": "account_error", "message": "autenticazione col relay fallita"}

    _uid = claimed_uid
    _id_token, _refresh_token, _token_expiry = id_token, refresh_token, expiry
    _save_account(_uid, _refresh_token)
    start()
    if _enabled:
        return {"type": "account_ok", "message": "PC registrato su Trova dispositivo"}
    return {"type": "account_error", "message": "avvio del relay fallito"}


def is_enabled():
    """Per la dashboard PC: e' vero solo se il relay e' davvero attivo
    (account abbinato, autenticazione riuscita)."""
    return _enabled


def own_device_id():
    """Per la dashboard PC: sapere qual e' la riga del PC stesso nella lista
    di 'Trova dispositivo' (es. per non mostrare 'rimuovi' su se stesso)."""
    return _device_id


def list_devices():
    """Per la dashboard PC: stessa lista che vede il telefono in 'Trova
    dispositivo', letta pero' via REST con l'idToken del PC."""
    if not _enabled:
        return []
    try:
        data = _fs_request("GET", f"users/{_uid}/devices")
    except Exception as e:
        log.warning(f"Trova dispositivo: lettura lista dispositivi fallita: {e}")
        return []
    result = []
    for doc in data.get("documents", []):
        item = _from_fields(doc.get("fields", {}))
        item["id"] = _doc_id(doc["name"])
        result.append(item)
    return result


def send_command(device_id, cmd_type):
    if not _enabled or not device_id or not cmd_type:
        return
    try:
        _fs_request("POST", f"users/{_uid}/devices/{device_id}/commands", body={"fields": _to_fields({
            "type": cmd_type,
            "status": "pending",
            "requestedAt": _now_iso(),
        })})
    except Exception as e:
        log.warning(f"Trova dispositivo: invio comando fallito: {e}")
        return
    if device_id != _device_id:
        _relay_to_phone(device_id, cmd_type)


def rename_device(device_id, name):
    if not _enabled or not device_id or not name:
        return
    try:
        _fs_set_merge(f"users/{_uid}/devices/{device_id}", {"name": name})
    except Exception as e:
        log.warning(f"Trova dispositivo: rinomina fallita: {e}")


def remove_device(device_id):
    if not _enabled or not device_id:
        return
    try:
        _fs_request("DELETE", f"users/{_uid}/devices/{device_id}")
    except Exception as e:
        log.warning(f"Trova dispositivo: rimozione fallita: {e}")


def _stop():
    global _enabled
    _enabled = False


def start():
    """Avvia il relay se un account e' gia' noto (da un abbinamento
    precedente) o e' appena arrivato da register_account()."""
    global _enabled, _uid, _refresh_token, _device_id
    if _enabled:
        return
    if _uid is None:
        loaded = _load_account()
        if not loaded:
            log.info("Trova dispositivo: PC non ancora abbinato a un account (apri 'Trova dispositivo' dal telefono)")
            return
        _uid, _refresh_token = loaded

    _device_id = _load_or_create_device_id()
    try:
        _ensure_fresh_id_token()
        _register_pc_device()
    except Exception as e:
        # Non segna _enabled=True: un tentativo successivo (es. l'utente
        # riapre "Trova dispositivo" sul telefono) puo' ripartire da capo.
        log.warning(f"Trova dispositivo: avvio del relay fallito, riprovera' al prossimo tentativo: {e}")
        return

    _enabled = True
    threading.Thread(target=_relay_loop, daemon=True).start()
    log.info("Trova dispositivo: relay attivo (nessuna credenziale admin sul PC)")


def _register_pc_device():
    _fs_set_merge(_device_path(), {
        "name": platform.node() or "PC",
        "type": "pc",
        "lastSeen": _now_iso(),
        "alarmActive": False,
    })


def _relay_loop():
    """Sostituisce i listener realtime della Admin SDK: senza credenziali
    admin non c'e' un modo leggero di ascoltare Firestore in streaming da
    Python, quindi si controllano i propri comandi in polling. Ascolta solo
    i comandi diretti al PC stesso — quelli per gli altri dispositivi li
    inoltra chi li scrive (telefono o dashboard) chiamando subito il Worker,
    cosi' il sistema non dipende da un PC acceso per i comandi telefono ->
    telefono."""
    last_heartbeat = 0.0
    while _enabled:
        try:
            _ensure_fresh_id_token()
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                _update_own_lastseen()
                last_heartbeat = now
            _poll_own_commands()
        except Exception as e:
            log.warning(f"Trova dispositivo: ciclo del relay fallito: {e}")
        time.sleep(POLL_INTERVAL_S)


def _update_own_lastseen():
    try:
        _fs_set_merge(_device_path(), {"lastSeen": _now_iso()})
    except Exception as e:
        log.warning(f"Trova dispositivo: aggiornamento lastSeen fallito: {e}")


def _poll_own_commands():
    try:
        data = _fs_request("GET", f"users/{_uid}/devices/{_device_id}/commands")
    except Exception as e:
        log.warning(f"Trova dispositivo: lettura comandi fallita: {e}")
        return
    for doc in data.get("documents", []):
        fields = _from_fields(doc.get("fields", {}))
        if fields.get("status") != "pending":
            continue
        cmd_id = _doc_id(doc["name"])
        try:
            # Cancellato invece che marcato "done": non serve uno storico dei
            # comandi gia' eseguiti, e senza cancellarli la sottocollezione
            # crescerebbe all'infinito ad ogni localizza/allarme.
            _fs_request("DELETE", f"users/{_uid}/devices/{_device_id}/commands/{cmd_id}")
        except Exception as e:
            log.warning(f"Trova dispositivo: impossibile rimuovere il comando: {e}")
            continue
        _execute_local_command(fields.get("type"))


def _execute_local_command(cmd_type):
    if cmd_type == "locate":
        location = _geolocate_by_ip()
        update = {"lastSeen": _now_iso()}
        if location:
            update["lastLocation"] = {**location, "timestamp": _now_iso()}
        _fs_set_merge(_device_path(), update)
    elif cmd_type == "alarm_start":
        _start_alarm()
        _fs_set_merge(_device_path(), {"alarmActive": True})
    elif cmd_type == "alarm_stop":
        _stop_alarm()
        _fs_set_merge(_device_path(), {"alarmActive": False})


def _relay_to_phone(device_id, cmd_type):
    """I client Firebase non possono mandarsi notifiche a vicenda da soli:
    serve il Worker Cloudflare, l'unico che tiene le credenziali per farlo.
    Il PC gli manda solo il proprio idToken (nessun segreto admin)."""
    try:
        token = _ensure_fresh_id_token()
        _http_json("POST", f"{RELAY_BASE_URL}/send-push", body={
            "idToken": token, "deviceId": device_id, "cmd": cmd_type,
        })
        log.info(f"Trova dispositivo: comando '{cmd_type}' inoltrato via push a {device_id}")
    except Exception as e:
        log.warning(f"Trova dispositivo: invio push fallito: {e}")


def _geolocate_by_ip():
    """Stima approssimata (a livello di citta'): i PC normalmente non hanno GPS."""
    try:
        with urllib.request.urlopen("http://ip-api.com/json/", timeout=5) as resp:
            info = json.loads(resp.read().decode())
        if info.get("status") == "success":
            return {"lat": info["lat"], "lng": info["lon"], "accuracy": 50000}
    except Exception as e:
        log.warning(f"Trova dispositivo: geolocalizzazione IP fallita: {e}")
    return None


def _start_alarm():
    global _alarm_thread
    _alarm_stop.clear()
    if _alarm_thread is not None and _alarm_thread.is_alive():
        return
    _alarm_thread = threading.Thread(target=_alarm_loop, daemon=True)
    _alarm_thread.start()


def _alarm_loop():
    started = time.time()
    high = True
    while not _alarm_stop.is_set() and time.time() - started < ALARM_TIMEOUT_S:
        winsound.Beep(1200 if high else 800, 400)
        high = not high
    if not _alarm_stop.is_set():
        try:
            _fs_set_merge(_device_path(), {"alarmActive": False})
        except Exception as e:
            log.warning(f"Trova dispositivo: aggiornamento stato allarme fallito: {e}")
    _alarm_stop.set()


def _stop_alarm():
    _alarm_stop.set()
