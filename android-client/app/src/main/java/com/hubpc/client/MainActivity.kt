package com.hubpc.client

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.firebase.auth.FirebaseAuth
import com.hubpc.client.databinding.ActivityMainBinding
import com.hubpc.client.ui.HudSession
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Overview: la stessa schermata iniziale della dashboard PC (anelli con
 * orologio e pannelli dei moduli). Le combinazioni IP/token sono configurate
 * in OnboardingActivity (la prima volta) o in SettingsActivity — qui si tenta
 * la connessione automatica partendo dalla primaria, passando alla successiva
 * se una fallisce, e ci si smista verso le sezioni dedicate (ciascuna con un
 * proprio canale WebSocket). */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    // 3 s al posto dei 10 predefiniti: con l'indirizzo vecchio ormai morto l'attesa si sentiva a ogni tentativo
    private val client = OkHttpClient.Builder()
        .connectTimeout(3, java.util.concurrent.TimeUnit.SECONDS)
        .build()
    private var webSocket: WebSocket? = null
    private var authenticated = false
    private var profiles: List<ConnectionProfile> = emptyList()
    private var attemptIndex = 0
    private var activeIp: String = ""
    private var activeToken: String = ""
    private var discoveryDone = false          // ricerca del PC in rete gia' tentata in questo giro
    private val retryHandler = Handler(Looper.getMainLooper())
    private val retryRunnable = Runnable { if (!authenticated && !isFinishing) startConnecting(checkUpdate = false) }

    private val clockHandler = Handler(Looper.getMainLooper())
    private val clockTick = object : Runnable {
        override fun run() {
            if (!::binding.isInitialized) return
            val now = Date()
            binding.clockLabel.text = SimpleDateFormat("HH:mm:ss", Locale.ITALIAN).format(now)
            binding.dateLabel.text = SimpleDateFormat("EEE dd MMM", Locale.ITALIAN).format(now).uppercase(Locale.ITALIAN)
            clockHandler.postDelayed(this, 1000)
        }
    }

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

        binding.cardPcControl.setOnClickListener { openSection(PcControlActivity::class.java) }
        binding.cardScreen.setOnClickListener { openSection(ScreenActivity::class.java) }
        binding.cardProjector.setOnClickListener { openSection(ProjectorActivity::class.java) }
        binding.cardVirtualCamera.setOnClickListener { openSection(VirtualCameraActivity::class.java) }
        binding.cardUvcCamera.setOnClickListener { openSection(UvcCameraActivity::class.java) }
        binding.cardTaskManager.setOnClickListener { openSection(TaskManagerActivity::class.java) }
        // Impostazioni e Wake-on-LAN servono proprio quando il PC NON e'
        // raggiungibile (sveglia da spento, cambio connessione), quindi non
        // passano dal gate di openSection().
        binding.cardSystem.setOnClickListener { openUngated(SystemActivity::class.java) }
        // accensione e spegnimento sono un solo popup (Avvia / Sospendi / Riavvia / Spegni): da
        // spento funziona "Avvia" (Wake-on-LAN, non serve il collegamento)
        binding.cardWake.setOnClickListener {
            startActivity(
                Intent(this, PcControlActivity::class.java)
                    .putExtra("ip", activeIp)
                    .putExtra("token", activeToken)
                    .putExtra("openPower", true)
            )
        }

        binding.topBar.onHelp = {
            HelpDialogs.show(
                this, "Overview",
                "Qui vedi lo stato del collegamento con il PC e accedi ai moduli, come nella dashboard sul computer.\n\n" +
                    "Mouse / Tastiera: usa il telefono come touchpad e tastiera per il PC.\n\n" +
                    "Schermo PC: specchia il monitor principale sul telefono, con zoom.\n\n" +
                    "Projector: il contrario — manda lo schermo del telefono al PC.\n\n" +
                    "Virtual Camera: la fotocamera del telefono diventa una webcam del PC (serve OBS Studio).\n\n" +
                    "Camera UVC: guarda sul telefono una webcam USB collegata al PC.\n\n" +
                    "In basso: TV, File & Rete, Impostazioni e Trova dispositivo."
            )
        }

        startConnecting()
    }

    override fun onResume() {
        super.onResume()
        if (::binding.isInitialized) {
            clockHandler.removeCallbacks(clockTick)
            clockHandler.post(clockTick)
            val fresh = ConnectionProfiles.load(this)
            if (fresh != profiles) startConnecting()
        }
    }

    override fun onPause() {
        clockHandler.removeCallbacks(clockTick)
        retryHandler.removeCallbacks(retryRunnable)
        super.onPause()
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

    private fun openUngated(target: Class<*>) {
        startActivity(
            Intent(this, target)
                .putExtra("ip", activeIp)
                .putExtra("token", activeToken)
        )
    }

    private fun startConnecting(checkUpdate: Boolean = true) {
        profiles = ConnectionProfiles.load(this)
        if (checkUpdate) com.hubpc.client.ui.AppUpdater.check(this)
        if (profiles.isEmpty()) {
            binding.textPcAddress.text = "nessuna connessione configurata"
            setConnStatus(false, "non configurato")
            return
        }
        attemptIndex = 0
        discoveryDone = false
        retryHandler.removeCallbacks(retryRunnable)
        tryNextProfile()
    }

    /** Nessun indirizzo salvato risponde: il PC ha probabilmente un IP nuovo. Lo cerco in rete e
     * riprovo con quello trovato (il token decide se e' davvero il mio PC). */
    private fun discoverAndRetry() {
        discoveryDone = true
        setConnStatus(false, "connessione...")
        binding.textPcAddress.text = "cerco il PC in rete..."
        log("Cerco il PC sulla rete (l'indirizzo e' cambiato?)...")
        Thread {
            val found = PcLocator.discover()
            runOnUiThread {
                if (isFinishing) return@runOnUiThread
                if (found.isEmpty()) {
                    setConnStatus(false, "nessun pc raggiungibile")
                    log("Nessun PC ha risposto in rete: riprovo tra poco.")
                    retryHandler.postDelayed(retryRunnable, 15000)
                    return@runOnUiThread
                }
                val first = profiles.firstOrNull() ?: return@runOnUiThread
                log("Trovato in rete: " + found.joinToString { it.ip })
                // per ogni PC trovato provo i token salvati: auth_ok conferma quello giusto
                val candidates = mutableListOf<ConnectionProfile>()
                for (f in found) for (p in profiles) {
                    candidates.add(ConnectionProfile(p.name.ifEmpty { first.name }, f.ip, p.token))
                }
                profiles = candidates
                attemptIndex = 0
                tryNextProfile()
            }
        }.start()
    }

    private fun tryNextProfile() {
        if (attemptIndex >= profiles.size) {
            if (!discoveryDone) {
                discoverAndRetry()
                return
            }
            setConnStatus(false, "nessun pc raggiungibile")
            log("Nessuna delle ${profiles.size} connessioni salvate ha risposto.")
            retryHandler.postDelayed(retryRunnable, 15000)
            return
        }
        val profile = profiles[attemptIndex]
        authenticated = false
        binding.textPcAddress.text = if (profiles.size > 1) {
            "${profile.name} (${profile.ip}) — tentativo ${attemptIndex + 1}/${profiles.size}"
        } else {
            "${profile.name} (${profile.ip})"
        }
        binding.topBar.setSubtitle("HOST // ${profile.name}")
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

    /** Salva l'indirizzo con cui ha risposto il PC al posto di quello vecchio (stesso token). */
    private fun rememberAddress(connected: ConnectionProfile) {
        val saved = ConnectionProfiles.load(this)
        var changed = false
        val updated = saved.map {
            if (it.token == connected.token && it.ip != connected.ip) {
                changed = true
                ConnectionProfile(it.name, connected.ip, it.token)
            } else {
                it
            }
        }
        if (changed) {
            ConnectionProfiles.save(this, updated)
            profiles = updated
            log("Indirizzo del PC aggiornato: " + connected.ip)
        }
    }

    private fun handleServerMessage(text: String, profile: ConnectionProfile) {
        val json = try { JSONObject(text) } catch (e: Exception) { return }
        when (json.optString("type")) {
            "auth_ok" -> {
                authenticated = true
                activeIp = profile.ip
                activeToken = profile.token
                HudSession.ip = profile.ip
                HudSession.token = profile.token
                setConnStatus(true, "connesso")
                log("Connesso a ${profile.name}.")
                rememberAddress(profile)
                webSocket?.send(JSONObject().put("type", "service_status").toString())
            }
            "auth_error" -> {
                log("${profile.name}: token non valido, provo la successiva")
                attemptIndex++
                tryNextProfile()
            }
            "service_status_result" -> {
                val installed = json.optBoolean("installed")
                binding.textSystemStatus.text = if (installed) "installato" else "non installato"
                binding.dotSystem.setBackgroundResource(if (installed) R.drawable.bg_dot_online else R.drawable.bg_dot_warn)
            }
        }
    }

    /** Aggiorna barra, anello e puntini dei pannelli: i moduli che passano dal
     * PC hanno il puntino verde solo se il collegamento e' attivo. */
    private fun setConnStatus(ok: Boolean, label: String) {
        // etichetta corta: quella lunga schiaccerebbe il titolo "CONNEXUS // PHONE"
        binding.topBar.setOnline(
            ok,
            when {
                ok -> "ONLINE"
                label.startsWith("connessione") -> "CONNETTO..."
                else -> "OFFLINE"
            },
        )
        binding.clientsLabel.text = if (ok) "PC CONNESSO" else "NESSUN PC CONNESSO"
        val dot = if (ok) R.drawable.bg_dot_online else R.drawable.bg_dot_off
        for (view: View in listOf(
            binding.dotPcControl, binding.dotScreen, binding.dotProjector,
            binding.dotVirtualCamera, binding.dotUvcCamera, binding.dotTaskManager,
        )) {
            view.setBackgroundResource(dot)
        }
        if (!ok) {
            binding.dotSystem.setBackgroundResource(R.drawable.bg_dot_idle)
            binding.textSystemStatus.text = "verifica in corso"
        }
        binding.dotWake.setBackgroundResource(R.drawable.bg_dot_idle)
    }

    private fun log(message: String) {
        binding.textStatus.text = message
    }

    override fun onDestroy() {
        clockHandler.removeCallbacks(clockTick)
        retryHandler.removeCallbacks(retryRunnable)
        webSocket?.close(1000, "App chiusa")
        super.onDestroy()
    }
}
