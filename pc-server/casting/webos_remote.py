"""Client minimale per il protocollo SSAP (Second Screen Application Protocol)
usato dai TV LG webOS per l'accoppiamento e il telecomando via rete locale.

Il manifest qui sotto e' quello "di test" pubblico usato da praticamente
tutti i progetti open source di telecomando webOS (pylgtv, aiowebostv, ecc.):
non e' un segreto, i TV LG lo accettano per app di terze parti in rete locale.
"""
import asyncio
import json

import websockets

REGISTER_PAYLOAD = {
    "forcePairing": False,
    "pairingType": "PROMPT",
    "manifest": {
        "manifestVersion": 1,
        "appVersion": "1.1",
        "signed": {
            "created": "20140509",
            "appId": "com.lge.test",
            "vendorId": "com.lge",
            "localizedAppNames": {
                "": "Hub PC Remote",
            },
            "localizedVendorNames": {
                "": "LG Electronics",
            },
            "permissions": [
                "TEST_SECURE",
                "CONTROL_INPUT_TEXT",
                "CONTROL_MOUSE_AND_KEYBOARD",
                "READ_INSTALLED_APPS",
                "READ_LGE_SDX",
                "READ_NOTIFICATIONS",
                "SEARCH",
                "WRITE_SETTINGS",
                "WRITE_NOTIFICATION_ALERT",
                "CONTROL_POWER",
                "READ_CURRENT_CHANNEL",
                "READ_RUNNING_APPS",
                "READ_UPDATE_INFO",
                "UPDATE_FROM_REMOTE_APP",
                "READ_LGE_TV_INPUT_EVENTS",
                "READ_TV_CURRENT_TIME",
            ],
            "serial": "2f930e2d2cfe083771f68e4fe7bb07",
        },
        "permissions": [
            "LAUNCH",
            "LAUNCH_WEBAPP",
            "APP_TO_APP",
            "CLOSE",
            "TEST_OPEN",
            "TEST_PROTECTED",
            "CONTROL_AUDIO",
            "CONTROL_DISPLAY",
            "CONTROL_INPUT_JOYSTICK",
            "CONTROL_INPUT_MEDIA_RECORDING",
            "CONTROL_INPUT_MEDIA_PLAYBACK",
            "CONTROL_INPUT_TV",
            "CONTROL_MOUSE_AND_KEYBOARD",
            "CONTROL_INPUT_TEXT",
            "CONTROL_POWER",
            "READ_APP_STATUS",
            "READ_INSTALLED_APPS",
            "READ_CURRENT_CHANNEL",
            "READ_INPUT_DEVICE_LIST",
            "READ_NETWORK_STATE",
            "READ_RUNNING_APPS",
            "READ_TV_CHANNEL_LIST",
            "WRITE_NOTIFICATION_TOAST",
            "READ_POWER_STATE",
            "READ_COUNTRY_INFO",
        ],
        "signatures": [
            {
                "signatureVersion": 1,
                "signature": (
                    "eyJhbGdvcml0aG0iOiJSU0EtU0hBMjU2Iiwia2V5SWQiOiJ0ZXN0LXNpZ25pbmctY2VydCIsInBhcmFtcyI6"
                    "eyJrZXlUeXBlIjoiT1NBIiwiZW5jb2RpbmciOiJoZXgiLCJkaWdlc3RUeXBlIjoic2hhMjU2In19.hrVRgjCwXVvE"
                    "2OOSpDZ58hR+59aFNwYDyjQBebTU3EbrOa1t8ehcvpVJKq5eUp1vI0AAKY3IWtOAPUxbrhamvj1DrzCTycQFuVEr"
                    "y2GA6ml6MDNvW+wZUKPluOn5eb/6uxV4C93NB2M9EYlH1zaGyYK/DubasfbULdmXvJRQwtqbbGEg8CVEK8OSRhZ8"
                    "9UOJ9j06VSLmM0Vs6rEQb9YujQ9AqcFV32vHQIrslYUHU7mZmt7NCLpXtiIU/9lXCyH0zQOgw4Ir/89t3ScDMRZm"
                    "Kz9ZI2eaQNTuySnvNoAKAOu2phnl3ThVSF2/2Y7/RQVLAeAsB0WsIFq+hCTAyMdSA=="
                ),
            }
        ],
    },
}

SSAP_PORT = 3000

BUTTON_URIS = {
    "volume_up": "ssap://audio/volumeUp",
    "volume_down": "ssap://audio/volumeDown",
    "mute": "ssap://audio/setMute",
    "channel_up": "ssap://tv/channelUp",
    "channel_down": "ssap://tv/channelDown",
    "power_off": "ssap://system/turnOff",
    "home": "ssap://com.webos.service.ime/closeSocketTypeUI",
}

DPAD_BUTTON_NAMES = {
    "up": "UP",
    "down": "DOWN",
    "left": "LEFT",
    "right": "RIGHT",
    "enter": "ENTER",
    "back": "BACK",
    "home": "HOME",
}

# Nomi verificati manualmente contro una TV reale (nessun errore alla chiamata):
# tasti colorati, cifre, ed extra tipici del telecomando fisico LG Magic Remote.
REMOTE_BUTTON_NAMES = {
    **DPAD_BUTTON_NAMES,
    "0": "0", "1": "1", "2": "2", "3": "3", "4": "4",
    "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
    "red": "RED", "green": "GREEN", "yellow": "YELLOW", "blue": "BLUE",
    "menu": "MENU", "list": "LIST", "info": "INFO", "guide": "GUIDE",
    "exit": "EXIT", "dash": "DASH", "play": "PLAY", "pause": "PAUSE",
}


class WebOSError(Exception):
    pass


async def _connect(tv_ip, timeout=5):
    uri = f"ws://{tv_ip}:{SSAP_PORT}/"
    return await asyncio.wait_for(websockets.connect(uri, max_size=None), timeout=timeout)


async def pair(tv_ip, client_key=None, prompt_timeout=30):
    """Avvia l'accoppiamento. Se client_key non e' valida, il TV mostra un prompt
    da accettare entro prompt_timeout secondi. Ritorna la client_key da salvare."""
    ws = await _connect(tv_ip)
    try:
        payload = dict(REGISTER_PAYLOAD)
        if client_key:
            payload = {**REGISTER_PAYLOAD, "client-key": client_key}
        await ws.send(json.dumps({"type": "register", "id": "register_0", "payload": payload}))

        deadline = asyncio.get_event_loop().time() + prompt_timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise WebOSError("Timeout: prompt non accettato sulla TV")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if msg.get("type") == "registered":
                return msg["payload"]["client-key"]
            if msg.get("type") == "error":
                raise WebOSError(msg.get("error", "errore di registrazione sconosciuto"))
            # "response" con pairingType=PROMPT: continuiamo ad aspettare l'accettazione
    finally:
        await ws.close()


async def send_request(tv_ip, client_key, uri, payload=None, timeout=5):
    """Apre una connessione, si registra con la client_key gia' nota (nessun prompt) ed esegue un comando."""
    if not client_key:
        raise WebOSError("TV non ancora abbinata: usa prima 'ABBINA TV'")

    ws = await _connect(tv_ip)
    try:
        reg_payload = {**REGISTER_PAYLOAD, "client-key": client_key}
        await ws.send(json.dumps({"type": "register", "id": "register_0", "payload": reg_payload}))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("type") != "registered":
            raise WebOSError("Registrazione rifiutata, l'abbinamento potrebbe essere scaduto")

        req_id = "req_0"
        await ws.send(json.dumps({"type": "request", "id": req_id, "uri": uri, "payload": payload or {}}))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return json.loads(raw)
    finally:
        await ws.close()


async def list_apps(tv_ip, client_key, timeout=8):
    """Elenco reale delle app installate sul TV (titolo + id per il lancio)."""
    r = await send_request(
        tv_ip, client_key, "ssap://com.webos.applicationManager/listLaunchPoints",
        timeout=timeout,
    )
    apps = r.get("payload", {}).get("launchPoints", [])
    result = []
    for a in apps:
        if not a.get("id"):
            continue
        icon = a.get("icon") or a.get("largeIcon") or ""
        if icon and not icon.startswith("http"):
            icon = f"http://{tv_ip}:3000{icon}"
        result.append({"title": a.get("title", ""), "id": a.get("id", ""), "icon": icon})
    return result


async def launch_app(tv_ip, client_key, app_id, timeout=8):
    return await send_request(
        tv_ip, client_key, "ssap://system.launcher/launch", {"id": app_id}, timeout,
    )


async def list_inputs(tv_ip, client_key, timeout=8):
    """Elenco reale delle sorgenti (HDMI, AV, ...) con id e nome mostrato."""
    r = await send_request(tv_ip, client_key, "ssap://tv/getExternalInputList", timeout=timeout)
    devices = r.get("payload", {}).get("devices", [])
    return [{"label": d.get("label", ""), "id": d.get("id", "")} for d in devices if d.get("id")]


async def switch_input(tv_ip, client_key, input_id, timeout=8):
    return await send_request(
        tv_ip, client_key, "ssap://tv/switchInput", {"inputId": input_id}, timeout,
    )


async def send_button(tv_ip, client_key, button_name, timeout=5):
    """Invia una pressione di tasto (D-pad, HOME, BACK, ecc.) tramite il pointer input socket."""
    if not client_key:
        raise WebOSError("TV non ancora abbinata: usa prima 'ABBINA TV'")

    ws = await _connect(tv_ip)
    try:
        reg_payload = {**REGISTER_PAYLOAD, "client-key": client_key}
        await ws.send(json.dumps({"type": "register", "id": "register_0", "payload": reg_payload}))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("type") != "registered":
            raise WebOSError("Registrazione rifiutata, l'abbinamento potrebbe essere scaduto")

        await ws.send(json.dumps({
            "type": "request",
            "id": "pointer_0",
            "uri": "ssap://com.webos.service.networkinput/getPointerInputSocket",
            "payload": {},
        }))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        socket_path = msg.get("payload", {}).get("socketPath")
        if not socket_path:
            raise WebOSError("Impossibile ottenere il socket per il D-pad")
    finally:
        await ws.close()

    pointer_ws = await asyncio.wait_for(websockets.connect(socket_path, max_size=None), timeout=timeout)
    try:
        await pointer_ws.send(f"type:button\nname:{button_name}\n\n")
    finally:
        await pointer_ws.close()
