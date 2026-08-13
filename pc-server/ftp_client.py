"""Client FTP per parlare con il server FTP homemade del telefono
(FtpServer.kt sull'app Android): stesso sottoinsieme minimale di comandi
(USER/PASS/PWD/CWD/PASV/LIST/RETR/STOR/QUIT), quindi il modulo standard
ftplib basta senza configurazioni speciali.

Ogni funzione apre una connessione nuova e la chiude alla fine invece di
tenerne una persistente: piu' semplice da usare da un server HTTP senza
stato (ogni richiesta della dashboard e' indipendente dalle altre) e il
server del telefono e' comunque pensato per un utilizzo occasionale, non
per un flusso continuo di richieste."""
import ftplib


def _connect(host, port, password, timeout=10):
    ftp = ftplib.FTP(timeout=timeout)
    ftp.connect(host, port, timeout=timeout)
    ftp.login(user="phone", passwd=password)
    return ftp


def _split_path(path):
    path = (path or "").strip("/")
    if "/" in path:
        directory, filename = path.rsplit("/", 1)
        return "/" + directory, filename
    return "", path


def _parse_list_line(line):
    """Il formato delle righe e' quello scritto da formatListLine() in
    FtpServer.kt: 'permessi 1 owner group dimensione MMM gg HH:MM nome'."""
    parts = line.split(None, 7)
    if len(parts) < 8:
        return None
    perm, _links, _owner, _group, size, _month, _day, rest = parts
    _time, _, filename = rest.partition(" ")
    if not filename:
        return None
    return {
        "name": filename,
        "is_dir": perm.startswith("d"),
        "size": int(size) if size.isdigit() else 0,
    }


def list_dir(host, port, password, path):
    ftp = _connect(host, port, password)
    try:
        if path:
            ftp.cwd(path)
        lines = []
        ftp.retrlines("LIST", lines.append)
        entries = [e for e in (_parse_list_line(l) for l in lines) if e]
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return entries
    finally:
        ftp.quit()


def download_to_stream(host, port, password, remote_path, out_stream):
    ftp = _connect(host, port, password)
    try:
        directory, filename = _split_path(remote_path)
        if directory:
            ftp.cwd(directory)
        ftp.retrbinary(f"RETR {filename}", out_stream.write)
    finally:
        ftp.quit()


def upload_from_stream(host, port, password, remote_path, in_stream):
    ftp = _connect(host, port, password)
    try:
        directory, filename = _split_path(remote_path)
        if directory:
            ftp.cwd(directory)
        ftp.storbinary(f"STOR {filename}", in_stream)
    finally:
        ftp.quit()
