# Connexus

Controlla il PC dal telefono (e viceversa) sulla rete locale o da fuori casa:
mouse e tastiera, schermo del PC sul telefono, telefono come webcam/specchio
sul PC, webcam del PC sul telefono, file (FTP), telecomando TV, Task Manager e
"Trova dispositivo". App Android e server Windows condividono la stessa grafica HUD.

## Struttura del repository

```
Connexus/
├── android-client/     App Android (Kotlin, ViewBinding, package com.hubpc.client)
│   └── app/src/main/
│       ├── java/com/hubpc/client/      Activity e logica dell'app
│       │   └── ui/                     Componenti HUD (barra, navigazione, anelli, sfondo)
│       └── res/                        Layout, drawable, font, colori, stili
├── pc-server/          Server Windows + dashboard
│   ├── server.py                       Server headless (WebSocket + HTTP)
│   ├── desktop_app.py                  App desktop: finestra + tray, avvia il server
│   ├── Connexus-PC-Hub.spec            Ricetta PyInstaller per l'exe
│   ├── requirements.txt
│   ├── core/                           Percorsi, autenticazione, aggiornamenti, Wake-on-LAN, server della dashboard
│   ├── files/                          Esplora file e FTP (server, client, TLS)
│   ├── casting/                        Cast verso TV: DLNA e LG webOS
│   ├── streaming/                      Schermo PC, cattura DirectX, monitor virtuale
│   ├── remote_input/                   Mouse/tastiera, servizio Windows con permessi elevati (+ install/uninstall)
│   ├── cloud/                          Relay Firebase per "Trova dispositivo"
│   ├── dashboard/                      Interfaccia web mostrata nella finestra (HTML/CSS/JS, font)
│   └── assets/                         Icone dell'exe e della tray
├── cloudflare-relay/   Worker Cloudflare (relay e notifiche push)
└── design/             Immagini sorgente dell'icona
```

## Porte del server PC

| Porta | Uso |
|---|---|
| 8765 | Comandi (WebSocket) |
| 8766 | Media HTTP (cast, upload) |
| 8767 | Schermo esteso |
| 8768 | Projector (telefono → PC) |
| 8769 | Virtual camera |
| 8770 | Servizio input con permessi elevati (solo locale) |
| 8771 | Dashboard (solo locale) |
| 8772 | DLNA |
| 8773 | Camera UVC |

Se Windows Firewall blocca le connessioni in ingresso alla prima esecuzione,
va consentito l'accesso sulla rete privata.

## Server PC

```bash
cd pc-server
pip install -r requirements.txt
python server.py          # solo server
python desktop_app.py     # finestra + tray (come l'exe)
```

All'avvio il server stampa il token di accesso da inserire una volta nell'app.

### Exe per Windows

```bash
cd pc-server
python -m PyInstaller Connexus-PC-Hub.spec --noconfirm
```

Il risultato è in `pc-server/dist/Connexus-PC-Hub/` (esegui `Connexus-PC-Hub.exe`
da lì, tenendo accanto la cartella `_internal`). Config, token e chiavi vengono
creati accanto all'exe e non vanno mai committati.

### Servizio con permessi elevati (opzionale)

Permette mouse e tastiera anche sulle finestre UAC e sulla schermata di blocco.
Dal prompt dei comandi come amministratore: `pc-server\remote_input\install_service.bat`
(e `uninstall_service.bat` per rimuoverlo). Senza servizio l'app funziona comunque.

## App Android

Aprire `android-client` con Android Studio, oppure da riga di comando:

```bash
cd android-client
./gradlew assembleDebug        # APK di prova
./gradlew assembleRelease      # richiede app/keystore.properties (non nel repo)
```

Al primo avvio l'app chiede IP e token del PC; si possono gestire più connessioni
(casa, fuori casa) da Impostazioni → Account e connessioni.

## Cloudflare Relay

```bash
cd cloudflare-relay
npm install
npx wrangler deploy
```

Segreti (`.dev.vars`, `.cf_token`) e `firebase-service-account.json` restano solo in
locale: sono ignorati da git.

## Versioni

Il numero di versione sta in `android-client/app/build.gradle.kts` (`versionName`)
e in `pc-server/core/updater.py` (`APP_VERSION`): vanno tenuti uguali. Le release
si pubblicano su GitHub Releases (zip dell'exe + APK) e l'updater le legge da lì.
