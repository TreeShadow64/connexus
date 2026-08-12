import socket
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
from xml.sax.saxutils import escape

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
RENDERER_ST = "urn:schemas-upnp-org:device:MediaRenderer:1"
AVT_NS = "urn:schemas-upnp-org:service:AVTransport:1"
DEVICE_NS = {"d": "urn:schemas-upnp-org:device-1-0"}


def discover_renderers(timeout=3):
    msg = "\r\n".join([
        "M-SEARCH * HTTP/1.1",
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}",
        'MAN: "ssdp:discover"',
        "MX: 2",
        f"ST: {RENDERER_ST}",
        "", "",
    ]).encode()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    sock.sendto(msg, (SSDP_ADDR, SSDP_PORT))

    locations = set()
    start = time.time()
    while time.time() - start < timeout:
        try:
            data, _addr = sock.recvfrom(65507)
        except socket.timeout:
            break
        text = data.decode(errors="ignore")
        for line in text.split("\r\n"):
            if line.lower().startswith("location:"):
                locations.add(line.split(":", 1)[1].strip())
    sock.close()

    renderers = []
    for loc in locations:
        info = _describe_renderer(loc)
        if info:
            renderers.append(info)
    return renderers


def _describe_renderer(location):
    try:
        with urllib.request.urlopen(location, timeout=3) as resp:
            xml_data = resp.read()
    except Exception:
        return None

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return None

    device = root.find("d:device", DEVICE_NS)
    if device is None:
        return None

    friendly_name = device.findtext("d:friendlyName", default="Sconosciuto", namespaces=DEVICE_NS)

    control_url = None
    service_list = device.find("d:serviceList", DEVICE_NS)
    if service_list is not None:
        for service in service_list.findall("d:service", DEVICE_NS):
            service_type = service.findtext("d:serviceType", default="", namespaces=DEVICE_NS)
            if "AVTransport" in service_type:
                control_path = service.findtext("d:controlURL", default="", namespaces=DEVICE_NS)
                control_url = urljoin(location, control_path)

    if not control_url:
        return None

    return {"name": friendly_name, "control_url": control_url, "location": location}


def _soap_request(control_url, action, body_xml):
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f"<s:Body>{body_xml}</s:Body></s:Envelope>"
    )
    req = urllib.request.Request(
        control_url,
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{AVT_NS}#{action}"',
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read()


def _build_didl(media_url, title, mime="video/mp4"):
    upnp_class = "object.item.audioItem.musicTrack" if mime.startswith("audio/") else "object.item.videoItem"
    raw = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="0" restricted="1">'
        f"<dc:title>{escape(title)}</dc:title>"
        f"<upnp:class>{upnp_class}</upnp:class>"
        f'<res protocolInfo="http-get:*:{mime}:*">{escape(media_url)}</res>'
        "</item></DIDL-Lite>"
    )
    return escape(raw)


def set_av_transport_uri(control_url, media_url, title="Video", mime="video/mp4"):
    didl = _build_didl(media_url, title, mime)
    body = (
        f'<u:SetAVTransportURI xmlns:u="{AVT_NS}">'
        "<InstanceID>0</InstanceID>"
        f"<CurrentURI>{escape(media_url)}</CurrentURI>"
        f"<CurrentURIMetaData>{didl}</CurrentURIMetaData>"
        "</u:SetAVTransportURI>"
    )
    return _soap_request(control_url, "SetAVTransportURI", body)


def play(control_url):
    body = (
        f'<u:Play xmlns:u="{AVT_NS}">'
        "<InstanceID>0</InstanceID><Speed>1</Speed>"
        "</u:Play>"
    )
    # Alcuni TV LG webOS eseguono il comando ma non rispondono in tempo utile:
    # un timeout qui non significa che il Play sia fallito, va verificato con get_transport_info.
    return _soap_request(control_url, "Play", body)


def get_transport_info(control_url):
    body = (
        f'<u:GetTransportInfo xmlns:u="{AVT_NS}">'
        "<InstanceID>0</InstanceID>"
        "</u:GetTransportInfo>"
    )
    response = _soap_request(control_url, "GetTransportInfo", body)
    root = ET.fromstring(response)
    state_el = root.find(".//CurrentTransportState")
    return state_el.text if state_el is not None else "SCONOSCIUTO"


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()
