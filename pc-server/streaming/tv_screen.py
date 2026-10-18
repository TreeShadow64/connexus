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
    "video": {"fps": 15, "quality": 60, "max_width": 1280, "h264": (1920, 30, 26), "audio": True, "rtc": True},
    "videosolo": {"fps": 15, "quality": 60, "max_width": 1280, "h264": (1920, 30, 26)},
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
        video = "avc1.42C028"
    else:
        video = "avc1.%02X%02X%02X" % (moov[i + 5], moov[i + 6], moov[i + 7])
    # traccia audio AAC-LC nello stesso MP4: lo dice il box esds
    return video + (",mp4a.40.2" if moov.find(b"esds") >= 0 else "")


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
        self._last_fragment = 0.0
        self._watchdog_started = False
        self._audio_route = "tv"

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
                                      h264=cfg.get("h264"), audio=bool(cfg.get("audio")),
                                      rtc=bool(cfg.get("rtc")))
            self._video_init = None
            self.codec = None
            self._assembler = Fmp4Assembler(self._on_video_init, self._on_video_fragment) \
                if cfg.get("h264") else None
            import time
            self._last_fragment = time.time()
            streamer.start(self._on_frame, self._on_video if self._assembler else None)
            self._streamer = streamer
            self._profile = profile
            if streamer.rtc:
                streamer.send_control({"type": "audio_route", "route": self._audio_route})
            if not self._key:
                self._key = secrets.token_urlsafe(12)
            self._latest = None
            if not self._watchdog_started:
                self._watchdog_started = True
                threading.Thread(target=self._watchdog, daemon=True).start()
            log.info(f"Secondo schermo TV attivo (profilo {profile})")
            return streamer.geometry

    def _watchdog(self):
        """Se il video smette di arrivare dalla cattura (processo bloccato o flusso
        corrotto) riavvia la cattura da sola, con la stessa chiave: la TV riprende
        senza che nessuno debba intervenire."""
        import time
        while True:
            time.sleep(2)
            streamer = self._streamer
            if streamer is None or self._assembler is None:
                continue
            stuck = time.time() - self._last_fragment > 8
            if not (stuck or streamer.reader_ended):
                continue
            log.warning("Flusso video fermo: riavvio la cattura")
            try:
                profile = self._profile
                with self._lock:
                    old, self._streamer = self._streamer, None
                if old is not None:
                    old.stop()
                self.start(profile)
            except Exception as e:
                log.warning(f"Riavvio della cattura non riuscito: {type(e).__name__}: {e}")
                time.sleep(5)

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
        import time
        self._last_fragment = time.time()
        for q in list(self._video_subs):
            try:
                q.put_nowait(fragment)
            except Exception:
                try:                       # chi e' in ritardo salta i frammenti vecchi
                    q.get_nowait()
                    q.put_nowait(fragment)
                except Exception:
                    pass

    def set_audio_route(self, route):
        """'tv' = l'audio viaggia con il video; 'pc' = resta sul PC (la TV non lo riceve)."""
        self._audio_route = "pc" if route == "pc" else "tv"
        streamer = self._streamer
        if streamer is not None and streamer.rtc:
            streamer.send_control({"type": "audio_route", "route": self._audio_route})

    @property
    def rtc_enabled(self):
        streamer = self._streamer
        return bool(streamer is not None and streamer.wait_rtc_ready())

    def rtc_offer(self, sdp, sdp_type):
        streamer = self._streamer
        if streamer is None:
            raise RuntimeError("secondo schermo non attivo")
        return streamer.rtc_offer(sdp, sdp_type)

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
<video id="v" autoplay playsinline></video>
<img id="s" alt="" style="display:none">
<div id="msg" class="note">Connexus: in attesa del secondo schermo...</div>
<div id="aud" class="note"><button id="audbtn">Premi OK per attivare l'audio</button></div>
<div id="delay" class="note"></div>
<div id="hint" class="note" style="bottom:60px;display:block;color:#fff;background:rgba(0,0,0,.55);width:max-content;margin:auto;padding:6px 14px">Premi OK sul telecomando per lo schermo intero</div>
<script>setTimeout(function(){ var h = document.getElementById("hint"); if (h) h.style.display = "none"; }, 8000);</script>
<script>
var KEY = "__KEY__", RATE = __RATE__, RTC_ENABLED = "__RTC__";
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

/* video H.264: WebRTC (ritardo ~0,1 s); se non parte, Media Source (~1 s: e' la soglia di
   questa TV prima di mostrare il video) e, in ultimo, immagini JPEG */
var RATECTL = location.search.indexOf("r=1") >= 0;
var EMBEDDED_AUDIO = false;
function stopWebAudio(){ NOAUDIO = true; }
var usedFallback = false, seeks = 0, waits = 0, lastT = 0, lastTotal = 0, lastDrop = 0, tick = 0, rxBytes = 0, lastRx = 0;
function fallback(why){
  if (usedFallback) return;
  usedFallback = true; tvlog("ripiego JPEG: " + why); pullDirect();
}

/* play/pausa sul PC: il tasto multimediale del PC ferma/fa ripartire il video che sta girando li' */
function mediaKey(code){ new Image().src = "/tvkey?k=" + KEY + "&c=" + code + "&t=" + Date.now(); }
if (navigator.mediaSession){      /* i tasti multimediali del telecomando, se la TV li gira al browser */
  try {
    ["play", "pause", "stop", "previoustrack", "nexttrack", "seekbackward", "seekforward"].forEach(function(a){
      try { navigator.mediaSession.setActionHandler(a, function(){ tvlog("mediaSession " + a); if (a === "play" || a === "pause") mediaKey(19); }); } catch (e) {}
    });
  } catch (e) {}
}
/* se la TV mette in pausa/riprende direttamente il suo video (WebRTC), lo gira al PC */
video.addEventListener("pause", function(){ tvlog("evento pause del video"); if (RTC_ACTIVE) mediaKey(19); });
video.addEventListener("play", function(){ if (RTC_ACTIVE && !video.dataset.first) { tvlog("evento play del video"); mediaKey(415); } video.dataset.first = "1"; });

/* l'app puo' spostare l'audio: "pc" = la TV non lo riceve (resta sugli altoparlanti del PC).
   Con WebRTC il lettore allinea il video all'audio (~225 ms di uscita audio della TV) e non
   torna indietro da solo: al cambio si ricollega, cosi' senza audio il video resta a ~30 ms. */
var RTC_ACTIVE = false, lastRoute = null;
setInterval(function(){
  fetch("/tvstate?k=" + KEY).then(function(r){ return r.json(); }).then(function(i){
    if (EMBEDDED_AUDIO) video.muted = (i.route === "pc");
    if (RTC_ACTIVE && lastRoute !== null && i.route !== lastRoute) { location.reload(); return; }
    lastRoute = i.route;
  }).catch(function(){});
}, 2500);

function startMse(){
  if (!window.MediaSource || !window.fetch) return fallback("MediaSource/fetch non disponibili");
  fetch("/tvvideo.info?k=" + KEY).then(function(r){ return r.json(); }).then(function(info){
    var mime = 'video/mp4; codecs="' + info.codec + '"';
    EMBEDDED_AUDIO = info.codec.indexOf("mp4a") >= 0;
    if (EMBEDDED_AUDIO) { video.muted = false; video.volume = 1; stopWebAudio(); }
    tvlog("audio nel flusso: " + EMBEDDED_AUDIO);
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
          if (video.paused) { var pr = video.play(); if (pr && pr.catch) pr.catch(function(){ video.muted = true; EMBEDDED_AUDIO = false; NOAUDIO = false; tvlog("audio del video bloccato: uso il canale audio separato"); video.play(); }); }
        } catch (e) {}
      });
      fetch("/tvvideo.mp4?k=" + KEY).then(function(res){
        var reader = res.body.getReader();
        (function pump(){
          reader.read().then(function(r){
            if (r.done){ tvlog("flusso video chiuso"); setTimeout(function(){ location.reload(); }, 1500); return; }
            if (!gotData){ gotData = true; tvlog("primi dati video"); }
            rxBytes += r.value.length;
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
          else if (RATECTL && ahead > 0.6 && video.playbackRate !== 1.1) video.playbackRate = 1.1;     /* recupera piano piano */
          else if (RATECTL && ahead < 0.35 && video.playbackRate !== 1.0) video.playbackRate = 1.0;
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
              ", salti " + seeks + ", attese " + waits + ", " + video.videoWidth + "x" + video.videoHeight +
              ", " + Math.round((rxBytes - lastRx) * 8 / 2000) + " kbit/s");
        lastT = video.currentTime; lastTotal = total; lastDrop = drop; lastRx = rxBytes;
      }
    }, 1000);
  }).catch(function(e){ fallback("info: " + e); });
}

/* WebRTC: video e audio arrivano come una chiamata, senza la soglia di 1 s del lettore */
function startRtc(){
  var pc = null, done = false, shown = false;
  function toMse(why){
    if (done) return;
    done = true;
    RTC_ACTIVE = false;
    tvlog("WebRTC non riuscito (" + why + "): uso il video classico");
    try { pc.close(); } catch (e) {}
    video.srcObject = null;
    startMse();
  }
  try { pc = new RTCPeerConnection({iceServers: []}); } catch (e) { return startMse(); }
  tvlog("browser: " + navigator.userAgent);
  pc.addTransceiver("video", {direction: "recvonly"});
  pc.addTransceiver("audio", {direction: "recvonly"});
  pc.ontrack = function(e){
    if (!video.srcObject) video.srcObject = (e.streams && e.streams[0]) ? e.streams[0] : new MediaStream();
    if (!e.streams || !e.streams[0]) video.srcObject.addTrack(e.track);
    video.style.display = "block"; img.style.display = "none";
    EMBEDDED_AUDIO = true; video.muted = false; video.volume = 1; stopWebAudio();
    var pr = video.play(); if (pr && pr.catch) pr.catch(function(){});
  };
  pc.onconnectionstatechange = function(){
    tvlog("WebRTC: " + pc.connectionState);
    if (pc.connectionState === "connected") RTC_ACTIVE = true;
    if (done) return;
    if (pc.connectionState === "failed"){ if (shown) location.reload(); else toMse("collegamento fallito"); }
    if (pc.connectionState === "disconnected") setTimeout(function(){
      if (!done && pc.connectionState !== "connected"){ if (shown) location.reload(); else toMse("scollegato"); }
    }, 4000);
  };
  pc.createOffer().then(function(o){ return pc.setLocalDescription(o); }).then(function(){
    return new Promise(function(res){
      if (pc.iceGatheringState === "complete") return res();
      pc.onicegatheringstatechange = function(){ if (pc.iceGatheringState === "complete") res(); };
      setTimeout(res, 1500);
    });
  }).then(function(){
    return fetch("/tvrtc?k=" + KEY, {method: "POST", body: JSON.stringify({sdp: pc.localDescription.sdp, type: pc.localDescription.type})});
  }).then(function(r){ if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function(a){ return pc.setRemoteDescription(a); })
    .catch(function(e){ toMse("segnalazione: " + e); });

  var lastFrames = 0, ticks = 0;
  var timer = setInterval(function(){
    if (done){ clearInterval(timer); return; }
    ticks++;
    var q = video.getVideoPlaybackQuality ? video.getVideoPlaybackQuality() : null;
    var total = q ? q.totalVideoFrames : (video.webkitDecodedFrameCount || 0);
    frames = total - lastFrames;
    if (total > 0) shown = true;
    if (!shown && ticks >= 8){ toMse("nessun fotogramma in 8 s"); return; }
    if (ticks % 4 === 0){
      var line = "rtc t+" + ticks + "s fotogrammi " + total + " totali";
      try {
        pc.getStats(function(r){
          var vtxt = "", atxt = "";
          r.result().forEach(function(x){
            if (x.type !== "ssrc") return;
            if (x.stat("googFrameWidthReceived"))
              vtxt = ", video: ritardo di riproduzione " + x.stat("googCurrentDelayMs") + " ms (buffer " + x.stat("googJitterBufferMs") +
                     " ms), " + x.stat("googFrameWidthReceived") + "x" + x.stat("googFrameHeightReceived") + " a " + x.stat("googFrameRateReceived") + " fps";
            else if (x.stat("audioOutputLevel") !== undefined || x.stat("googJitterReceived") !== undefined)
              atxt = ", audio: ritardo " + x.stat("googCurrentDelayMs") + " ms (buffer " + x.stat("googJitterBufferMs") + " ms)";
          });
          tvlog(line + vtxt + atxt);
        });
      } catch (e) { tvlog(line); }
    }
    lastFrames = total;
  }, 1000);
}

var MODE = (location.search.match(/[?&]m=([0-9a-z]+)/) || [0, "v"])[1];
setInterval(function(){
  if (dbg){ delayBox.textContent = frames + " fps" + (MODE === "j" ? " (JPEG)" : ""); delayBox.style.display = "block"; }
  if (MODE === "j") frames = 0;
}, 1000);
if (MODE === "j") pullDirect();
else if (MODE === "m" || !window.RTCPeerConnection || RTC_ENABLED !== "1") startMse();
else startRtc();

/* ---- audio (Web Audio: PCM 16 bit stereo dal PC) ---- */
var ctx = null, nextTime = 0, delay = 0.95, carry = null;   /* il video ha ~1 s di ritardo (buffer della TV): l'audio lo segue */
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
function goFull(){
  var e = document.documentElement;
  var f = e.requestFullscreen || e.webkitRequestFullscreen || e.webkitRequestFullScreen || e.mozRequestFullScreen;
  var on = document.fullscreenElement || document.webkitFullscreenElement;
  if (on) return;
  try { if (f) f.call(e); } catch (x) {}
}
function unlock(){ if (ctx && ctx.state === "suspended") ctx.resume(); aud.style.display = "none"; goFull(); }
document.addEventListener("click", unlock); document.addEventListener("keydown", function(e){
  unlock();
  var k = e.keyCode;   /* 38 su: diagnostica; 37 sinistra, 39 destra: ritardo audio */
  tvlog("tasto " + k);
  /* ROSSO 403 = indietro, VERDE 404 = avanti (freccia sinistra/destra sul PC), BLU 406 = schermo intero (tasto F: alterna) */
  /* pausa 19, play 415, play/pausa 179, GIALLO 405 (ripiego: la TV non passa sempre pausa/play alla pagina) */
  if (k === 19 || k === 415 || k === 179 || k === 405 || k === 403 || k === 404 || k === 406 || k === 413 || k === 412 || k === 417){
    e.preventDefault();
    tvlog("tasto multimediale " + k);
    if (k !== 413 && k !== 412 && k !== 417) mediaKey(k);
    return;
  }
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
setTimeout(function(){
  if (NOAUDIO) return;
  startAudio();
  setInterval(function(){ if (ctx && ctx.state === "suspended") aud.style.display = "block"; }, 2000);
}, 2500);
</script></body></html>"""
