"""Server FTP minimale sul PC: stesso sottoinsieme di comandi del server
FTP del telefono (FtpServer.kt) — cosi' la condivisione e' simmetrica e lo
stesso client (ftp_client.py, o un client FTP qualunque) parla con
entrambi. Uso occasionale in LAN, non un server completo — ma con un
minimo di irrobustimento (Fase 3):
  - range di porte dati invece di una fissa, cosi' due trasferimenti
    contemporanei (es. due telefoni, o lista+download insieme) non si
    scavalcano sulla stessa porta;
  - REST per riprendere un trasferimento interrotto invece di ripartire
    sempre da zero;
  - blocco temporaneo dopo troppi PASS sbagliati di fila dallo stesso IP;
  - AUTH TLS opzionale (FTPS esplicito): il client normale continua a
    funzionare in chiaro, chi supporta FTPS (ftp_client.py, l'app Android)
    puo' chiedere una connessione cifrata.

Differenza voluta rispetto al telefono: li' la condivisione espone
deliberatamente tutto lo storage, quindi un controllo di contenimento del
percorso approssimativo (confronto di stringhe sui path canonici) non
cambia nulla in pratica. Qui invece si condivide UNA cartella scelta, quindi
il contenimento va verificato per bene con Path.is_relative_to() — un
confronto di stringhe fallirebbe silenziosamente su cartelle sorelle con un
prefisso in comune (es. root "C:\\Cond" e un vicino "C:\\Condiviso2")."""
import socket
import ssl
import threading
import time
from datetime import datetime
from pathlib import Path

import ftp_tls

CONTROL_PORT = 2130
DATA_PORT_RANGE = range(2131, 2141)

# Protezione brute-force: condivisa da tutte le connessioni (per IP, non per
# singola sessione, altrimenti basterebbe riconnettersi per azzerare il
# contatore). In memoria e non persistente: un riavvio del PC/dell'app resetta
# i tentativi, accettabile per un uso occasionale in LAN.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
_failed_attempts = {}
_failed_attempts_lock = threading.Lock()


def _is_locked_out(ip):
    with _failed_attempts_lock:
        entry = _failed_attempts.get(ip)
        if not entry:
            return False
        count, first_attempt = entry
        if count < MAX_FAILED_ATTEMPTS:
            return False
        if time.time() - first_attempt >= LOCKOUT_SECONDS:
            del _failed_attempts[ip]
            return False
        return True


def _record_failed_attempt(ip):
    with _failed_attempts_lock:
        count, first_attempt = _failed_attempts.get(ip, (0, time.time()))
        if time.time() - first_attempt >= LOCKOUT_SECONDS:
            count, first_attempt = 0, time.time()
        _failed_attempts[ip] = (count + 1, first_attempt)


def _clear_failed_attempts(ip):
    with _failed_attempts_lock:
        _failed_attempts.pop(ip, None)


class PcFtpServer:
    def __init__(self, root, password="", read_only=False):
        self.root = Path(root).resolve()
        self.password = password
        self.read_only = read_only
        self._running = False
        self._server_socket = None
        self._thread = None
        self._ssl_context = ftp_tls.get_server_context()

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
                conn, addr = self._server_socket.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(conn, addr[0]), daemon=True).start()

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

    def _open_passive(self):
        """Prova le porte del range in ordine finche' una e' libera: con un
        solo trasferimento alla volta (il caso comune) e' quasi sempre la
        prima, ma permette a due connessioni di sovrapporsi senza scontrarsi.

        Niente SO_REUSEADDR qui (a differenza del socket di controllo): su
        Windows quell'opzione e' molto piu' permissiva che su Linux e puo'
        lasciar bindare una seconda socket sulla stessa porta di una gia' in
        ascolto invece di sollevare OSError — proprio il controllo su cui si
        basa questo ciclo per capire se una porta e' libera."""
        for port in DATA_PORT_RANGE:
            dsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                dsock.bind(("0.0.0.0", port))
            except OSError:
                dsock.close()
                continue
            dsock.listen(1)
            return dsock, port
        return None, None

    def _handle_client(self, sock, client_ip):
        f = sock.makefile("rwb")
        authenticated = False
        current_dir = self.root
        passive_server = None
        restart_offset = 0
        tls_active = False
        protect_data = False

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

        def accept_data():
            """Accetta la connessione dati in attesa su passive_server, cifrandola
            se il client ha chiesto PROT P dopo AUTH TLS."""
            dconn, _ = passive_server.accept()
            if protect_data and self._ssl_context is not None:
                dconn = self._ssl_context.wrap_socket(dconn, server_side=True)
            return dconn

        def close_data(dconn):
            """Su TLS va chiuso con un unwrap() pulito (scambio di close_notify):
            un close() secco lascia il client in attesa dello shutdown TLS e
            ftplib lo segnala come SSLError('SHUTDOWN_WHILE_IN_INIT')."""
            try:
                if isinstance(dconn, ssl.SSLSocket):
                    dconn.unwrap()
            except (OSError, ssl.SSLError):
                pass
            try:
                dconn.close()
            except OSError:
                pass

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
                    if _is_locked_out(client_ip):
                        reply("530 Troppi tentativi falliti, riprova tra qualche minuto")
                    else:
                        authenticated = (self.password == "") or (arg == self.password)
                        if authenticated:
                            _clear_failed_attempts(client_ip)
                            reply("230 Accesso eseguito")
                        else:
                            _record_failed_attempt(client_ip)
                            reply("530 Password errata")
                elif cmd == "SYST":
                    reply("215 UNIX Type: L8")
                elif cmd == "FEAT":
                    feat_lines = ["211-Funzioni supportate", " REST STREAM"]
                    if self._ssl_context is not None:
                        feat_lines.append(" AUTH TLS")
                        feat_lines.append(" PBSZ")
                        feat_lines.append(" PROT")
                    feat_lines.append("211 End")
                    reply("\r\n".join(feat_lines))
                elif cmd == "AUTH" and arg.upper() == "TLS":
                    if self._ssl_context is None:
                        reply("502 TLS non disponibile")
                    elif tls_active:
                        reply("234 Gia' su TLS")
                    else:
                        reply("234 AUTH TLS riuscito")
                        f.flush()
                        sock = self._ssl_context.wrap_socket(sock, server_side=True)
                        f = sock.makefile("rwb")
                        tls_active = True
                elif cmd == "PBSZ":
                    reply("200 PBSZ=0")
                elif cmd == "PROT":
                    if arg.upper() == "P":
                        protect_data = True
                        reply("200 Canale dati protetto")
                    elif arg.upper() == "C":
                        protect_data = False
                        reply("200 Canale dati in chiaro")
                    else:
                        reply("504 Livello non supportato")
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
                elif cmd == "REST":
                    if not authenticated:
                        reply("530 Accesso negato")
                    elif not arg.isdigit():
                        reply("501 Offset non valido")
                    else:
                        restart_offset = int(arg)
                        reply(f"350 Riprendi da {restart_offset}")
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
                        dsock, port = self._open_passive()
                        if dsock is None:
                            reply("425 Nessuna porta dati disponibile")
                        else:
                            passive_server = dsock
                            ip = sock.getsockname()[0].replace(".", ",")
                            reply(f"227 Passive Mode ({ip},{port // 256},{port % 256})")
                elif cmd == "LIST":
                    if not authenticated:
                        reply("530 Accesso negato")
                    else:
                        reply("150 Invio elenco")
                        try:
                            dconn = accept_data()
                            try:
                                for entry in current_dir.iterdir():
                                    out = self._format_list_line(entry)
                                    if out:
                                        dconn.sendall((out + "\r\n").encode())
                            finally:
                                close_data(dconn)
                        except OSError:
                            pass
                        close_passive()
                        reply("226 Fine elenco")
                elif cmd == "RETR":
                    if not authenticated:
                        reply("530 Accesso negato")
                    else:
                        file_path = current_dir / arg
                        offset = restart_offset
                        restart_offset = 0
                        if not file_path.is_file() or not self._within_root(file_path):
                            reply("550 File non trovato")
                        else:
                            reply("150 Invio file")
                            try:
                                dconn = accept_data()
                                try:
                                    with open(file_path, "rb") as fp:
                                        if offset:
                                            fp.seek(offset)
                                        while chunk := fp.read(65536):
                                            dconn.sendall(chunk)
                                finally:
                                    close_data(dconn)
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
                        offset = restart_offset
                        restart_offset = 0
                        if not self._within_root(target.parent):
                            reply("550 Percorso non valido")
                        else:
                            reply("150 Ricezione file")
                            try:
                                dconn = accept_data()
                                try:
                                    # "r+b" per riprendere (serve il file gia' presente
                                    # e non lo tronca), "wb" da zero se non c'e' offset.
                                    mode = "r+b" if offset and target.exists() else "wb"
                                    with open(target, mode) as fp:
                                        if offset:
                                            fp.seek(offset)
                                        while True:
                                            chunk = dconn.recv(65536)
                                            if not chunk:
                                                break
                                            fp.write(chunk)
                                finally:
                                    close_data(dconn)
                            except OSError:
                                pass
                            close_passive()
                            reply("226 Trasferimento completato")
                else:
                    reply("502 Comando non supportato")
        except (OSError, ssl.SSLError):
            pass
        finally:
            close_passive()
            try:
                sock.close()
            except OSError:
                pass
