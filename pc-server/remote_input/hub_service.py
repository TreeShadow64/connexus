"""Servizio Windows (LocalSystem) che permette il controllo mouse/tastiera
anche sul desktop sicuro di UAC e sulla schermata di blocco.

Il problema che risolve: un servizio Windows gira nella "Sessione 0",
completamente isolata dalla sessione interattiva dell'utente (Session 0
isolation, introdotta da Windows Vista in poi). Un servizio in Sessione 0
non puo' raggiungere NESSUN desktop visibile, nemmeno con privilegi SYSTEM:
OpenInputDesktop() dalla Sessione 0 non ha nulla da aprire.

La soluzione, usata anche da strumenti come PsExec ("-s -i"): il servizio
duplica il proprio token SYSTEM, ne modifica l'ID di sessione per farlo
puntare alla sessione interattiva corrente, e con quel token lancia un
processo helper DENTRO quella sessione (hub_service_helper.py). Essendo
SYSTEM e girando nella sessione giusta, l'helper puo' aprire con successo
sia il desktop normale sia quello sicuro (lo stesso motivo per cui
winlogon.exe e consent.exe, che girano in quel modo, ci riescono).

Il servizio stesso non fa altro che sorvegliare l'helper: lo rilancia se
muore o se cambia la sessione attiva (cambio utente, logon/logoff).
"""
import sys
from pathlib import Path

import pywintypes
import servicemanager
import win32api
import win32con
import win32event
import win32file
import win32process
import win32security
import win32service
import win32serviceutil
import win32ts

CHECK_INTERVAL_S = 3
HELPER_SCRIPT = Path(__file__).parent / "hub_service_helper.py"
HELPER_LOG = Path(r"C:\ProgramData\HubPC\helper.log")

PRIVILEGES_NEEDED = [
    win32security.SE_TCB_NAME,
    win32security.SE_ASSIGNPRIMARYTOKEN_NAME,
    win32security.SE_INCREASE_QUOTA_NAME,
]


def _enable_privileges():
    hToken = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY,
    )
    new_privileges = [
        (win32security.LookupPrivilegeValue(None, name), win32security.SE_PRIVILEGE_ENABLED)
        for name in PRIVILEGES_NEEDED
    ]
    win32security.AdjustTokenPrivileges(hToken, False, new_privileges)
    win32api.CloseHandle(hToken)


def _launch_helper_in_session(session_id):
    """Duplica il token SYSTEM del servizio, lo riassegna alla sessione
    indicata, e vi lancia l'helper. Ritorna l'handle del processo."""
    hProcessToken = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        win32security.TOKEN_ALL_ACCESS,
    )
    try:
        hNewToken = win32security.DuplicateTokenEx(
            hProcessToken,
            win32security.SecurityImpersonation,
            win32security.TOKEN_ALL_ACCESS,
            win32security.TokenPrimary,
            None,
        )
    finally:
        win32api.CloseHandle(hProcessToken)

    win32security.SetTokenInformation(hNewToken, win32security.TokenSessionId, session_id)

    # stdout/stderr dell'helper vanno su file: senza redirezione esplicita un
    # processo lanciato con CREATE_NO_WINDOW non ha una console a cui scrivere,
    # e senza un log non e' possibile capire perche' muore.
    HELPER_LOG.parent.mkdir(parents=True, exist_ok=True)
    security_attrs = pywintypes.SECURITY_ATTRIBUTES()
    security_attrs.bInheritHandle = True
    hLog = win32file.CreateFile(
        str(HELPER_LOG),
        win32file.GENERIC_WRITE,
        win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
        security_attrs,
        win32file.CREATE_ALWAYS,
        win32file.FILE_ATTRIBUTE_NORMAL,
        None,
    )

    startup_info = win32process.STARTUPINFO()
    startup_info.lpDesktop = "winsta0\\default"
    startup_info.dwFlags = win32process.STARTF_USESTDHANDLES
    startup_info.hStdOutput = hLog
    startup_info.hStdError = hLog
    startup_info.hStdInput = None

    # sys.executable dentro un servizio pywin32 punta a pythonservice.exe
    # (l'host del servizio), non al vero interprete: pythonservice.exe non
    # sa eseguire uno script arbitrario, si limiterebbe a stampare il suo
    # messaggio d'aiuto e uscire subito. sys.exec_prefix invece riflette
    # sempre la cartella di installazione di Python, da cui ricaviamo il
    # python.exe vero.
    python_exe = str(Path(sys.exec_prefix) / "python.exe")
    command_line = f'"{python_exe}" -u "{HELPER_SCRIPT}"'

    try:
        handle, _thread_handle, pid, _tid = win32process.CreateProcessAsUser(
            hNewToken,
            None,
            command_line,
            None,
            None,
            True,  # eredita gli handle (serve per la redirezione hStdOutput/hStdError)
            win32process.CREATE_NO_WINDOW,
            None,
            None,
            startup_info,
        )
    finally:
        win32api.CloseHandle(hNewToken)
        hLog.Close()
    return handle, pid


class HubInputService(win32serviceutil.ServiceFramework):
    _svc_name_ = "HubPCInputService"
    _svc_display_name_ = "Hub PC - Servizio Input (UAC/Lock screen)"
    _svc_description_ = (
        "Permette all'app Hub PC + Telefono di cliccare sulle finestre UAC e "
        "sulla schermata di blocco, lanciando un helper con privilegi SYSTEM "
        "nella sessione interattiva corrente. Ascolta solo su localhost."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.helper_handle = None
        self.helper_pid = None
        self.helper_session = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._kill_helper()
        win32event.SetEvent(self.stop_event)

    def _kill_helper(self):
        if self.helper_handle is not None:
            try:
                win32process.TerminateProcess(self.helper_handle, 0)
                win32api.CloseHandle(self.helper_handle)
            except Exception:
                pass
            self.helper_handle = None
            self.helper_pid = None

    def _helper_is_alive(self):
        if self.helper_handle is None:
            return False
        try:
            exit_code = win32process.GetExitCodeProcess(self.helper_handle)
        except Exception as e:
            servicemanager.LogErrorMsg(f"DEBUG GetExitCodeProcess fallita: {e}")
            return False
        servicemanager.LogInfoMsg(
            f"DEBUG exit_code={exit_code} STILL_ACTIVE={win32con.STILL_ACTIVE} "
            f"handle={self.helper_handle} pid={self.helper_pid}"
        )
        return exit_code == win32con.STILL_ACTIVE

    def _ensure_helper_running(self):
        active_session = win32ts.WTSGetActiveConsoleSessionId()
        # 0xFFFFFFFF significa "nessuna sessione interattiva attiva ora"
        # (es. subito dopo il boot, prima del login): non c'e' nulla a cui
        # agganciarsi, si riprova al giro successivo.
        if active_session in (0xFFFFFFFF, None):
            return

        alive = self._helper_is_alive()
        servicemanager.LogInfoMsg(
            f"DEBUG alive={alive} helper_session={self.helper_session} active_session={active_session}"
        )
        if alive and self.helper_session == active_session:
            return  # tutto normale: l'helper c'e' ed e' nella sessione giusta

        self._kill_helper()
        try:
            handle, pid = _launch_helper_in_session(active_session)
            self.helper_handle = handle
            self.helper_pid = pid
            self.helper_session = active_session
            servicemanager.LogInfoMsg(
                f"Helper avviato nella sessione {active_session} (PID {pid})"
            )
        except Exception as e:
            servicemanager.LogErrorMsg(f"Avvio helper fallito: {e}")

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        _enable_privileges()

        while True:
            self._ensure_helper_running()
            result = win32event.WaitForSingleObject(
                self.stop_event, CHECK_INTERVAL_S * 1000
            )
            if result == win32event.WAIT_OBJECT_0:
                break

        self._kill_helper()


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(HubInputService)
