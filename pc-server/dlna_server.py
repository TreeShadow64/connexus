"""Server DLNA minimale (MediaServer/ContentDirectory) sul PC: a differenza
di dlna_cast.py (che "spinge" un file alla volta su una TV, cast_play), qui
il PC diventa una libreria che la TV sfoglia da sola con la sua app
Smart Share, esattamente come farebbe con un NAS. Stesso spirito minimale di
ftp_server.py: solo Browse (niente Search/Sort veri), nessuna
autenticazione — il protocollo DLNA reale non ne ha mai avuta, e' pensato
per la sola LAN di casa.

L'annuncio SSDP (sia la risposta a M-SEARCH sia gli annunci periodici
"alive") lega il socket all'IP della LAN reale invece di lasciare la
scelta dell'interfaccia al sistema operativo: senza, con una VPN come
Tailscale installata, gli annunci partirebbero dall'interfaccia sbagliata
e la TV non ci troverebbe mai (stesso bug gia' risolto in dlna_cast.py)."""
import base64
import logging
import mimetypes
import re
import socket
import threading
import time
import uuid as uuid_lib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape

import cv2

import dlna_cast

log = logging.getLogger("hub-server")

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
HTTP_PORT = 8772
DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaServer:1"
CD_TYPE = "urn:schemas-upnp-org:service:ContentDirectory:1"
CM_TYPE = "urn:schemas-upnp-org:service:ConnectionManager:1"
ANNOUNCE_INTERVAL_S = 890  # sotto il max-age di 1800s annunciato, cosi' non scade mai per i client passivi


def _encode_id(rel_path):
    if not rel_path:
        return "0"
    return base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_id(object_id):
    if object_id in ("0", ""):
        return ""
    padded = object_id + "=" * (-len(object_id) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def _extract_tag(xml_text, tag):
    m = re.search(f"<{tag}>(.*?)</{tag}>", xml_text, re.DOTALL)
    return m.group(1).strip() if m else None


def _soap_envelope(action_name, service_type, fields):
    body = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action_name} xmlns:u="{service_type}">{body}</u:{action_name}></s:Body>'
        "</s:Envelope>"
    )


# Flag DLNA standard (lo stesso valore usato da minidlna e altri server
# minimali per contenuti a cui non si assegna un profilo DLNA specifico):
# bit "range supported" + "streaming transfer mode" + versione DLNA 1.5.
# Senza questi, alcune TV (LG webOS in particolare) accettano la richiesta
# e iniziano a ricevere i dati ma interrompono la connessione dopo pochi
# secondi perche' non riescono a confermare che il file sia posizionabile.
_DLNA_FLAGS = "01700000000000000000000000000000"


def _dlna_content_features():
    return f"DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS={_DLNA_FLAGS}"


def _upnp_class_for(mime):
    if mime.startswith("video/"):
        return "object.item.videoItem"
    if mime.startswith("audio/"):
        return "object.item.audioItem.musicTrack"
    if mime.startswith("image/"):
        return "object.item.imageItem.photo"
    return "object.item"


_DIDL_HEADER = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
)
_DIDL_FOOTER = "</DIDL-Lite>"

_CD_SCPD = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
    "<specVersion><major>1</major><minor>0</minor></specVersion>"
    "<actionList>"
    "<action><name>Browse</name><argumentList>"
    '<argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>'
    '<argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>'
    '<argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>'
    '<argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>'
    '<argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>'
    '<argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>'
    '<argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>'
    '<argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>'
    '<argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>'
    '<argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>'
    "</argumentList></action>"
    "<action><name>GetSearchCapabilities</name><argumentList>"
    '<argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>'
    "</argumentList></action>"
    "<action><name>GetSortCapabilities</name><argumentList>"
    '<argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument>'
    "</argumentList></action>"
    "<action><name>GetSystemUpdateID</name><argumentList>"
    '<argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>'
    "</argumentList></action>"
    "</actionList>"
    "<serviceStateTable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType>'
    "<allowedValueList><allowedValue>BrowseMetadata</allowedValue><allowedValue>BrowseDirectChildren</allowedValue></allowedValueList>"
    "</stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable>'
    '<stateVariable sendEvents="yes"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>'
    "</serviceStateTable>"
    "</scpd>"
)

_CM_SCPD = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
    "<specVersion><major>1</major><minor>0</minor></specVersion>"
    "<actionList>"
    "<action><name>GetProtocolInfo</name><argumentList>"
    '<argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>'
    '<argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument>'
    "</argumentList></action>"
    "<action><name>GetCurrentConnectionIDs</name><argumentList>"
    '<argument><name>ConnectionIDs</name><direction>out</direction><relatedStateVariable>CurrentConnectionIDs</relatedStateVariable></argument>'
    "</argumentList></action>"
    "<action><name>GetCurrentConnectionInfo</name><argumentList>"
    '<argument><name>ConnectionID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>'
    '<argument><name>RcsID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_RcsID</relatedStateVariable></argument>'
    '<argument><name>AVTransportID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_AVTransportID</relatedStateVariable></argument>'
    '<argument><name>ProtocolInfo</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ProtocolInfo</relatedStateVariable></argument>'
    '<argument><name>PeerConnectionManager</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionManager</relatedStateVariable></argument>'
    '<argument><name>PeerConnectionID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>'
    '<argument><name>Direction</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Direction</relatedStateVariable></argument>'
    '<argument><name>Status</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionStatus</relatedStateVariable></argument>'
    "</argumentList></action>"
    "</actionList>"
    "<serviceStateTable>"
    '<stateVariable sendEvents="yes"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="yes"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="yes"><name>CurrentConnectionIDs</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionID</name><dataType>i4</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_RcsID</name><dataType>i4</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_AVTransportID</name><dataType>i4</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ProtocolInfo</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionManager</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Direction</name><dataType>string</dataType></stateVariable>'
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionStatus</name><dataType>string</dataType></stateVariable>'
    "</serviceStateTable>"
    "</scpd>"
)


class DlnaMediaServer:
    def __init__(self, root, friendly_name=None):
        self.root = Path(root).resolve()
        self.friendly_name = friendly_name or f"Connexus - {self.root.name or 'Condivisa'}"
        self.udn = "uuid:" + str(uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, str(self.root)))
        self._running = False
        self._httpd = None
        self._ssdp_socket = None
        self._local_ip = None
        # In memoria, per la durata della condivisione: rigenerare un
        # fotogramma ad ogni richiesta sarebbe lento (va aperto il video
        # con OpenCV), e la miniatura di un file non cambia mentre e' condiviso.
        self._thumb_cache = {}

    @property
    def is_active(self):
        return self._running

    def start(self):
        if self._running:
            return
        self._local_ip = dlna_cast.get_local_ip()
        self._running = True
        self._start_http()
        self._start_ssdp()
        threading.Thread(target=self._announce_loop, daemon=True).start()

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._send_byebye()
        try:
            if self._httpd:
                self._httpd.shutdown()
                self._httpd.server_close()
        except Exception:
            pass
        try:
            if self._ssdp_socket:
                self._ssdp_socket.close()
        except OSError:
            pass
        self._httpd = None
        self._ssdp_socket = None

    # --- HTTP: descrizione dispositivo, SCPD, controllo SOAP, contenuto file ---

    def _start_http(self):
        media_server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/dlna/description.xml":
                    self._send_xml(media_server._description_xml())
                elif self.path == "/dlna/ContentDirectory.xml":
                    self._send_xml(_CD_SCPD)
                elif self.path == "/dlna/ConnectionManager.xml":
                    self._send_xml(_CM_SCPD)
                elif self.path.startswith("/dlna/content/"):
                    media_server._serve_content(self, send_body=True)
                elif self.path.startswith("/dlna/thumbnail/"):
                    media_server._serve_thumbnail(self, send_body=True)
                else:
                    self.send_error(404)

            def do_HEAD(self):
                # I player DLNA (inclusi quelli LG webOS) spesso interrogano
                # il file con HEAD prima di riprodurlo davvero, per leggere
                # Content-Length/Content-Type: senza risposta qui rinunciano
                # a riprodurre anche se poi la GET funzionerebbe benissimo.
                if self.path.startswith("/dlna/content/"):
                    media_server._serve_content(self, send_body=False)
                elif self.path.startswith("/dlna/thumbnail/"):
                    media_server._serve_thumbnail(self, send_body=False)
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.path == "/dlna/control/ContentDirectory":
                    media_server._handle_cd_control(self)
                elif self.path == "/dlna/control/ConnectionManager":
                    media_server._handle_cm_control(self)
                else:
                    self.send_error(404)

            def do_SUBSCRIBE(self):
                # Eventing GENA: non implementato per davvero, ma alcuni
                # client si aspettano comunque un 200 con SID/TIMEOUT prima
                # di procedere con Browse.
                self.send_response(200)
                self.send_header("SID", "uuid:00000000-0000-0000-0000-000000000000")
                self.send_header("TIMEOUT", "Second-1800")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_UNSUBSCRIBE(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _send_xml(self, xml_text):
                body = xml_text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", 'text/xml; charset="utf-8"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def _serve_content(self, handler, send_body=True):
        encoded = handler.path[len("/dlna/content/"):]
        rel_path = _decode_id(unquote(encoded))
        file_path = (self.root / rel_path).resolve()
        try:
            in_root = file_path.is_relative_to(self.root)
        except (OSError, RuntimeError):
            in_root = False
        if not in_root or not file_path.is_file():
            log.warning(f"DLNA content non trovato: rel_path={rel_path!r} in_root={in_root}")
            handler.send_error(404)
            return

        size = file_path.stat().st_size
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = 200
        range_header = handler.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            status = 206
            spec = range_header[len("bytes="):].split("-")
            if spec[0]:
                start = int(spec[0])
            if len(spec) > 1 and spec[1]:
                end = int(spec[1])
            end = min(end, size - 1)

        length = end - start + 1
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", mime)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Length", str(length))
            handler.send_header("contentFeatures.dlna.org", _dlna_content_features())
            handler.send_header("transferMode.dlna.org", "Streaming")
            if status == 206:
                handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            handler.end_headers()
            if not send_body:
                return
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # la TV ha interrotto la riproduzione/il seek

    def _serve_thumbnail(self, handler, send_body=True):
        encoded = handler.path[len("/dlna/thumbnail/"):]
        rel_path = _decode_id(unquote(encoded))
        file_path = (self.root / rel_path).resolve()
        try:
            in_root = file_path.is_relative_to(self.root)
        except (OSError, RuntimeError):
            in_root = False
        if not in_root or not file_path.is_file():
            handler.send_error(404)
            return

        cache_key = str(file_path)
        if cache_key not in self._thumb_cache:
            self._thumb_cache[cache_key] = self._generate_thumbnail(file_path)
        data = self._thumb_cache[cache_key]
        if data is None:
            handler.send_error(404)
            return

        try:
            handler.send_response(200)
            handler.send_header("Content-Type", "image/jpeg")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            if send_body:
                handler.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    @staticmethod
    def _generate_thumbnail(file_path):
        """Un fotogramma preso un po' dentro il video (non il primo: spesso
        e' un logo o uno schermo nero) e ridotto a una miniatura, cosi' ogni
        riquadro in Smart Share mostra un'anteprima vera invece di un'icona
        generica. None se il file non e' un video leggibile da OpenCV (o non
        e' affatto un video): in quel caso niente miniatura per quel file,
        non un errore che blocca la condivisione."""
        cap = None
        try:
            cap = cv2.VideoCapture(str(file_path))
            if not cap.isOpened():
                return None
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            if frame_count > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_count * 0.1))
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
            height, width = frame.shape[:2]
            if width > 320:
                new_width = 320
                new_height = int(height * (new_width / width))
                frame = cv2.resize(frame, (new_width, new_height))
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return buf.tobytes() if ok else None
        except Exception:
            return None
        finally:
            if cap is not None:
                cap.release()

    def _handle_cd_control(self, handler):
        length = int(handler.headers.get("Content-Length", 0))
        body = handler.rfile.read(length).decode("utf-8", errors="ignore")
        action = self._soap_action_name(handler)

        if action == "Browse":
            object_id = _extract_tag(body, "ObjectID") or "0"
            browse_flag = _extract_tag(body, "BrowseFlag") or "BrowseDirectChildren"
            response_xml = self._browse(object_id, browse_flag)
        elif action == "GetSearchCapabilities":
            response_xml = _soap_envelope("GetSearchCapabilitiesResponse", CD_TYPE, {"SearchCaps": ""})
        elif action == "GetSortCapabilities":
            response_xml = _soap_envelope("GetSortCapabilitiesResponse", CD_TYPE, {"SortCaps": ""})
        elif action == "GetSystemUpdateID":
            response_xml = _soap_envelope("GetSystemUpdateIDResponse", CD_TYPE, {"Id": "1"})
        else:
            handler.send_error(500, "Azione non supportata")
            return
        self._write_soap_response(handler, response_xml)

    def _handle_cm_control(self, handler):
        length = int(handler.headers.get("Content-Length", 0))
        handler.rfile.read(length)
        action = self._soap_action_name(handler)

        if action == "GetProtocolInfo":
            response_xml = _soap_envelope("GetProtocolInfoResponse", CM_TYPE, {"Source": "http-get:*:*:*", "Sink": ""})
        elif action == "GetCurrentConnectionIDs":
            response_xml = _soap_envelope("GetCurrentConnectionIDsResponse", CM_TYPE, {"ConnectionIDs": "0"})
        elif action == "GetCurrentConnectionInfo":
            response_xml = _soap_envelope("GetCurrentConnectionInfoResponse", CM_TYPE, {
                "RcsID": "-1", "AVTransportID": "-1", "ProtocolInfo": "",
                "PeerConnectionManager": "", "PeerConnectionID": "-1",
                "Direction": "Output", "Status": "OK",
            })
        else:
            handler.send_error(500, "Azione non supportata")
            return
        self._write_soap_response(handler, response_xml)

    @staticmethod
    def _soap_action_name(handler):
        soap_action = handler.headers.get("SOAPAction", "")
        return soap_action.split("#")[-1].strip('"') if "#" in soap_action else ""

    @staticmethod
    def _write_soap_response(handler, response_xml):
        body_bytes = response_xml.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", 'text/xml; charset="utf-8"')
        handler.send_header("Content-Length", str(len(body_bytes)))
        handler.end_headers()
        handler.wfile.write(body_bytes)

    def _browse(self, object_id, browse_flag):
        rel_path = _decode_id(object_id)
        target = (self.root / rel_path).resolve() if rel_path else self.root
        try:
            in_root = target.is_relative_to(self.root)
        except (OSError, RuntimeError):
            in_root = False

        if not in_root or not target.is_dir():
            didl = _DIDL_HEADER + _DIDL_FOOTER
            return _soap_envelope("BrowseResponse", CD_TYPE, {
                "Result": escape(didl), "NumberReturned": "0", "TotalMatches": "0", "UpdateID": "1",
            })

        if browse_flag == "BrowseMetadata":
            items_xml = self._container_xml(rel_path, target)
            count = 1
        else:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            parts = []
            for entry in entries:
                child_rel = f"{rel_path}/{entry.name}" if rel_path else entry.name
                if entry.is_dir():
                    parts.append(self._container_xml(child_rel, entry, parent_id=object_id))
                elif entry.is_file():
                    parts.append(self._item_xml(child_rel, entry, parent_id=object_id))
            items_xml = "".join(parts)
            count = len(parts)

        didl = _DIDL_HEADER + items_xml + _DIDL_FOOTER
        return _soap_envelope("BrowseResponse", CD_TYPE, {
            "Result": escape(didl), "NumberReturned": str(count), "TotalMatches": str(count), "UpdateID": "1",
        })

    def _container_xml(self, rel_path, path_obj, parent_id="0"):
        object_id = _encode_id(rel_path)
        title = escape(path_obj.name or self.friendly_name)
        return (
            f'<container id="{object_id}" parentID="{parent_id}" restricted="1" searchable="0">'
            f"<dc:title>{title}</dc:title>"
            "<upnp:class>object.container.storageFolder</upnp:class>"
            "</container>"
        )

    def _item_xml(self, rel_path, path_obj, parent_id="0"):
        object_id = _encode_id(rel_path)
        title = escape(path_obj.name)
        mime = mimetypes.guess_type(path_obj.name)[0] or "application/octet-stream"
        upnp_class = _upnp_class_for(mime)
        size = path_obj.stat().st_size
        content_url = f"http://{self._local_ip}:{HTTP_PORT}/dlna/content/{quote(object_id)}"
        protocol_info = f"http-get:*:{mime}:{_dlna_content_features()}"

        thumb_xml = ""
        if mime.startswith("video/") or mime.startswith("image/"):
            thumb_url = f"http://{self._local_ip}:{HTTP_PORT}/dlna/thumbnail/{quote(object_id)}"
            thumb_protocol_info = f"http-get:*:image/jpeg:DLNA.ORG_PN=JPEG_TN;DLNA.ORG_FLAGS={_DLNA_FLAGS}"
            thumb_xml = (
                f"<upnp:albumArtURI>{escape(thumb_url)}</upnp:albumArtURI>"
                f'<res protocolInfo="{thumb_protocol_info}">{escape(thumb_url)}</res>'
            )

        return (
            f'<item id="{object_id}" parentID="{parent_id}" restricted="1">'
            f"<dc:title>{title}</dc:title>"
            f"<upnp:class>{upnp_class}</upnp:class>"
            f'<res protocolInfo="{protocol_info}" size="{size}">{escape(content_url)}</res>'
            f"{thumb_xml}"
            "</item>"
        )

    def _description_xml(self):
        base_url = f"http://{self._local_ip}:{HTTP_PORT}"
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<root xmlns="urn:schemas-upnp-org:device-1-0">'
            "<specVersion><major>1</major><minor>0</minor></specVersion>"
            f"<URLBase>{base_url}/</URLBase>"
            "<device>"
            f"<deviceType>{DEVICE_TYPE}</deviceType>"
            f"<friendlyName>{escape(self.friendly_name)}</friendlyName>"
            "<manufacturer>Connexus</manufacturer>"
            "<modelName>Connexus PC Hub</modelName>"
            f"<UDN>{self.udn}</UDN>"
            "<serviceList>"
            "<service>"
            f"<serviceType>{CD_TYPE}</serviceType>"
            "<serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>"
            "<SCPDURL>/dlna/ContentDirectory.xml</SCPDURL>"
            "<controlURL>/dlna/control/ContentDirectory</controlURL>"
            "<eventSubURL>/dlna/event/ContentDirectory</eventSubURL>"
            "</service>"
            "<service>"
            f"<serviceType>{CM_TYPE}</serviceType>"
            "<serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>"
            "<SCPDURL>/dlna/ConnectionManager.xml</SCPDURL>"
            "<controlURL>/dlna/control/ConnectionManager</controlURL>"
            "<eventSubURL>/dlna/event/ConnectionManager</eventSubURL>"
            "</service>"
            "</serviceList>"
            "</device>"
            "</root>"
        )

    # --- SSDP: risposta a M-SEARCH + annunci periodici "alive"/"byebye" ---

    def _start_ssdp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", SSDP_PORT))
        group = socket.inet_aton(SSDP_ADDR) + socket.inet_aton(self._local_ip)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group)
        sock.settimeout(1.0)
        self._ssdp_socket = sock
        threading.Thread(target=self._ssdp_loop, daemon=True).start()

    def _ssdp_loop(self):
        while self._running:
            try:
                data, addr = self._ssdp_socket.recvfrom(65507)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode(errors="ignore")
            if not text.upper().startswith("M-SEARCH"):
                continue
            st = None
            for line in text.split("\r\n"):
                if line.lower().startswith("st:"):
                    st = line.split(":", 1)[1].strip()
            if st in ("ssdp:all", "upnp:rootdevice", DEVICE_TYPE, self.udn):
                self._reply_search(addr, st)

    def _reply_search(self, addr, st):
        location = f"http://{self._local_ip}:{HTTP_PORT}/dlna/description.xml"
        usn = self.udn if st == self.udn else f"{self.udn}::{st}"
        msg = "\r\n".join([
            "HTTP/1.1 200 OK",
            "CACHE-CONTROL: max-age=1800",
            "EXT:",
            f"LOCATION: {location}",
            "SERVER: Windows/10 UPnP/1.0 Connexus/1.0",
            f"ST: {st}",
            f"USN: {usn}",
            "", "",
        ]).encode()
        try:
            self._ssdp_socket.sendto(msg, addr)
        except OSError:
            pass

    def _announce_loop(self):
        time.sleep(1)
        while self._running:
            self._send_alive()
            for _ in range(ANNOUNCE_INTERVAL_S // 3):
                if not self._running:
                    return
                time.sleep(3)

    def _send_alive(self):
        location = f"http://{self._local_ip}:{HTTP_PORT}/dlna/description.xml"
        for nt, usn in [
            ("upnp:rootdevice", f"{self.udn}::upnp:rootdevice"),
            (self.udn, self.udn),
            (DEVICE_TYPE, f"{self.udn}::{DEVICE_TYPE}"),
            (CD_TYPE, f"{self.udn}::{CD_TYPE}"),
        ]:
            msg = "\r\n".join([
                "NOTIFY * HTTP/1.1",
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}",
                "CACHE-CONTROL: max-age=1800",
                f"LOCATION: {location}",
                "SERVER: Windows/10 UPnP/1.0 Connexus/1.0",
                f"NT: {nt}",
                "NTS: ssdp:alive",
                f"USN: {usn}",
                "", "",
            ]).encode()
            self._send_multicast(msg)

    def _send_byebye(self):
        for nt, usn in [
            ("upnp:rootdevice", f"{self.udn}::upnp:rootdevice"),
            (self.udn, self.udn),
            (DEVICE_TYPE, f"{self.udn}::{DEVICE_TYPE}"),
        ]:
            msg = "\r\n".join([
                "NOTIFY * HTTP/1.1",
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}",
                f"NT: {nt}",
                "NTS: ssdp:byebye",
                f"USN: {usn}",
                "", "",
            ]).encode()
            self._send_multicast(msg)

    def _send_multicast(self, msg):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self._local_ip))
            sock.sendto(msg, (SSDP_ADDR, SSDP_PORT))
            sock.close()
        except OSError:
            pass
