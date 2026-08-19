"""Punto di avvio dell'app desktop: fa partire il server (identico a
'python server.py') su un thread separato, poi apre la dashboard dentro una
vera finestra (pywebview) invece che in una scheda del browser, con
un'icona nella system tray per richiamarla o uscire.

Chiudere la finestra la nasconde soltanto: il server resta attivo (il
telefono deve poter continuare a connettersi). Solo "Esci" dal menu della
tray chiude tutto per davvero.
"""
import asyncio
import os
import sys
import threading

# Va controllato PRIMA di ogni altro import pesante (pywebview, tray...):
# pacchettizzato, questo stesso exe viene rilanciato da screen_stream.py come
# processo di cattura schermo (niente python.exe a cui passare uno script
# sciolto quando tutto e' compilato in un unico eseguibile). Va quindi
# ridiretto subito alla logica di capture_process.py invece di aprire una
# seconda finestra/tray.
if len(sys.argv) > 1 and sys.argv[1] == "--capture-process":
    sys.argv = sys.argv[1:]
    import capture_process
    sys.exit(capture_process.main())

import pystray
import webview
from PIL import Image, ImageDraw

import server

DASHBOARD_URL = "http://127.0.0.1:8771/"

window = None
tray_icon = None


def run_server():
    asyncio.run(server.main())


def _asset_path(*parts):
    """Percorso di una risorsa bundlata, sia in sviluppo (pc-server/assets/...)
    sia impacchettata con PyInstaller (dentro sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", *parts)


def make_tray_image():
    """Icona della tray: usa tray.png se presente, altrimenti un placeholder
    disegnato a runtime (cosi' l'app parte comunque se manca l'asset)."""
    tray_path = _asset_path("tray.png")
    if os.path.isfile(tray_path):
        return Image.open(tray_path)

    size = 64
    img = Image.new("RGBA", (size, size), (3, 6, 8, 255))
    draw = ImageDraw.Draw(img)
    cyan = (111, 227, 255, 255)
    draw.ellipse((4, 4, size - 4, size - 4), outline=cyan, width=3)
    draw.ellipse((18, 18, size - 18, size - 18), outline=cyan, width=3)
    draw.ellipse((size // 2 - 4, size // 2 - 4, size // 2 + 4, size // 2 + 4), fill=cyan)
    return img


def show_window():
    if window is not None:
        window.show()
        window.restore()


def quit_app():
    if tray_icon is not None:
        tray_icon.stop()
    if window is not None:
        window.destroy()
    os._exit(0)  # il server gira sullo stesso processo: va chiuso di forza


def run_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem("Apri dashboard", lambda: show_window(), default=True),
        pystray.MenuItem("Esci", lambda: quit_app()),
    )
    tray_icon = pystray.Icon("connexus_pc_hub", make_tray_image(), "Connexus // PC Hub", menu)
    tray_icon.run()


def on_closing():
    """Chiudere la X nasconde in tray invece di terminare l'app: il server
    deve restare raggiungibile dal telefono anche a finestra chiusa."""
    window.hide()
    return False


if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=run_tray, daemon=True).start()

    window = webview.create_window(
        "Connexus // PC Hub",
        DASHBOARD_URL,
        width=1180,
        height=800,
        background_color="#030608",
        min_size=(820, 600),
    )
    window.events.closing += on_closing
    webview.start()
