"""Prova locale del WebRTC verso la TV: un client aiortc nello stesso processo fa le
veci della pagina della TV (nessuna TV e nessun monitor coinvolti)."""
import asyncio
import threading
import time
import unittest

try:
    import aiortc  # noqa: F401
    HAVE_AIORTC = True
except ImportError:
    HAVE_AIORTC = False


@unittest.skipUnless(HAVE_AIORTC, "aiortc non installato")
class RtcHubTest(unittest.TestCase):
    def test_video_e_audio_arrivano_al_client(self):
        import numpy as np
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        from streaming.rtc_peer import RtcHub

        width, height = 640, 360
        hub = RtcHub(width, height, with_audio=True, crf=30, maxrate_kbps=3000)
        stop = threading.Event()

        def feed():
            n = 0
            tone = (np.sin(np.arange(1024) / 20.0) * 8000).astype(np.int16)
            stereo = np.stack([tone, tone], axis=1).reshape(-1).tobytes()
            while not stop.is_set():
                frame = np.full((height, width, 3), (n * 3) % 255, dtype=np.uint8)
                frame[:, (n * 7) % width:(n * 7) % width + 40] = 255
                hub.push_video(frame)
                hub.push_audio(stereo, 44100, 2)
                n += 1
                time.sleep(1 / 30)

        threading.Thread(target=feed, daemon=True).start()

        async def client():
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            pc.addTransceiver("video", direction="recvonly")
            pc.addTransceiver("audio", direction="recvonly")
            tracks = {}

            @pc.on("track")
            def on_track(track):
                tracks[track.kind] = track

            await pc.setLocalDescription(await pc.createOffer())
            loop = asyncio.get_running_loop()
            answer = await loop.run_in_executor(
                None, hub.offer, pc.localDescription.sdp, pc.localDescription.type)
            await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

            async def read(kind, count):
                got = []
                for _ in range(count):
                    got.append(await asyncio.wait_for(tracks[kind].recv(), timeout=10))
                return got

            deadline = time.time() + 10
            while len(tracks) < 2 and time.time() < deadline:
                await asyncio.sleep(0.1)
            self.assertEqual(set(tracks), {"video", "audio"})
            video = await read("video", 15)
            audio = await read("audio", 15)
            await pc.close()
            return video, audio

        try:
            video, audio = asyncio.run(client())
        finally:
            stop.set()
            hub.close()

        self.assertEqual((video[-1].width, video[-1].height), (width, height))
        self.assertGreaterEqual(len(audio), 15)
        self.assertTrue(hub.connected is False)         # dopo close() non e' piu' collegato


if __name__ == "__main__":
    unittest.main()
