"""Server FTP minimale sul PC: stesso sottoinsieme di comandi del server
FTP del telefono (FtpServer.kt) — cosi' la condivisione e' simmetrica e lo
stesso client (ftp_client.py, o un client FTP qualunque) parla con
entrambi. Solo modalita' passiva con porta dati fissa, niente
resume/MLSD/active mode: uso occasionale in LAN, non un server completo.

Differenza voluta rispetto al telefono: li' la condivisione espone
deliberatamente tutto lo storage, quindi un controllo di contenimento del
percorso approssimativo (confronto di stringhe sui path canonici) non
cambia nulla in pratica. Qui invece si condivide UNA cartella scelta, quindi
il contenimento va verificato per bene con Path.is_relative_to() — un
confronto di stringhe fallirebbe silenziosamente su cartelle sorelle con un
prefisso in comune (es. root "C:\\Cond" e un vicino "C:\\Condiviso2")."""
import socket
import threading
from datetime import datetime
from pathlib import Path

CONTROL_PORT = 2130
DATA_PORT = 2131


class PcFtpServer:
    def __init__(self, root, password="", read_only=False):
        self.root = Path(root).resolve()
        self.password = password
        self.read_only = read_only
        self._running = False
        self._server_socket = None
        self._thread = None

    @property
    def is_active(self):
        return self._running

    def start(self):
        if self._running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", CONTROL_PORT))
        sock.listen(5)
        self._server_socket = sock
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        try:
            if self._server_socket:
                self._server_socket.close()
        except OSError:
            pass
        self._server_socket = None

    def _within_root(self, path):
        try:
            return path.resolve().is_relative_to(self.root)
        except (OSError, RuntimeError):
            return False

    def _accept_loop(self):
        while self._running:
            try:
                conn, _addr = self._server_socket.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()

    def _resolve_path(self, frm, path):
        if not path:
            return frm
        node = self.root if path.startswith("/") else frm
        for segment in path.strip("/").split("/"):
            if not segment or segment == ".":
                continue
            if segment == "..":
                parent = node.parent
                node = parent if self._within_root(parent) else self.root
            else:
                candidate = node / segment
                if not self._within_root(candidate):
                    return None
                node = candidate
        return node

    @staticmethod
    def _format_list_line(entry):
        try:
            is_dir = entry.is_dir()
            size = 0 if is_dir else entry.stat().st_size
        except OSError:
            return None
        perm = "drwxr-xr-x" if is_dir else "-rw-r--r--"
        date = datetime.now().strftime("%b %d %H:%M")
        return f"{perm} 1 owner group {size} {date} {entry.name}"

    def _handle_client(self, sock):
        f = sock.makefile("rwb")
        authenticated = False
        current_dir = self.root
        passive_server = None

        def reply(line):
            f.write((line + "\r\n").encode())
            f.flush()

        def close_passive():
            nonlocal passive_server
            try:
                if passive_server:
                    passive_server.close()
            except OSError:
                pass
            passive_server = None

        try:
            reply("220 Connexus PC FTP")
            while True:
                line = f.readline()
                if not line:
                    break
                line = line.decode(errors="ignore").strip()
                if not line:
                    continue
                parts = line.split(" ", 1)
                cmd = parts[0].upper()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd == "USER":
                    reply("331 Password richiesta")
                elif cmd == "PASS":
                    authenticated = (self.password == "") or (arg == self.password)
                    reply("230 Accesso eseguito" if authenticated else "530 Password errata")
                elif cmd == "SYST":
                    reply("215 UNIX Type: L8")
                elif cmd == "FEAT":
                    reply("211 nessuna funzione extra\r\n211 End")
                elif cmd == "PWD":
                    rel = "" if current_dir == self.root else str(current_dir.relative_to(self.root)).replace("\\", "/")
                    reply(f'257 "/{rel}"')
                elif cmd == "TYPE":
                    reply("200 OK")
                elif cmd == "NOOP":
                    reply("200 OK")
                elif cmd == "QUIT":
                    reply("221 Ciao")
                    break
                elif cmd == "CDUP":
                    if not authenticated:
                        reply("530 Accesso negato")
                    else:
                        parent = current_dir.parent
                        current_dir = parent if self._within_root(parent) else self.root
                        reply("250 OK")
                elif cmd == "CWD":
                    if not authenticated:
                        reply("530 Accesso negato")
                    else:
                        target = self._resolve_path(current_dir, arg)
                        if target is not None and target.is_dir():
                            current_dir = target
                            reply("250 OK")
                        else:
                            reply("550 Cartella non trovata")
                elif cmd == "PASV":
                    if not authenticated:
                        reply("530 Accesso negato")
                    else:
                        close_passive()
                        dsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        dsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        dsock.bind(("0.0.0.0", DATA_PORT))
                        dsock.listen(1)
                        passive_server = dsock
                        ip = sock.getsockname()[0].replace(".", ",")
                        reply(f"227 Passive Mode ({ip},{DATA_PORT // 256},{DATA_PORT % 256})")
                elif cmd == "LIST":
                    if not authenticated:
                        reply("530 Accesso negato")
                    else:
                        reply("150 Invio elenco")
                        try:
                            dconn, _ = passive_server.accept()
                            try:
                                for entry in current_dir.iterdir():
                                    out = self._format_list_line(entry)
                                    if out:
                                        dconn.sendall((out + "\r\n").encode())
                            finally:
                                dconn.close()
                        except OSError:
                            pass
                        close_passive()
                        reply("226 Fine elenco")
                elif cmd == "RETR":
                    if not authenticated:
                        reply("530 Accesso negato")
                    else:
                        file_path = current_dir / arg
                        if not file_path.is_file() or not self._within_root(file_path):
                            reply("550 File non trovato")
                        else:
                            reply("150 Invio file")
                            try:
                                dconn, _ = passive_server.accept()
                                try:
                                    with open(file_path, "rb") as fp:
                                        while chunk := fp.read(65536):
                                            dconn.sendall(chunk)
                                finally:
                                    dconn.close()
                            except OSError:
                                pass
                            close_passive()
                            reply("226 Trasferimento completato")
                elif cmd == "STOR":
                    if not authenticated:
                        reply("530 Accesso negato")
                    elif self.read_only:
                        reply("550 Condivisione in sola lettura")
                    else:
                        target = current_dir / arg
                        if not self._within_root(target.parent):
                            reply("550 Percorso non valido")
                        else:
                            reply("150 Ricezione file")
                            try:
                                dconn, _ = passive_server.accept()
                                try:
                                    with open(target, "wb") as fp:
                                        while True:
                                            chunk = dconn.recv(65536)
                                            if not chunk:
                                                break
                                            fp.write(chunk)
                                finally:
                                    dconn.close()
                            except OSError:
                                pass
                            close_passive()
                            reply("226 Trasferimento completato")
                else:
                    reply("502 Comando non supportato")
        except OSError:
            pass
        finally:
            close_passive()
            try:
                sock.close()
            except OSError:
                pass
