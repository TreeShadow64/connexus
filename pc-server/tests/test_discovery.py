import json
import socket
import time
import unittest

from core import discovery


class DiscoveryTest(unittest.TestCase):
    def test_risponde_alla_ricerca_con_nome_indirizzo_e_porta(self):
        port = 38774
        discovery.start(port)
        time.sleep(0.3)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.sendto(discovery.MAGIC, ("127.0.0.1", port))
        data, _ = s.recvfrom(512)
        reply = json.loads(data)
        self.assertEqual(reply["app"], "connexus")
        self.assertEqual(reply["port"], 8765)
        self.assertTrue(reply["ip"])
        self.assertTrue(reply["name"])
        self.assertNotIn("token", reply)             # il token non viaggia mai in chiaro nel broadcast

    def test_ignora_messaggi_estranei(self):
        port = 38775
        discovery.start(port)
        time.sleep(0.3)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.7)
        s.sendto(b"ciao", ("127.0.0.1", port))
        with self.assertRaises(socket.timeout):
            s.recvfrom(512)


if __name__ == "__main__":
    unittest.main()
