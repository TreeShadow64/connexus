"""Iniezione di input (mouse/tastiera) nel desktop correntemente attivo.

Usato dall'helper che gira DENTRO la sessione interattiva (vedi
hub_service_helper.py) — non dal servizio Session-0 stesso, che non ha
accesso a nessun desktop visibile per via dell'isolamento delle sessioni
di Windows.

OpenInputDesktop() + SetThreadDesktop() vanno richiamate prima di OGNI
iniezione: ogni finestra UAC crea un desktop sicuro nuovo, quindi il
"desktop attivo" puo' cambiare da un comando all'altro.
"""
import ctypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

DESKTOP_ALL_ACCESS = 0x000F01FF
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
XBUTTON1 = 0x0001  # "indietro"
XBUTTON2 = 0x0002  # "avanti"
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# I tasti multimediali (volume...) sono fisicamente "extended" sulla maggior
# parte delle tastiere: senza questo flag Windows non li riconosce come tali
# e l'OSD del volume non compare, anche se keybd_event/SendInput non segnala
# alcun errore (verificato: e' l'unica differenza che fa funzionare il tasto).
EXTENDED_KEYS = {"volume_up", "volume_down", "volume_mute"}

VK_CODES = {
    "enter": 0x0D,
    "backspace": 0x08,
    "space": 0x20,
    "tab": 0x09,
    "esc": 0x1B,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "volume_mute": 0xAD,
}

PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", InputUnion)]


user32.OpenInputDesktop.restype = ctypes.c_void_p
user32.OpenInputDesktop.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
user32.SetThreadDesktop.restype = ctypes.c_int
user32.SetThreadDesktop.argtypes = [ctypes.c_void_p]
user32.CloseDesktop.restype = ctypes.c_int
user32.CloseDesktop.argtypes = [ctypes.c_void_p]
user32.SendInput.restype = ctypes.c_uint
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int]


def attach_to_active_desktop():
    hdesk = user32.OpenInputDesktop(0, False, DESKTOP_ALL_ACCESS)
    if not hdesk:
        return False
    ok = user32.SetThreadDesktop(hdesk)
    user32.CloseDesktop(hdesk)
    return bool(ok)


def _send(inputs):
    array = (Input * len(inputs))(*inputs)
    user32.SendInput(len(inputs), array, ctypes.sizeof(Input))


def move_mouse(dx, dy):
    if not attach_to_active_desktop():
        return False
    mi = MouseInput(dx, dy, 0, MOUSEEVENTF_MOVE, 0, None)
    _send([Input(INPUT_MOUSE, InputUnion(mi=mi))])
    return True


def click_mouse(button):
    if not attach_to_active_desktop():
        return False
    if button in ("back", "forward"):
        x_data = XBUTTON1 if button == "back" else XBUTTON2
        _send([Input(INPUT_MOUSE, InputUnion(mi=MouseInput(0, 0, x_data, MOUSEEVENTF_XDOWN, 0, None)))])
        _send([Input(INPUT_MOUSE, InputUnion(mi=MouseInput(0, 0, x_data, MOUSEEVENTF_XUP, 0, None)))])
        return True
    down = {"right": MOUSEEVENTF_RIGHTDOWN, "middle": MOUSEEVENTF_MIDDLEDOWN}.get(button, MOUSEEVENTF_LEFTDOWN)
    up = {"right": MOUSEEVENTF_RIGHTUP, "middle": MOUSEEVENTF_MIDDLEUP}.get(button, MOUSEEVENTF_LEFTUP)
    _send([Input(INPUT_MOUSE, InputUnion(mi=MouseInput(0, 0, 0, down, 0, None)))])
    _send([Input(INPUT_MOUSE, InputUnion(mi=MouseInput(0, 0, 0, up, 0, None)))])
    return True


def scroll_mouse(amount):
    """amount positivo = su, negativo = giu' (in 'tacche' di rotella)."""
    if not attach_to_active_desktop():
        return False
    mi = MouseInput(0, 0, ctypes.c_ulong(int(amount * WHEEL_DELTA) & 0xFFFFFFFF).value, MOUSEEVENTF_WHEEL, 0, None)
    _send([Input(INPUT_MOUSE, InputUnion(mi=mi))])
    return True


def type_text(text):
    if not attach_to_active_desktop():
        return False
    for char in text:
        code = ord(char)
        down = KeyBdInput(0, code, KEYEVENTF_UNICODE, 0, None)
        up = KeyBdInput(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
        _send([Input(INPUT_KEYBOARD, InputUnion(ki=down))])
        _send([Input(INPUT_KEYBOARD, InputUnion(ki=up))])
    return True


def press_key(name):
    vk = VK_CODES.get(name)
    if vk is None or not attach_to_active_desktop():
        return False
    flags = KEYEVENTF_EXTENDEDKEY if name in EXTENDED_KEYS else 0
    down = KeyBdInput(vk, 0, flags, 0, None)
    up = KeyBdInput(vk, 0, flags | KEYEVENTF_KEYUP, 0, None)
    _send([Input(INPUT_KEYBOARD, InputUnion(ki=down))])
    _send([Input(INPUT_KEYBOARD, InputUnion(ki=up))])
    return True


def accept_uac():
    """Tab per spostare il focus, poi Invio: un solo comando dal telefono
    invece di piu' tocchi durante la finestra che blocca tutto il PC.
    Il focus predefinito del prompt UAC non e' garantito essere "Si"."""
    import time

    if not attach_to_active_desktop():
        return False
    for vk in (VK_CODES["tab"], VK_CODES["enter"]):
        down = KeyBdInput(vk, 0, 0, 0, None)
        up = KeyBdInput(vk, 0, KEYEVENTF_KEYUP, 0, None)
        _send([Input(INPUT_KEYBOARD, InputUnion(ki=down))])
        _send([Input(INPUT_KEYBOARD, InputUnion(ki=up))])
        time.sleep(0.15)
        attach_to_active_desktop()
    return True
