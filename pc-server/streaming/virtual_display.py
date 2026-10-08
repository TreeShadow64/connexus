"""Controllo del Parsec Virtual Display Driver (VDD) tramite IOCTL.

Il driver e' gia' installato dal client Parsec (dispositivo PnP "Parsec Virtual
Display Adapter", hardware ID Root\\Parsec\\VDA) ed e' firmato: non serve
compilare nulla ne' attivare il test-signing mode di Windows.

Protocollo ricavato dalla documentazione reverse-engineered del progetto
open source nomi-san/parsec-vdd.

Proprieta' di sicurezza importante: gli schermi virtuali aggiunti restano vivi
solo finche' l'handle e' aperto e riceve un "ping" periodico (< 100 ms).
Se il processo termina o l'handle viene chiuso, Windows li rimuove da solo:
non e' possibile lasciare monitor fantasma nel sistema.
"""
import ctypes
import threading
import time
from ctypes import wintypes

# --- costanti Win32 ---
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_FLAG_NO_BUFFERING = 0x20000000
FILE_FLAG_OVERLAPPED = 0x40000000

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

# --- codici IOCTL del driver Parsec VDD ---
VDD_IOCTL_ADD = 0x0022E004
VDD_IOCTL_REMOVE = 0x0022A008
VDD_IOCTL_UPDATE = 0x0022A00C
VDD_IOCTL_VERSION = 0x0022E010

PING_INTERVAL_S = 0.05  # il driver richiede un ping ogni < 100 ms

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


VDD_INTERFACE_GUID = GUID(
    0x00B41627, 0x04C4, 0x429E,
    (ctypes.c_ubyte * 8)(0xA2, 0x6E, 0x02, 0x65, 0xCF, 0x50, 0xC8, 0xFA),
)

# Prototipi espliciti: senza questi ctypes assume un ritorno a 32 bit e
# tronca gli handle a 64 bit, facendo fallire ogni chiamata successiva.
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID),
    wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
]
kernel32.DeviceIoControl.restype = wintypes.BOOL
kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(OVERLAPPED),
]
kernel32.GetOverlappedResultEx.restype = wintypes.BOOL
kernel32.GetOverlappedResultEx.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD, wintypes.BOOL,
]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class VirtualDisplayError(Exception):
    pass


# --- enumerazione dei monitor attivi (serve per mappare il tocco del telefono) ---

user32 = ctypes.WinDLL("user32", use_last_error=True)

DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004
ENUM_CURRENT_SETTINGS = -1


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", ctypes.c_wchar * 32),
        ("DeviceString", ctypes.c_wchar * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", ctypes.c_wchar * 128),
        ("DeviceKey", ctypes.c_wchar * 128),
    ]


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


def list_active_displays():
    """Ritorna i monitor attualmente attaccati al desktop, con posizione e dimensione."""
    displays = []
    index = 0
    while True:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        if device.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            mode = DEVMODEW()
            mode.dmSize = ctypes.sizeof(DEVMODEW)
            if user32.EnumDisplaySettingsW(
                device.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)
            ):
                displays.append({
                    "device_name": device.DeviceName,
                    "description": device.DeviceString,
                    "primary": bool(device.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE),
                    "x": mode.dmPositionX,
                    "y": mode.dmPositionY,
                    "width": mode.dmPelsWidth,
                    "height": mode.dmPelsHeight,
                })
        index += 1
    return displays


def find_virtual_display():
    """Ritorna la geometria del monitor virtuale Parsec, o None se non presente."""
    for display in list_active_displays():
        if "Parsec Virtual Display" in display["description"]:
            return display
    return None


def find_primary_display():
    """Ritorna la geometria del monitor principale del PC, o None se non trovato."""
    for display in list_active_displays():
        if display["primary"]:
            return display
    return None


def _find_device_path():
    """Trova il path del dispositivo VDD tramite SetupAPI."""
    dev_info = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(VDD_INTERFACE_GUID), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if dev_info == INVALID_HANDLE_VALUE:
        raise VirtualDisplayError("SetupDiGetClassDevs fallita")

    try:
        iface = SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)

        if not setupapi.SetupDiEnumDeviceInterfaces(
            dev_info, None, ctypes.byref(VDD_INTERFACE_GUID), 0, ctypes.byref(iface)
        ):
            raise VirtualDisplayError(
                "Nessuna interfaccia VDD trovata: il driver Parsec e' installato?"
            )

        required = wintypes.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            dev_info, ctypes.byref(iface), None, 0, ctypes.byref(required), None
        )
        if required.value == 0:
            raise VirtualDisplayError("Impossibile determinare la dimensione del device path")

        buffer = ctypes.create_string_buffer(required.value)
        # SP_DEVICE_INTERFACE_DETAIL_DATA_W: cbSize e' 8 su x64, 6 su x86.
        cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        ctypes.memmove(buffer, ctypes.byref(wintypes.DWORD(cb_size)), 4)

        if not setupapi.SetupDiGetDeviceInterfaceDetailW(
            dev_info, ctypes.byref(iface), buffer, required.value,
            ctypes.byref(required), None,
        ):
            raise VirtualDisplayError(
                f"SetupDiGetDeviceInterfaceDetail fallita (err {ctypes.get_last_error()})"
            )

        # Il path inizia subito dopo il campo cbSize (offset 4), stringa wide.
        return ctypes.wstring_at(ctypes.addressof(buffer) + 4)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(dev_info)


class VirtualDisplay:
    """Gestisce il ciclo di vita di uno o piu' schermi virtuali."""

    def __init__(self):
        self._handle = None
        self._ping_thread = None
        self._stop_ping = threading.Event()
        self._display_indexes = []
        self._lock = threading.Lock()

    # -- gestione handle --

    def open(self):
        if self._handle is not None:
            return
        path = _find_device_path()
        handle = kernel32.CreateFileW(
            path,
            wintypes.DWORD(GENERIC_READ | GENERIC_WRITE),
            wintypes.DWORD(FILE_SHARE_READ | FILE_SHARE_WRITE),
            None,
            wintypes.DWORD(OPEN_EXISTING),
            wintypes.DWORD(
                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_NO_BUFFERING
                | FILE_FLAG_OVERLAPPED | FILE_FLAG_WRITE_THROUGH
            ),
            None,
        )
        if handle == INVALID_HANDLE_VALUE or not handle:
            raise VirtualDisplayError(
                f"Impossibile aprire il dispositivo VDD (err {ctypes.get_last_error()})"
            )
        self._handle = handle

    def close(self):
        """Chiude l'handle: Windows rimuove automaticamente tutti gli schermi virtuali."""
        self._stop_ping.set()
        if self._ping_thread is not None:
            self._ping_thread.join(timeout=1)
            self._ping_thread = None
        with self._lock:
            self._display_indexes.clear()
        if self._handle is not None:
            kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = None

    # -- IOCTL --

    def _io_control(self, code, data=None):
        if self._handle is None:
            raise VirtualDisplayError("Handle VDD non aperto")

        in_buffer = ctypes.create_string_buffer(32)
        if data:
            ctypes.memmove(in_buffer, data, min(len(data), 32))

        out_buffer = wintypes.DWORD(0)
        overlapped = OVERLAPPED()
        overlapped.hEvent = kernel32.CreateEventW(None, True, False, None)

        try:
            kernel32.DeviceIoControl(
                wintypes.HANDLE(self._handle),
                wintypes.DWORD(code),
                in_buffer, wintypes.DWORD(32),
                ctypes.byref(out_buffer), wintypes.DWORD(ctypes.sizeof(wintypes.DWORD)),
                None,
                ctypes.byref(overlapped),
            )
            transferred = wintypes.DWORD(0)
            ok = kernel32.GetOverlappedResultEx(
                wintypes.HANDLE(self._handle), ctypes.byref(overlapped),
                ctypes.byref(transferred), wintypes.DWORD(5000), False,
            )
            if not ok:
                raise VirtualDisplayError(
                    f"IOCTL {hex(code)} fallito (err {ctypes.get_last_error()})"
                )
            return out_buffer.value
        finally:
            if overlapped.hEvent:
                kernel32.CloseHandle(overlapped.hEvent)

    def version(self):
        return self._io_control(VDD_IOCTL_VERSION)

    def _ping(self):
        self._io_control(VDD_IOCTL_UPDATE)

    def _ping_loop(self):
        while not self._stop_ping.is_set():
            try:
                self._ping()
            except Exception:
                break
            self._stop_ping.wait(PING_INTERVAL_S)

    # -- schermi --

    def add_display(self):
        """Aggiunge uno schermo virtuale e ritorna il suo indice."""
        self.open()
        index = self._io_control(VDD_IOCTL_ADD)
        self._ping()

        with self._lock:
            self._display_indexes.append(index)

        if self._ping_thread is None:
            self._stop_ping.clear()
            self._ping_thread = threading.Thread(target=self._ping_loop, daemon=True)
            self._ping_thread.start()

        # Windows impiega un attimo a rendere disponibile il nuovo monitor.
        time.sleep(1.5)
        return index

    def remove_display(self, index):
        # L'indice viene passato al driver con i byte invertiti (big-endian).
        swapped = (((index & 0xFF) << 8) | ((index >> 8) & 0xFF)) & 0xFFFF
        self._io_control(VDD_IOCTL_REMOVE, swapped.to_bytes(2, "little"))
        self._ping()
        with self._lock:
            if index in self._display_indexes:
                self._display_indexes.remove(index)
        time.sleep(1.0)

    def remove_all(self):
        with self._lock:
            indexes = list(self._display_indexes)
        for index in indexes:
            try:
                self.remove_display(index)
            except VirtualDisplayError:
                pass

    @property
    def active_count(self):
        with self._lock:
            return len(self._display_indexes)
