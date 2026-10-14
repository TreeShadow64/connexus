"""Secondo schermo via WebRTC: video e audio verso la TV con ~0,1 s di ritardo.

Il flusso MP4/Media Source ha un limite del lettore della TV: dopo ogni avvio o
interruzione aspetta 1,0 s di video prima di mostrarlo, qualunque sia il bitrate.
WebRTC non ha quella soglia (sulla TV misurati ~25 ms di riproduzione).

Gira dentro il processo di cattura (e' li' che nascono i fotogrammi): un thread con
un loop asyncio ospita aiortc, il ciclo di cattura gli passa l'ultimo fotogramma e
l'audio di sistema. La segnalazione (offerta/risposta SDP) arriva dal processo
principale, che la riceve via HTTP dalla pagina della TV."""
import asyncio
import fractions
import threading
import time

AUDIO_RATE = 48000
AUDIO_FRAME = 960                 # 20 ms a 48 kHz
AUDIO_MAX_BUFFER = AUDIO_RATE // 5 * 4   # al massimo 200 ms (s16 stereo) in coda
FPS = 30

_patched = False


def _patch_h264_encoder(crf, maxrate_kbps):
    """L'encoder H.264 di aiortc usa preset 'medium', livello 3.1 (max 720p) e un
    bitrate che il ricevitore fa crollare a ~0,5 Mbit/s: lo si sostituisce con uno
    'ultrafast' a qualita' costante limitata, adatto al desktop a 1080p."""
    global _patched
    if _patched:
        return
    _patched = True
    import av
    from aiortc.codecs.h264 import H264Encoder

    def _encode_frame(self, frame, force_keyframe):
        if self.codec and (frame.width != self.codec.width or frame.height != self.codec.height):
            self.buffer_data = b""
            self.buffer_pts = None
            self.codec = None
        frame.pict_type = (av.video.frame.PictureType.I if force_keyframe
                           else av.video.frame.PictureType.NONE)
        if self.codec is None:
            self.codec = av.CodecContext.create("libx264", "w")
            self.codec.width, self.codec.height = frame.width, frame.height
            self.codec.pix_fmt = "yuv420p"
            self.codec.framerate = fractions.Fraction(FPS, 1)
            self.codec.time_base = fractions.Fraction(1, FPS)
            self.codec.options = {
                "level": "42", "preset": "ultrafast", "tune": "zerolatency",
                "crf": str(crf), "maxrate": f"{maxrate_kbps}k", "bufsize": f"{maxrate_kbps // 10}k",
                # intra-refresh: niente fotogrammi chiave giganti (a raffica sul Wi-Fi),
                # lo schermo si rinfresca a bande: il ritardo di riproduzione resta basso
                "x264-params": "keyint=60:min-keyint=60:scenecut=0:bframes=0:intra-refresh=1",
            }
            self.codec.profile = "Baseline"
        data = b""
        for packet in self.codec.encode(frame):
            data += bytes(packet)
        if data:
            yield from self._split_bitstream(data)

    H264Encoder._encode_frame = _encode_frame
    # bitrate non regolato dal ricevitore (REMB): la qualita' la decide il CRF
    H264Encoder.target_bitrate = property(lambda self: maxrate_kbps * 1000, lambda self, value: None)


class RtcHub:
    def __init__(self, width, height, log=None, crf=24, maxrate_kbps=12000, with_audio=True):
        _patch_h264_encoder(crf, maxrate_kbps)
        self.width, self.height = width, height
        self._log = log or (lambda message: None)
        self._with_audio = with_audio
        self._latest = None
        self._frame_lock = threading.Lock()
        self._audio = bytearray()
        self._audio_lock = threading.Lock()
        self._resampler = None
        self._resampler_key = None
        self._pcs = set()
        self.connected = False
        try:    # timer di Windows a 1 ms (di serie ~15 ms): pacing regolare di video e audio
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="rtc", daemon=True)
        self._thread.start()

    # --- ingresso dal ciclo di cattura ---

    def push_video(self, bgr):
        """Ultimo fotogramma BGR (altezza x larghezza x 3), gia' alla risoluzione di uscita."""
        with self._frame_lock:
            self._latest = bgr

    def push_audio(self, data, rate, channels):
        """PCM s16 interlacciato dalla cattura 'loopback' di Windows."""
        if not self._with_audio or not self.connected:
            return
        try:
            import av
            import numpy as np

            arr = np.frombuffer(data, dtype=np.int16)
            if channels != 2:
                arr = arr.reshape(-1, channels)[:, :2].reshape(-1)
            key = (rate,)
            if self._resampler_key != key:
                self._resampler = av.AudioResampler(format="s16", layout="stereo", rate=AUDIO_RATE)
                self._resampler_key = key
            frame = av.AudioFrame.from_ndarray(arr.reshape(1, -1), format="s16", layout="stereo")
            frame.sample_rate = rate
            out = bytearray()
            for resampled in self._resampler.resample(frame):
                out += bytes(resampled.planes[0])[: resampled.samples * 4]
            with self._audio_lock:
                self._audio += out
                if len(self._audio) > AUDIO_MAX_BUFFER:      # chi e' in ritardo salta il vecchio
                    del self._audio[: len(self._audio) - AUDIO_MAX_BUFFER]
        except Exception as e:
            self._log(f"audio WebRTC: {type(e).__name__}: {e}")

    def _take_audio(self, samples):
        need = samples * 4
        with self._audio_lock:
            chunk = bytes(self._audio[:need])
            del self._audio[:need]
        return chunk + b"\x00" * (need - len(chunk))     # silenzio se manca

    # --- WebRTC ---

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def offer(self, sdp, sdp_type, timeout=15):
        """Riceve l'offerta SDP della TV e ritorna {'sdp','type'} della risposta."""
        future = asyncio.run_coroutine_threadsafe(self._handle_offer(sdp, sdp_type), self._loop)
        return future.result(timeout=timeout)

    async def _handle_offer(self, sdp, sdp_type):
        from aiortc import (MediaStreamTrack, RTCConfiguration, RTCPeerConnection,
                            RTCRtpSender, RTCSessionDescription, VideoStreamTrack)
        import av

        hub = self

        class ScreenTrack(VideoStreamTrack):
            async def recv(self):
                pts, time_base = await self.next_timestamp()
                with hub._frame_lock:
                    image = hub._latest
                if image is None:
                    import numpy as np
                    image = np.zeros((hub.height, hub.width, 3), dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(image, format="bgr24")
                frame.pts, frame.time_base = pts, time_base
                return frame

        class SystemAudioTrack(MediaStreamTrack):
            kind = "audio"

            def __init__(self):
                super().__init__()
                self._start = None
                self._count = 0

            async def recv(self):
                if self._start is None:
                    self._start = time.time()
                wait = self._start + (self._count + AUDIO_FRAME) / AUDIO_RATE - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                frame = av.AudioFrame(format="s16", layout="stereo", samples=AUDIO_FRAME)
                frame.planes[0].update(hub._take_audio(AUDIO_FRAME))
                frame.sample_rate = AUDIO_RATE
                frame.pts = self._count
                frame.time_base = fractions.Fraction(1, AUDIO_RATE)
                self._count += AUDIO_FRAME
                return frame

        # una sola TV alla volta: una nuova offerta sostituisce la precedente
        for old in list(self._pcs):
            await old.close()
        self._pcs.clear()
        self.connected = False

        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))     # rete locale: niente STUN
        self._pcs.add(pc)

        @pc.on("connectionstatechange")
        async def _on_state():
            state = pc.connectionState
            self._log(f"WebRTC: {state}")
            self.connected = state == "connected"
            if state in ("failed", "closed"):
                self._pcs.discard(pc)

        video = pc.addTransceiver(ScreenTrack(), direction="sendonly")
        caps = RTCRtpSender.getCapabilities("video").codecs
        h264 = [c for c in caps if c.mimeType == "video/H264"]
        if h264:                                  # H.264 per primo: la TV lo decodifica in hardware
            video.setCodecPreferences(h264 + [c for c in caps if c.mimeType != "video/H264"])
        if self._with_audio:
            pc.addTransceiver(SystemAudioTrack(), direction="sendonly")

        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
        await pc.setLocalDescription(await pc.createAnswer())
        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    def close(self):
        async def _close():
            for pc in list(self._pcs):
                await pc.close()
            self._pcs.clear()
        try:
            asyncio.run_coroutine_threadsafe(_close(), self._loop).result(timeout=5)
        except Exception:
            pass
        self.connected = False
