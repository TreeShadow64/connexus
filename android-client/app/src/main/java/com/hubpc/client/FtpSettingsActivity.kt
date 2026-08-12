package com.hubpc.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.net.Uri
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityFtpSettingsBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.net.NetworkInterface
import java.net.ServerSocket

/** Pagina dedicata alla condivisione FTP di TUTTO lo storage del telefono
 * (non una cartella scelta: quella e' "File del telefono", un'altra cosa).
 * Deliberatamente occupa lo schermo finche' resta attiva: si esce solo
 * fermando la condivisione, cosi' non capita di dimenticarla accesa. */
class FtpSettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivityFtpSettingsBinding
    private var webSocket: WebSocket? = null
    private var authenticated = false
    private var ftpServer: FtpServer? = null
    private var sharing = false
    private var currentAddress = ""

    private var ip = ""
    private var token = ""

    private val backCallback = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {
            Toast.makeText(
                this@FtpSettingsActivity, "Ferma la condivisione per uscire da questa pagina", Toast.LENGTH_SHORT
            ).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFtpSettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        onBackPressedDispatcher.addCallback(this, backCallback)

        ip = intent.getStringExtra("ip").orEmpty()
        token = intent.getStringExtra("token").orEmpty()

        binding.helpFtp.setOnClickListener {
            HelpDialogs.show(
                this, "FTP",
                "Accende un piccolo server sul telefono che espone TUTTO lo storage (non una cartella scelta) come " +
                    "unita' di rete, raggiungibile dal PC o da qualsiasi programma FTP all'indirizzo mostrato qui " +
                    "(tocca per copiarlo). IMPOSTAZIONI (disponibile solo a condivisione ferma) permette password, " +
                    "porta fissa o casuale, sola lettura e se tenere lo schermo acceso.\n\n" +
                    "E' momentanea per scelta: resti su questa pagina finche' e' attiva, e si ferma da sola se esci — " +
                    "cosi' non capita di lasciarla dimenticata accesa."
            )
        }

        binding.textFtpAddress.setOnClickListener { copyAddress() }
        binding.buttonFtpToggle.setOnClickListener { if (sharing) stopSharing() else startSharingFlow() }
        binding.buttonFtpAdvanced.setOnClickListener {
            if (!sharing) startActivity(Intent(this, FtpAdvancedSettingsActivity::class.java))
        }

        updateAddressText()
        if (ip.isNotEmpty() && token.isNotEmpty()) {
            connect(ip, token)
        }
    }

    override fun onResume() {
        super.onResume()
        if (!sharing) updateAddressText()
    }

    private fun updateAddressText() {
        val prefs = getSharedPreferences(HubApplication.PREFS_NAME, MODE_PRIVATE)
        val port = prefs.getInt(FtpAdvancedSettingsActivity.PREF_FTP_PORT, FtpAdvancedSettingsActivity.DEFAULT_FTP_PORT)
        val localIp = localIpAddress() ?: "<IP del telefono>"
        binding.textFtpAddress.text = "ftp://$localIp:$port"
    }

    private fun copyAddress() {
        val manager = getSystemService(ClipboardManager::class.java)
        manager.setPrimaryClip(ClipData.newPlainText("Indirizzo FTP", currentAddress.ifEmpty { binding.textFtpAddress.text }))
        Toast.makeText(this, "Indirizzo copiato", Toast.LENGTH_SHORT).show()
    }

    private fun localIpAddress(): String? = try {
        NetworkInterface.getNetworkInterfaces().asSequence()
            .flatMap { it.inetAddresses.asSequence() }
            .firstOrNull { !it.isLoopbackAddress && it.hostAddress?.contains(':') == false }
            ?.hostAddress
    } catch (e: Exception) {
        null
    }

    private fun hasAllFilesAccess(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.R || Environment.isExternalStorageManager()

    private fun requestAllFilesAccess() {
        Toast.makeText(
            this, "Concedi l'accesso a tutti i file, poi torna qui e premi di nuovo AVVIA", Toast.LENGTH_LONG
        ).show()
        try {
            startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:$packageName")))
        } catch (e: android.content.ActivityNotFoundException) {
            startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
        }
    }

    /** Trova una porta libera facendo assegnare al sistema operativo un
     * ServerSocket su porta 0: e' il modo standard per farsi dare una porta
     * casuale ma davvero libera, senza indovinare. */
    private fun findFreePort(): Int = ServerSocket(0).use { it.localPort }

    private fun startSharingFlow() {
        if (!hasAllFilesAccess()) {
            requestAllFilesAccess()
            return
        }
        val prefs = getSharedPreferences(HubApplication.PREFS_NAME, MODE_PRIVATE)
        val anonymous = prefs.getBoolean(FtpAdvancedSettingsActivity.PREF_FTP_ANONYMOUS, true)
        val password = if (anonymous) "" else prefs.getString(FtpAdvancedSettingsActivity.PREF_FTP_PASSWORD, "").orEmpty()
        val readOnly = prefs.getBoolean(FtpAdvancedSettingsActivity.PREF_FTP_READ_ONLY, false)
        val keepScreenOn = prefs.getBoolean(FtpAdvancedSettingsActivity.PREF_FTP_KEEP_SCREEN_ON, true)
        val randomPort = prefs.getBoolean(FtpAdvancedSettingsActivity.PREF_FTP_RANDOM_PORT, false)
        val port = if (randomPort) findFreePort()
        else prefs.getInt(FtpAdvancedSettingsActivity.PREF_FTP_PORT, FtpAdvancedSettingsActivity.DEFAULT_FTP_PORT)

        val root = Environment.getExternalStorageDirectory()
        val server = FtpServer(root, password = password, readOnly = readOnly, controlPort = port)
        try {
            server.start()
            ftpServer = server
            sharing = true
            backCallback.isEnabled = true
            if (keepScreenOn) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            binding.buttonFtpToggle.text = "INTERROMPI"
            binding.buttonFtpAdvanced.alpha = 0.4f
            val access = if (password.isEmpty()) "accesso anonimo" else "con password"
            val mode = if (readOnly) "sola lettura" else "lettura/scrittura"
            binding.textFtpStatus.text = "condivisione attiva ($access, $mode)"
            val localIp = localIpAddress() ?: "<IP del telefono>"
            currentAddress = "ftp://$localIp:$port"
            binding.textFtpAddress.text = currentAddress
            sendShareStatus(sharing = true, ftpPort = port)
        } catch (e: Exception) {
            binding.textFtpStatus.text = "avvio fallito: ${e.message}"
        }
    }

    private fun stopSharing() {
        ftpServer?.stop()
        ftpServer = null
        sharing = false
        backCallback.isEnabled = false
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        binding.buttonFtpToggle.text = "AVVIA"
        binding.buttonFtpAdvanced.alpha = 1f
        binding.textFtpStatus.text = "condivisione disattivata"
        updateAddressText()
        sendShareStatus(sharing = false)
    }

    private fun connect(ip: String, token: String) {
        val client = OkHttpClient()
        val request = Request.Builder().url("ws://$ip:8765").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "auth").put("token", token).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val json = try { JSONObject(text) } catch (e: Exception) { return }
                if (json.optString("type") == "auth_ok") authenticated = true
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {}

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {}
        })
    }

    private fun sendShareStatus(sharing: Boolean, ftpPort: Int = FtpAdvancedSettingsActivity.DEFAULT_FTP_PORT) {
        if (!authenticated) return
        webSocket?.send(
            JSONObject()
                .put("type", "share_status")
                .put("sharing", sharing)
                .put("name", "${Build.MANUFACTURER} ${Build.MODEL}")
                .put("folder", "tutto lo storage")
                .put("ftp_port", ftpPort)
                .toString()
        )
    }

    override fun onDestroy() {
        ftpServer?.stop()
        if (sharing) sendShareStatus(sharing = false)
        webSocket?.close(1000, "Chiuso")
        super.onDestroy()
    }
}
