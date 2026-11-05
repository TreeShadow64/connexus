import socket
import threading
import unittest

from files import ftp_client


def _fake_server_without_tls(port):
    """FTP minimale come quello del telefono: rifiuta AUTH TLS con 502."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(5)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            f = conn.makefile("rwb")
            f.write(b"220 finto\r\n"); f.flush()
            for raw in f:
                cmd = raw.decode().strip().upper()
                if cmd.startswith("AUTH"):
                    reply = "502 Comando non supportato"
                elif cmd.startswith("USER"):
                    reply = "331 password?"
                elif cmd.startswith("PASS"):
                    reply = "230 ok"
                elif cmd.startswith("PWD"):
                    reply = '257 "/"'
                elif cmd.startswith("QUIT"):
                    f.write(b"221 ciao\r\n"); f.flush(); break
                else:
                    reply = "502 no"
                f.write((reply + "\r\n").encode()); f.flush()
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv


class FtpClientTest(unittest.TestCase):
    def test_server_senza_tls_ripiega_sul_chiaro(self):
        srv = _fake_server_without_tls(39221)
        try:
            ftp = ftp_client._connect("127.0.0.1", 39221, "")
            self.assertEqual(ftp.pwd(), "/")
            ftp.close()
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
