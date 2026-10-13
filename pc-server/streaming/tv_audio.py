"""Audio del PC verso la TV: cattura "quello che senti" (loopback WASAPI della
scheda audio predefinita) e lo distribuisce come PCM a 16 bit ai browser delle TV.

La cattura parte al primo ascoltatore e si ferma quando se ne va l'ultimo. Il
volume di Windows non influisce sulla cattura: si puo' abbassare il PC e
sentire tutto a volume pieno dalla TV."""
import logging
import queue
import threading

log = logging.getLogger("hub-server")


class TvAudio:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = []
        self._pa = None
        self._stream = None
        self.rate = 0
        self.channels = 2
        self.device_name = ""

    # --- cattura ---

    def _open(self):
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            loopback = None
            for dev in pa.get_loopback_device_info_generator():
                if speakers["name"] in dev["name"]:
                    loopback = dev
                    break
            if loopback is None:
                raise RuntimeError("nessuna uscita audio con cattura disponibile")

            channels = int(loopback["maxInputChannels"])
            rate = int(loopback["defaultSampleRate"])

            def on_audio(in_data, frame_count, time_info, status):
                data = in_data
                if channels != 2:
                    import numpy as np
                    arr = np.frombuffer(in_data, dtype=np.int16).reshape(-1, channels)
                    data = arr[:, :2].tobytes()
                self._distribute(data)
                return (None, pyaudio.paContinue)

            stream = pa.open(
                format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                input_device_index=loopback["index"], frames_per_buffer=1024,
                stream_callback=on_audio,
            )
        except Exception:
            pa.terminate()
            raise
        self._pa, self._stream = pa, stream
        self.rate, self.channels, self.device_name = rate, channels, loopback["name"]
        log.info(f"Audio verso la TV: cattura di {self.device_name} a {rate} Hz")

    def _close(self):
        stream, pa = self._stream, self._pa
        self._stream = self._pa = None
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        finally:
            if pa is not None:
                pa.terminate()
        log.info("Audio verso la TV: cattura fermata")

    def _distribute(self, data):
        for q in list(self._subs):
            try:
                q.put_nowait(data)
            except queue.Full:
                try:  # chi e' in ritardo salta i pezzi vecchi invece di accumulare
                    q.get_nowait()
                    q.put_nowait(data)
                except queue.Empty:
                    pass

    # --- ascoltatori ---

    def subscribe(self):
        """Ritorna (coda, frequenza). Solleva RuntimeError se non c'e' cattura."""
        with self._lock:
            if self._stream is None:
                self._open()
            q = queue.Queue(maxsize=40)  # ~1 secondo
            self._subs.append(q)
            return q, self.rate

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)
            if not self._subs and self._stream is not None:
                self._close()

    def stop_all(self):
        with self._lock:
            self._subs.clear()
            if self._stream is not None:
                self._close()

    @property
    def listeners(self):
        return len(self._subs)

    def silence(self):
        """10 ms di silenzio: tiene viva la connessione quando non suona nulla
        (la cattura WASAPI non produce dati durante il silenzio)."""
        return b"\x00" * (self.rate // 100 * 4)


tv_audio = TvAudio()
