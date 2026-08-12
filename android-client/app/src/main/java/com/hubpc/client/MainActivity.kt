package com.hubpc.client

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.firebase.auth.FirebaseAuth
import com.hubpc.client.databinding.ActivityMainBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject

/** Home a moduli: le combinazioni IP/token sono configurate in OnboardingActivity
 * (la prima volta) o in SettingsActivity — qui si tenta la connessione automatica
 * partendo dalla primaria, passando alla successiva se una fallisce, e ci si
 * smista verso le sezioni dedicate (ciascuna con un proprio canale WebSocket). */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val client = OkHttpClient()
    private var webSocket: WebSocket? = null
    private var authenticated = false
    private var profiles: List<ConnectionProfile> = emptyList()
    private var attemptIndex = 0
    private var activeIp: String = ""
    private var activeToken: String = ""

    companion object {
        private const val PREFS_NAME = HubApplication.PREFS_NAME
        private const val PREF_ONBOARDED = "onboarded"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (FirebaseAuth.getInstance().currentUser == null) {
            startActivity(Intent(this, LoginActivity::class.java))
            finish()
            return
        }
        DeviceRegistryService.registerThisDevice(this)

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        if (!prefs.getBoolean(PREF_ONBOARDED, false)) {
            startActivity(Intent(this, OnboardingActivity::class.java))
            finish()
            return
        }

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.buttonConnect.setOnClickListener { startConnecting() }
        binding.buttonSettingsGear.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        binding.cardTvRemote.setOnClickListener { openSection(TvRemoteActivity::class.java) }
        binding.cardPcControl.setOnClickListener { openSection(PcControlActivity::class.java) }
        binding.cardScreen.setOnClickListener { openSection(ScreenActivity::class.java) }
        binding.cardProjector.setOnClickListener { openSection(ProjectorActivity::class.java) }
        binding.cardUvcCamera.setOnClickListener { openSection(UvcCameraActivity::class.java) }
        // "Trova dispositivo" funziona anche senza il PC raggiungibile in LAN
        // (e' proprio il punto: un telefono perso fuori casa) quindi non passa
        // dal gate di openSection(), che blocca se non si e' connessi al PC.
        binding.cardFindDevice.setOnClickListener {
            startActivity(
                Intent(this, FindDeviceActivity::class.java)
                    .putExtra("ip", activeIp)
                    .putExtra("token", activeToken)
            )
        }
        binding.cardFiles.setOnClickListener { openSection(FilesActivity::class.java) }
        binding.cardSystem.setOnClickListener { openSection(SystemActivity::class.java) }
        binding.cardTaskManager.setOnClickListener { openSection(TaskManagerActivity::class.java) }
        binding.cardVirtualCamera.setOnClickListener { openSection(VirtualCameraActivity::class.java) }

        binding.helpControllo.setOnClickListener {
            HelpDialogs.show(
                this, "Controllo",
                "Telecomando TV / Smart share: telecomando fedele, app installate e riproduzione sulla TV.\n\n" +
                    "Mouse / tastiera: usa il telefono come touchpad e tastiera per il PC, con controlli di alimentazione.\n\n" +
                    "Schermo PC: specchia il monitor principale del PC sul telefono, con zoom per vedere meglio i dettagli.\n\n" +
                    "Projector: il contrario — manda lo schermo del telefono al PC, che lo mostra in una finestra.\n\n" +
                    "Camera UVC: guarda sul telefono una webcam USB gia' collegata al PC, con luminosita'/contrasto regolabili.\n\n" +
                    "Trova dispositivo: localizza o fai suonare un allarme su un telefono o sul PC, anche fuori casa."
            )
        }
        binding.helpSistema.setOnClickListener {
            HelpDialogs.show(
                this, "Sistema",
                "Gestione file: sfoglia una cartella del telefono, condividi tutto lo storage via FTP, o pulisci spazio " +
                    "(video, APK, file grandi).\n\n" +
                    "Sistema: sveglia il PC in rete (Wake-on-LAN) e verifica il servizio con i permessi elevati " +
                    "usato per sbloccare lo schermo da remoto.\n\n" +
                    "Task manager: vedi i processi del PC e chiudili da qui.\n\n" +
                    "Virtual camera: usa la fotocamera del telefono come webcam del PC in Zoom/Teams/Discord " +
                    "(serve OBS Studio installato una volta sul PC)."
            )
        }

        startConnecting()
    }

    override fun onResume() {
        super.onResume()
        if (::binding.isInitialized) {
            val fresh = ConnectionProfiles.load(this)
            if (fresh != profiles) startConnecting()
        }
    }

    private fun openSection(target: Class<*>) {
        if (!authenticated) {
            val message = "Non ancora connesso al PC — attendi o tocca RICONNETTI"
            log(message)
            Toast.makeText(this, message, Toast.LENGTH_LONG).show()
            return
        }
        startActivity(
            Intent(this, target)
                .putExtra("ip", activeIp)
                .putExtra("token", activeToken)
        )
    }

    private fun startConnecting() {
        profiles = ConnectionProfiles.load(this)
        if (profiles.isEmpty()) {
            binding.textPcAddress.text = "nessuna connessione configurata"
            setConnStatus(false, "non configurato")
            return
        }
        attemptIndex = 0
        tryNextProfile()
    }

    private fun tryNextProfile() {
        if (attemptIndex >= profiles.size) {
            setConnStatus(false, "nessun pc raggiungibile")
            log("Nessuna delle ${profiles.size} connessioni salvate ha risposto.")
            return
        }
        val profile = profiles[attemptIndex]
        authenticated = false
        binding.textPcAddress.text = if (profiles.size > 1) {
            "${profile.name} (${profile.ip}) — tentativo ${attemptIndex + 1}/${profiles.size}"
        } else {
            "${profile.name} (${profile.ip})"
        }
        setConnStatus(false, "connessione...")
        log("Connessione a ${profile.ip} ...")

        val request = Request.Builder().url("ws://${profile.ip}:8765").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "auth").put("token", profile.token).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runOnUiThread { handleServerMessage(text, profile) }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {}

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                if (authenticated) runOnUiThread { setConnStatus(false, "disconnesso") }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread {
                    log("${profile.name}: non raggiungibile (${t.message})")
                    attemptIndex++
                    tryNextProfile()
                }
            }
        })
    }

    private fun handleServerMessage(text: String, profile: ConnectionProfile) {
        val json = try { JSONObject(text) } catch (e: Exception) { return }
        when (json.optString("type")) {
            "auth_ok" -> {
                authenticated = true
                activeIp = profile.ip
                activeToken = profile.token
                setConnStatus(true, "connesso")
                log("Connesso a ${profile.name}.")
                Toast.makeText(this, "Connessione riuscita", Toast.LENGTH_SHORT).show()
                webSocket?.send(JSONObject().put("type", "service_status").toString())
            }
            "auth_error" -> {
                log("${profile.name}: token non valido, provo la successiva")
                attemptIndex++
                tryNextProfile()
            }
            "service_status_result" -> {
                val installed = json.optBoolean("installed")
                binding.textSystemStatus.text =
                    if (installed) "Servizio UAC attivo" else "Wake / VPN / servizio"
            }
        }
    }

    private fun setConnStatus(ok: Boolean, label: String) {
        binding.textConnStatus.text = label
        binding.dotConnStatus.setBackgroundResource(
            if (ok) R.drawable.bg_dot_online else R.drawable.bg_remote_circle_mini
        )
    }

    private fun log(message: String) {
        binding.textStatus.text = message
    }

    override fun onDestroy() {
        webSocket?.close(1000, "App chiusa")
        super.onDestroy()
    }
}
