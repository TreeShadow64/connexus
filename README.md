# Hub PC + Telefono — Fase 1

Comunicazione base PC-telefono via WebSocket sulla rete locale.

## pc-server (Python)

```bash
cd pc-server
pip install -r requirements.txt
python server.py
```

Il server ascolta su `0.0.0.0:8765` e stampa in console ogni messaggio ricevuto.

IP locale attuale del PC: `192.168.1.2` (usare questo indirizzo nell'app Android, sulla stessa rete Wi-Fi).

Se Windows Firewall blocca le connessioni in ingresso alla prima esecuzione, va consentito l'accesso per Python sulla rete privata.

## android-client (Kotlin)

Aprire la cartella `android-client` con Android Studio (Gradle sync automatico, wrapper generato da Android Studio al primo avvio). Poi:

1. Eseguire l'app su un telefono/emulatore sulla stessa rete Wi-Fi del PC.
2. Inserire l'IP del PC (es. `192.168.1.2`) e premere "CONNETTI".
3. Premere "INVIA MESSAGGIO DI TEST": il messaggio `test` deve comparire nella console del server Python.

## Verifica

1. Avviare `server.py`.
2. Avviare l'app Android e connettersi all'IP del PC.
3. Confermare nella console del server la riga `Ricevuto da (...): test`.
