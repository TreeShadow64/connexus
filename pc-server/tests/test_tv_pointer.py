"""PointerSession contro una finta TV locale (SSAP + socket del puntatore)."""
import asyncio
import json
import unittest

import websockets

from casting import webos_remote


class PointerSessionTest(unittest.TestCase):
    def test_movimento_clic_scroll_su_un_solo_collegamento(self):
        received = []
        pointer_connections = []

        async def pointer_handler(ws):
            pointer_connections.append(1)
            async for message in ws:
                received.append(message)

        async def ssap_handler(ws):
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "register":
                    await ws.send(json.dumps({"type": "registered", "id": msg["id"]}))
                elif msg["type"] == "request":
                    await ws.send(json.dumps({"type": "response", "id": msg["id"],
                                              "payload": {"socketPath": "ws://127.0.0.1:38999/"}}))

        async def run():
            old_port = webos_remote.SSAP_PORT
            webos_remote.SSAP_PORT = 38000
            try:
                async with websockets.serve(ssap_handler, "127.0.0.1", 38000), \
                        websockets.serve(pointer_handler, "127.0.0.1", 38999):
                    session = webos_remote.PointerSession()
                    for _ in range(5):
                        await session.move("127.0.0.1", "chiave", 7, -3)
                    await session.click("127.0.0.1", "chiave")
                    await session.scroll("127.0.0.1", "chiave", -2)
                    await asyncio.sleep(0.3)
                    await session.close()
            finally:
                webos_remote.SSAP_PORT = old_port

        asyncio.run(run())
        self.assertEqual(len(pointer_connections), 1)       # un solo collegamento riusato
        self.assertEqual(received[0], "type:move\ndx:7\ndy:-3\ndown:0\n\n")
        self.assertIn("type:click\n\n", received)
        self.assertIn("type:scroll\ndx:0\ndy:-2\n\n", received)

    def test_senza_abbinamento_errore_chiaro(self):
        async def run():
            await webos_remote.PointerSession().move("127.0.0.1", None, 1, 1)

        with self.assertRaises(webos_remote.WebOSError):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
