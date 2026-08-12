package com.hubpc.client

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivitySystemBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject

/** Wake-on-LAN, VPN sul telefono e stato del servizio Windows con permessi
 * elevati (sblocco schermo da remoto). Canale WebSocket dedicato. */
class SystemActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySystemBinding
    private var webSocket: WebSocket? = null
    private var authenticated = false

    companion object {
        private const val PREFS_NAME = HubApplication.PREFS_NAME
        private const val PREF_MAC = "mac"
        private const val PROTONVPN_PACKAGE = "ch.protonvpn.android"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySystemBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val ip = intent.getStringExtra("ip").orEmpty()
        val token = intent.getStringExtra("token").orEmpty()

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        binding.editMac.setText(prefs.getString(PREF_MAC, ""))

        binding.helpSystem.setOnClickListener {
            HelpDialogs.show(
                this, "Sistema",
                "AVVIA PROTONVPN: apre l'app ProtonVPN sul telefono — se non e' installata, apre lo Store.\n\n" +
                    "SVEGLIA PC (Wake-on-LAN): il PC spento non puo' avere un server in ascolto, quindi questo pulsante " +
                    "manda un pacchetto speciale direttamente sulla rete Wi-Fi di casa per riaccenderlo (funziona solo se sei " +
                    "sulla stessa rete). Gli serve l'indirizzo MAC del PC, un identificativo fisso della sua scheda di rete: " +
                    "premi OTTIENI mentre sei gia' connesso per salvarlo automaticamente, cosi' non dovrai piu' cercarlo.\n\n" +
                    "Servizio con permessi elevati: gira in background sul PC e permette di sbloccare lo schermo da remoto " +
                    "anche quando nessuno ha fatto login."
            )
        }

        binding.helpRemoteAccess.setOnClickListener {
            HelpDialogs.show(
                this, "Accesso da fuori casa",
                "Il PC e' raggiungibile solo dalla rete Wi-Fi di casa, a meno di aprire un varco nel router: " +
                    "in gergo si chiama \"port forwarding\" e si configura dal pannello del router (non da questa app).\n\n" +
                    "Porte da inoltrare verso l'IP locale del PC: 8765, 8766, 8767, 8768 (protocollo TCP).\n\n" +
                    "Poi in Impostazioni aggiungi una nuova connessione usando, al posto dell'IP locale, il tuo IP pubblico " +
                    "o un indirizzo DDNS (es. \"casamia.duckdns.org\") se il tuo IP pubblico cambia nel tempo — il resto " +
                    "dell'app funziona esattamente come in casa, basta scegliere quella connessione."
            )
        }

        binding.buttonLaunchVpn.setOnClickListener { launchProtonVpn() }

        binding.buttonGetMac.setOnClickListener {
            sendCommand(JSONObject().put("type", "get_mac_address"))
            log("Richiesta indirizzo MAC del PC...")
        }
        binding.buttonWakeOnLan.setOnClickListener {
            val mac = binding.editMac.text.toString().trim()
            if (mac.isEmpty()) {
                log("Inserisci l'indirizzo MAC del PC (o premi OTTIENI mentre sei connesso)")
            } else {
                prefs.edit().putString(PREF_MAC, mac).apply()
                sendWakeOnLan(mac)
            }
        }

        binding.buttonCheckService.setOnClickListener {
            sendCommand(JSONObject().put("type", "service_status"))
        }

        connect(ip, token)
    }

    private fun launchProtonVpn() {
        val launchIntent = packageManager.getLaunchIntentForPackage(PROTONVPN_PACKAGE)
        if (launchIntent != null) {
            startActivity(launchIntent)
        } else {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$PROTONVPN_PACKAGE")))
            } catch (e: android.content.ActivityNotFoundException) {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=$PROTONVPN_PACKAGE")))
            }
        }
    }

    private fun sendWakeOnLan(macAddress: String) {
        WolHelper.send(macAddress) { _, message -> runOnUiThread { log(message) } }
    }

    private fun sendCommand(json: JSONObject) {
        if (!authenticated) {
            log("Non ancora autenticato")
            return
        }
        webSocket?.send(json.toString())
    }

    private fun connect(ip: String, token: String) {
        val client = OkHttpClient()
        val request = Request.Builder().url("ws://$ip:8765").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "auth").put("token", token).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runOnUiThread { handleMessage(text) }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {}

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { log("Errore: ${t.message}") }
            }
        })
    }

    private fun handleMessage(text: String) {
        val json = try { JSONObject(text) } catch (e: Exception) { return }
        when (json.optString("type")) {
            "auth_ok" -> {
                authenticated = true
                log("Connesso.")
                sendCommand(JSONObject().put("type", "service_status"))
            }
            "auth_error" -> log("Autenticazione fallita")
            "mac_address" -> {
                val mac = json.optString("mac")
                binding.editMac.setText(mac)
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().putString(PREF_MAC, mac).apply()
                log("MAC del PC: $mac")
            }
            "service_status_result" -> {
                val installed = json.optBoolean("installed")
                binding.textServiceStatus.text =
                    if (installed) "servizio UAC: ATTIVO" else "servizio UAC: non installato"
                log(json.optString("message"))
            }
        }
    }

    private fun log(message: String) {
        binding.textStatus.text = message
    }

    override fun onDestroy() {
        webSocket?.close(1000, "Chiuso")
        super.onDestroy()
    }
}
