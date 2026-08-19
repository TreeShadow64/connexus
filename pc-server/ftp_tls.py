"""Certificato TLS auto-firmato per AUTH TLS (FTPS esplicito) sul server FTP
del PC. Generato una sola volta e salvato in app_dir(), cosi' resta lo
stesso tra un riavvio e l'altro invece di cambiare ogni volta.

Nessuna autorita' di certificazione: e' un uso LAN-only tra dispositivi
della stessa persona/famiglia, lo scopo e' cifrare il canale contro chi
origlia sulla stessa rete, non autenticare un'identita' verificabile da
terzi — lo stesso principio di fiducia "LAN = fidata" gia' usato altrove
in questa funzione (nessuna password obbligatoria di default, nessun
controllo sulla provenienza dell'IP oltre al blocco brute-force)."""
import logging
import ssl
from pathlib import Path

from paths import app_dir

log = logging.getLogger("hub-server")

CERT_PATH = app_dir() / "ftp_cert.pem"
KEY_PATH = app_dir() / "ftp_key.pem"


def _generate_self_signed_cert():
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Connexus PC FTP")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    log.info("FTP: generato nuovo certificato TLS auto-firmato")


def get_server_context():
    """SSLContext pronto per AUTH TLS, o None se 'cryptography' non e'
    disponibile o la generazione fallisce: la condivisione deve continuare
    a funzionare in chiaro anche senza, non bloccarsi."""
    try:
        if not CERT_PATH.exists() or not KEY_PATH.exists():
            _generate_self_signed_cert()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(CERT_PATH), str(KEY_PATH))
        return context
    except Exception as e:
        log.warning(f"FTP: TLS non disponibile ({e}), la condivisione restera' solo in chiaro")
        return None
