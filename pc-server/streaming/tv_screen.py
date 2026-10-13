"""La TV come secondo schermo.

Il PC crea un monitor virtuale (driver Parsec), lo cattura e lo serve come
flusso MJPEG su HTTP: la TV lo apre con il suo browser web. Windows lo tratta
come un vero secondo monitor, quindi basta trascinarci sopra le finestre.

Il flusso e' protetto da una chiave casuale valida solo per la sessione: chi
non la conosce non puo' vedere lo schermo."""
import logging
import secrets
import threading

from streaming.screen_stream import ScreenStreamer

log = logging.getLogger("hub-server")

# La TV decodifica ogni fotogramma con la sua CPU, che e' lenta: meno pixel =
# meno ritardo. "fluido" e' il predefinito, "nitido" e' per immagini ferme.
PROFILES = {
    # video H.264 decodificato dal chip della TV (consigliato); JPEG a 1280 px come ripiego
    "video": {"fps": 15, "quality": 60, "max_width": 1280, "h264": (1920, 30, 26)},
    "video720": {"fps": 15, "quality": 60, "max_width": 1280, "h264": (1280, 30, 26)},
    "video24": {"fps": 15, "quality": 60, "max_width": 1280, "h264": (1280, 24, 26)},
    "fluido": {"fps": 15, "quality": 60, "max_width": 1280},
    "nitido": {"fps": 12, "quality": 75, "max_width": 1920},
    "leggero": {"fps": 12, "quality": 55, "max_width": 960},
}
DEFAULT_PROFILE = "video"


class Fmp4Assembler:
    """Ricompone il flusso MP4 frammentato in: segmento iniziale (ftyp+moov) e
    frammenti (moof+mdat). Ogni frammento comincia con un fotogramma chiave, quindi
    chi si collega a meta' puo' partire dal frammento successivo."""

    def __init__(self, on_init, on_fragment):
        self._buf = bytearray()
        self._on_init = on_init
        self._on_fragment = on_fragment
        self._ftyp = None
        self._moof = None

    def feed(self, data):
        self._buf += data
        while len(self._buf) >= 8:
            size = int.from_bytes(self._buf[0:4], "big")
            kind = bytes(self._buf[4:8])
            if size < 8:
                self._buf.clear()          # flusso incoerente: si riparte
                return
            if len(self._buf) < size:
                return
            box = bytes(self._buf[:size])
            del self._buf[:size]
            if kind == b"ftyp":
                self._ftyp = box
            elif kind == b"moov" and self._ftyp is not None:
                self._on_init(self._ftyp + box, _codec_from_moov(box))
            elif kind == b"moof":
                self._moof = box
            elif kind == b"mdat" and self._moof is not None:
                self._on_fragment(self._moof + box)
                self._moof = None


def _codec_from_moov(moov):
    """Stringa codec 'avc1.PPCCLL' presa dal box avcC (profilo, vincoli, livello)."""
    i = moov.find(b"avcC")
    if i < 0 or len(moov) < i + 8:
        return "avc1.42C028"
    return "avc1.%02X%02X%02X" % (moov[i + 5], moov[i + 6], moov[i + 7])


class TvScreen:
    def __init__(self):
        self._streamer = None
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._latest = None
        self._seq = 0
        self._viewers = 0
        self._key = None
        self._profile = DEFAULT_PROFILE
        self._client_stats = {}
        self.last_client_fps = 0.0
        self.last_served = {}
        self._video_init = None
        self.codec = None
        self._video_subs = []
        self._assembler = None

    # --- stato ---

    @property
    def active(self):
        return self._streamer is not None

    @property
    def viewers(self):
        return self._viewers

    @property
    def key(self):
        return self._key

    @property
    def geometry(self):
        streamer = self._streamer
        return streamer.geometry if streamer is not None else None

    def check_key(self, candidate):
        return bool(self._key) and bool(candidate) and secrets.compare_digest(self._key, str(candidate))

    # --- ciclo di vita ---

    def _on_frame(self, jpeg):
        with self._cond:
            self._latest = jpeg
            self._seq += 1
            self._cond.notify_all()

    @property
    def profile(self):
        return self._profile

    def start(self, profile=None):
        """Avvia monitor virtuale e cattura. Ritorna la geometria; solleva
        RuntimeError se il driver non e' disponibile. Se e' gia' attivo con un
        altro profilo lo riavvia (la chiave resta: la TV si riconnette da sola)."""
        profile = profile if profile in PROFILES else (profile or self._profile or DEFAULT_PROFILE)
        if profile not in PROFILES:
            profile = DEFAULT_PROFILE
        with self._lock:
            if self._streamer is not None:
                if profile == self._profile:
                    return self._streamer.geometry
                old, self._streamer = self._streamer, None
                old.stop()
            cfg = PROFILES[profile]
            streamer = ScreenStreamer(target_fps=cfg["fps"], jpeg_quality=cfg["quality"],
                                      max_width=cfg["max_width"], virtual=True,
                                      h264=cfg.get("h264"))
            self._video_init = None
            self.codec = None
            self._assembler = Fmp4Assembler(self._on_video_init, self._on_video_fragment) \
                if cfg.get("h264") else None
            streamer.start(self._on_frame, self._on_video if self._assembler else None)
            self._streamer = streamer
            self._profile = profile
            if not self._key:
                self._key = secrets.token_urlsafe(12)
            self._latest = None
            log.info(f"Secondo schermo TV attivo (profilo {profile})")
            return streamer.geometry

    def stop(self):
        with self._lock:
            streamer, self._streamer = self._streamer, None
            self._key = None
        if streamer is not None:
            streamer.stop()
            log.info("Secondo schermo TV fermato")
        self._video_init = None
        self.codec = None
        self._assembler = None
        self._video_subs.clear()
        with self._cond:
            self._latest = None
            self._cond.notify_all()

    # --- video H.264 ---

    def _on_video(self, chunk):
        assembler = self._assembler
        if assembler is not None:
            assembler.feed(chunk)

    def _on_video_init(self, init, codec):
        with self._cond:
            self._video_init = init
            self.codec = codec
            self._cond.notify_all()
        log.info(f"Video H.264 pronto per la TV ({codec})")

    def _on_video_fragment(self, fragment):
        for q in list(self._video_subs):
            try:
                q.put_nowait(fragment)
            except Exception:
                try:                       # chi e' in ritardo salta i frammenti vecchi
                    q.get_nowait()
                    q.put_nowait(fragment)
                except Exception:
                    pass

    @property
    def video_ready(self):
        return self._video_init is not None

    def wait_video_init(self, timeout=10.0):
        with self._cond:
            if self._video_init is None and self.active:
                self._cond.wait(timeout=timeout)
            return self._video_init

    def video_subscribe(self):
        import queue
        q = queue.Queue(maxsize=24)         # ~5 secondi di frammenti
        self._video_subs.append(q)
        return q

    def video_unsubscribe(self, q):
        if q in self._video_subs:
            self._video_subs.remove(q)

    def next_frame(self, after, timeout=2.0):
        """Ritorna (numero, jpeg) del fotogramma piu' recente se e' successivo a
        `after`; altrimenti attende fino a `timeout` e, se non e' cambiato niente,
        ritorna (after, None). Il visualizzatore chiede il fotogramma seguente solo
        quando ha finito di mostrare il precedente, quindi e' lui a dettare il
        ritmo e non si accumula ritardo."""
        with self._cond:
            if self._seq <= after and self.active:
                self._cond.wait(timeout=timeout)
            if self._latest is None or self._seq <= after:
                return after, None
            return self._seq, self._latest

    def note_request(self, ip, nbytes):
        """Statistiche per client: ogni 5 secondi scrive nel log quanti
        fotogrammi al secondo la TV chiede e riesce quindi a mostrare."""
        import time
        now = time.time()
        with self._cond:
            st = self._client_stats.setdefault(ip, {"t": now, "n": 0, "b": 0})
            st["n"] += 1
            st["b"] += nbytes
            if now - st["t"] >= 5:
                fps = st["n"] / (now - st["t"])
                kb = st["b"] / max(1, st["n"]) / 1024
                self.last_client_fps = round(fps, 1)
                log.info(f"TV {ip}: {fps:.1f} fotogrammi/s mostrati, {kb:.0f} KB l'uno")
                st["t"], st["n"], st["b"] = now, 0, 0

    # --- flusso per i visualizzatori ---

    def frames(self):
        """Generatore di fotogrammi JPEG per un visualizzatore. Rispedisce
        l'ultimo ogni 2 secondi se il desktop e' fermo (il browser della TV
        altrimenti chiude connessioni silenziose)."""
        with self._cond:
            self._viewers += 1
            seen = -1
        try:
            while self.active:
                with self._cond:
                    if self._seq == seen:
                        self._cond.wait(timeout=2.0)
                    frame, seq = self._latest, self._seq
                if frame is None:
                    continue
                seen = seq
                yield frame
        finally:
            with self._cond:
                self._viewers = max(0, self._viewers - 1)


tv_screen = TvScreen()

VIEWER_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Connexus</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html,body{margin:0;height:100%;background:#000;overflow:hidden}
img,video{position:fixed;inset:0;width:100%;height:100%;object-fit:contain;background:#000}
img.native{margin:auto;width:auto;height:auto;max-width:100%;max-height:100%}
.note{position:fixed;left:0;right:0;text-align:center;color:#6fe3ff;font:22px sans-serif;display:none}
#msg{top:45%}
#aud{top:16px}
#aud button{font:22px sans-serif;padding:10px 20px;color:#000;background:#6fe3ff;border:0}
#delay{bottom:16px;color:#fff;background:rgba(0,0,0,.6);width:max-content;margin:auto;padding:6px 14px}
</style></head><body>
<video id="v" autoplay muted playsinline></video>
<img id="s" alt="" style="display:none">
<div id="msg" class="note">Connexus: in attesa del secondo schermo...</div>
<div id="aud" class="note"><button id="audbtn">Premi OK per attivare l'audio</button></div>
<div id="delay" class="note"></div>
<script>
var KEY = "__KEY__", RATE = __RATE__;
var img = document.getElementById("s"), msg = document.getElementById("msg");
var aud = document.getElementById("aud"), delayBox = document.getElementById("delay");

/* ---- video ---- */
var video = document.getElementById("v");
function tvlog(m){ try { new Image().src = "/tvlog?k=" + KEY + "&m=" + encodeURIComponent(m) + "&t=" + Date.now(); } catch (e) {} }

/* ripiego: immagini JPEG, una alla volta */
var frames = 0, dbg = false;
function pullDirect(){
  img.style.display = "block"; video.style.display = "none";
  img.onload = function(){ frames++; msg.style.display = "none"; pullDirect(); };
  img.onerror = function(){ msg.style.display = "block"; setTimeout(pullDirect, 1000); };
  img.src = "/tvframe.jpg?k=" + KEY + "&d=1&t=" + Date.now();
}

/* video H.264 con Media Source: il chip della TV lo decodifica */
var usedFallback = false, seeks = 0, waits = 0, lastT = 0, lastTotal = 0, lastDrop = 0, tick = 0;
function fallback(why){
  if (usedFallback) return;
  usedFallback = true; tvlog("ripiego JPEG: " + why); pullDirect();
}
function startMse(){
  if (!window.MediaSource || !window.fetch) return fallback("MediaSource/fetch non disponibili");
  fetch("/tvvideo.info?k=" + KEY).then(function(r){ return r.json(); }).then(function(info){
    var mime = 'video/mp4; codecs="' + info.codec + '"';
    var ok = MediaSource.isTypeSupported(mime);
    tvlog("supporto " + mime + " = " + ok);
    if (!ok) return fallback("codec non supportato");
    var ms = new MediaSource(), sb = null, queue = [], gotData = false;
    video.src = URL.createObjectURL(ms);
    video.style.display = "block"; img.style.display = "none";
    function pumpQueue(){
      if (!sb || sb.updating || !queue.length) return;
      try { sb.appendBuffer(queue.shift()); } catch (e) { tvlog("append: " + e.name); queue.length = 0; }
    }
    ms.addEventListener("sourceopen", function(){
      sb = ms.addSourceBuffer(mime);
      try { sb.mode = "sequence"; } catch (e) {}
      sb.addEventListener("updateend", function(){
        pumpQueue();
        /* resta vicino al vivo e libera il passato */
        try {
          var b = video.buffered;
          if (b.length && !sb.updating && video.currentTime - b.start(0) > 8) sb.remove(b.start(0), video.currentTime - 4);
          if (video.paused) video.play();
        } catch (e) {}
      });
      fetch("/tvvideo.mp4?k=" + KEY).then(function(res){
        var reader = res.body.getReader();
        (function pump(){
          reader.read().then(function(r){
            if (r.done){ tvlog("flusso video chiuso"); setTimeout(function(){ location.reload(); }, 1500); return; }
            if (!gotData){ gotData = true; tvlog("primi dati video"); }
            queue.push(r.value); pumpQueue(); pump();
          }).catch(function(e){ tvlog("lettura: " + e); setTimeout(function(){ location.reload(); }, 2000); });
        })();
      }).catch(function(e){ fallback("fetch: " + e); });
    });
    video.addEventListener("error", function(){ tvlog("errore video " + (video.error && video.error.code)); });
    video.addEventListener("waiting", function(){ waits++; });
    setTimeout(function(){ if (!gotData) fallback("nessun dato video in 10 s"); }, 10000);
    setInterval(function(){
      try {
        var bb = video.buffered;
        if (bb.length){
          var ahead = bb.end(bb.length - 1) - video.currentTime;
          if (ahead > 2.0){ video.currentTime = bb.end(bb.length - 1) - 0.3; seeks++; }
          else if (ahead > 0.6 && video.playbackRate !== 1.1) video.playbackRate = 1.1;     /* recupera piano piano */
          else if (ahead < 0.35 && video.playbackRate !== 1.0) video.playbackRate = 1.0;
        }
      } catch (e) {}
    }, 250);
    setInterval(function(){
      tick++;
      var q = video.getVideoPlaybackQuality ? video.getVideoPlaybackQuality() : null;
      var total = q ? q.totalVideoFrames : (video.webkitDecodedFrameCount || 0);
      var drop = q ? q.droppedVideoFrames : (video.webkitDroppedFrameCount || 0);
      frames = total - lastTotal;
      if (tick % 2 === 0){
        var ahead = video.buffered.length ? (video.buffered.end(video.buffered.length - 1) - video.currentTime) : -1;
        tvlog("t+" + tick + "s avanza " + (video.currentTime - lastT).toFixed(2) + "s/2s, fotogrammi " + (total - lastTotal) +
              ", scartati " + (drop - lastDrop) + ", buffer " + ahead.toFixed(2) + "s, rs " + video.readyState +
              ", salti " + seeks + ", attese " + waits + ", " + video.videoWidth + "x" + video.videoHeight);
        lastT = video.currentTime; lastTotal = total; lastDrop = drop;
      }
    }, 1000);
  }).catch(function(e){ fallback("info: " + e); });
}

var MODE = (location.search.match(/[?&]m=([0-9a-z]+)/) || [0, "v"])[1];
setInterval(function(){
  if (dbg){ delayBox.textContent = frames + " fps" + (MODE === "j" ? " (JPEG)" : ""); delayBox.style.display = "block"; }
  if (MODE === "j") frames = 0;
}, 1000);
if (MODE === "j") pullDirect(); else startMse();

/* ---- audio (Web Audio: PCM 16 bit stereo dal PC) ---- */
var ctx = null, nextTime = 0, delay = 0.35, carry = null;
function resetClock(){ nextTime = ctx.currentTime + 0.08 + delay; }
var pend = [], pendLen = 0, BLOCK = Math.round(RATE * 0.09) * 4;   /* blocchi da ~90 ms: meno lavoro per la TV */
function playChunk(bytes){
  if (carry && carry.length){ var m = new Uint8Array(carry.length + bytes.length); m.set(carry, 0); m.set(bytes, carry.length); bytes = m; carry = null; }
  pend.push(bytes); pendLen += bytes.length;
  if (pendLen < BLOCK) return;
  var all = new Uint8Array(pendLen), o = 0;
  for (var j = 0; j < pend.length; j++){ all.set(pend[j], o); o += pend[j].length; }
  pend = []; pendLen = 0;
  var frames = Math.floor(all.length / 4), use = frames * 4;
  if (use < all.length) carry = all.subarray(use);
  if (!frames) return;
  var i16 = new Int16Array(all.buffer, 0, use / 2);
  var ab = ctx.createBuffer(2, frames, RATE), L = ab.getChannelData(0), R = ab.getChannelData(1);
  for (var i = 0, k = 0; i < frames; i++, k += 2){ L[i] = i16[k] / 32768; R[i] = i16[k + 1] / 32768; }
  var src = ctx.createBufferSource(); src.buffer = ab; src.connect(ctx.destination);
  var floor = ctx.currentTime + 0.05 + delay;
  if (nextTime < floor || nextTime > floor + 0.7) nextTime = floor;   /* dopo una pausa o un accumulo si riallinea */
  src.start(nextTime); nextTime += ab.duration;
}
function pump(reader){
  reader.read().then(function(r){
    if (r.done) { setTimeout(startAudio, 1500); return; }
    playChunk(r.value); pump(reader);
  }).catch(function(){ setTimeout(startAudio, 1500); });
}
function startAudio(){
  try {
    if (!ctx) { var AC = window.AudioContext || window.webkitAudioContext; ctx = new AC(); }
    if (ctx.state === "suspended") { aud.style.display = "block"; ctx.resume(); }
    resetClock();
    fetch("/tvaudio.pcm?k=" + KEY).then(function(res){ pump(res.body.getReader()); })
      .catch(function(){ setTimeout(startAudio, 3000); });
  } catch (e) { /* senza audio il video funziona lo stesso */ }
}
function unlock(){ if (ctx && ctx.state === "suspended") ctx.resume(); aud.style.display = "none"; }
document.addEventListener("click", unlock); document.addEventListener("keydown", function(e){
  unlock();
  var k = e.keyCode;   /* 38 su: diagnostica; 37 sinistra, 39 destra: ritardo audio */
  if (k === 38){ dbg = !dbg; if (!dbg) delayBox.style.display = "none"; }
  if (k === 37 || k === 39){
    delay = Math.max(0, Math.min(2, delay + (k === 39 ? 0.1 : -0.1)));
    if (ctx) resetClock();
    delayBox.textContent = "Ritardo audio: " + delay.toFixed(1) + " s  (frecce sinistra/destra)";
    delayBox.style.display = "block";
    clearTimeout(delayBox._t); delayBox._t = setTimeout(function(){ delayBox.style.display = "none"; }, 2500);
  }
});
var NOAUDIO = location.search.indexOf("a=0") >= 0;
if (!NOAUDIO) {
  setTimeout(startAudio, 300);
  setInterval(function(){ if (ctx && ctx.state === "suspended") aud.style.display = "block"; }, 2000);
}
</script></body></html>"""
