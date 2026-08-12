package com.hubpc.client

import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.view.Gravity
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.google.android.gms.cast.MediaInfo
import com.google.android.gms.cast.MediaLoadRequestData
import com.google.android.gms.cast.framework.CastButtonFactory
import com.google.android.gms.cast.framework.CastContext
import com.google.android.gms.cast.framework.CastSession
import com.google.android.gms.cast.framework.SessionManagerListener
import com.hubpc.client.databinding.ActivityTvRemoteBinding
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Telecomando TV (replica fedele del LG Magic Remote) e Smart Share riuniti
 * in un'unica schermata, con selettore in basso per passare dall'uno all'altro. */
class TvRemoteActivity : AppCompatActivity() {

    private lateinit var binding: ActivityTvRemoteBinding
    private val httpClient = OkHttpClient()
    private var webSocket: WebSocket? = null
    private var authenticated = false
    private var lastInputs: JSONArray? = null
    private var onRemoteTab = true
    private var pcIp = ""
    private var pcToken = ""
    private var rendererNames: List<String> = emptyList()
    private var selectedRendererIndex = 0

    private var castSession: CastSession? = null
    private val castSessionListener = object : SessionManagerListener<CastSession> {
        override fun onSessionStarted(session: CastSession, sessionId: String) = onCastConnected(session)
        override fun onSessionResumed(session: CastSession, wasSuspended: Boolean) = onCastConnected(session)
        override fun onSessionEnded(session: CastSession, error: Int) = onCastDisconnected()
        override fun onSessionSuspended(session: CastSession, reason: Int) = onCastDisconnected()
        override fun onSessionStarting(session: CastSession) {}
        override fun onSessionStartFailed(session: CastSession, error: Int) {}
        override fun onSessionEnding(session: CastSession) {}
        override fun onSessionResuming(session: CastSession, sessionId: String) {}
        override fun onSessionResumeFailed(session: CastSession, error: Int) {}
    }

    private val pickGalleryFile = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) uploadAndCast(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityTvRemoteBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val ip = intent.getStringExtra("ip").orEmpty()
        val token = intent.getStringExtra("token").orEmpty()
        pcIp = ip
        pcToken = token

        wireHelp()
        wireRemoteButtons()
        wireSmartShare()
        wireTabs()
        setUpGoogleCast()

        binding.buttonTvPair.setOnClickListener {
            sendCommand(JSONObject().put("type", "tv_pair"))
            log("Abbinamento TV in corso... accetta il prompt sullo schermo della TV")
        }

        binding.buttonTvRefreshApps.setOnClickListener {
            sendCommand(JSONObject().put("type", "tv_list_apps"))
            log("Aggiornamento elenco app...")
        }

        connect(ip, token)
    }

    private fun wireTabs() {
        binding.tabRemote.setOnClickListener { showTab(remote = true) }
        binding.tabSmartShare.setOnClickListener { showTab(remote = false) }
    }

    private fun showTab(remote: Boolean) {
        onRemoteTab = remote
        binding.pageRemote.visibility = if (remote) android.view.View.VISIBLE else android.view.View.GONE
        binding.pageSmartShare.visibility = if (remote) android.view.View.GONE else android.view.View.VISIBLE
        binding.textPageTitle.text = if (remote) "TELECOMANDO" else "SMART SHARE"
        binding.tabRemoteLabel.setTextColor(getColor(if (remote) R.color.cyan else R.color.text_faint))
        binding.tabSmartShareLabel.setTextColor(getColor(if (remote) R.color.text_faint else R.color.cyan))
    }

    private fun wireHelp() {
        binding.helpRemote.setOnClickListener {
            if (onRemoteTab) {
                HelpDialogs.show(
                    this, "Telecomando",
                    "Replica 1:1 il telecomando fisico della TV: stessa disposizione di tastierino numerico, volume, canale, rotella OK, tasti colorati e scorciatoie app. " +
                        "Se la TV non risponde, tocca ABBINA TV e accetta il prompt che compare sullo schermo.\n\n" +
                        "App installate: elenco reale delle app sulla TV con l'icona presa direttamente da essa. Tocca un'icona per aprirla."
                )
            } else {
                HelpDialogs.show(
                    this, "Smart share",
                    "L'icona di Google Cast in alto ti fa scegliere una TV moderna (funziona sui Samsung 2024+, LG, Chromecast — lo standard attuale). " +
                        "Se e' connessa, viene usata automaticamente al posto del DLNA.\n\n" +
                        "CERCA TV IN RETE trova invece le TV/renderer DLNA sulla stessa rete (LG webOS e la maggior parte delle TV piu' datate; i Samsung recenti hanno tolto il supporto DLNA). " +
                        "Tocca una TV nell'elenco per selezionarla.\n\n" +
                        "SCEGLI DA GALLERIA carica una foto/video dal telefono al PC e lo manda subito in riproduzione, sulla TV Cast connessa o su quella DLNA selezionata. " +
                        "In alternativa scrivi il nome di un file gia' presente in media/ sul PC."
                )
            }
        }
    }

    private fun wireRemoteButtons() {
        binding.buttonPower.setOnClickListener { sendTvCommand("power_off") }
        binding.buttonInput.setOnClickListener { requestInputs() }
        binding.buttonQuickInput.setOnClickListener { requestInputs() }

        for ((id, view) in listOf(
            "1" to binding.buttonNum1, "2" to binding.buttonNum2, "3" to binding.buttonNum3,
            "4" to binding.buttonNum4, "5" to binding.buttonNum5, "6" to binding.buttonNum6,
            "7" to binding.buttonNum7, "8" to binding.buttonNum8, "9" to binding.buttonNum9,
            "0" to binding.buttonNum0
        )) {
            view.setOnClickListener { sendTvButton(id) }
        }

        binding.buttonTvList.setOnClickListener { sendTvButton("list") }
        binding.buttonTvDash.setOnClickListener { sendTvButton("dash") }

        binding.buttonVolUp.setOnClickListener { sendTvCommand("volume_up") }
        binding.buttonVolDown.setOnClickListener { sendTvCommand("volume_down") }
        binding.buttonMute.setOnClickListener { sendTvCommand("mute") }
        binding.buttonChUp.setOnClickListener { sendTvCommand("channel_up") }
        binding.buttonChDown.setOnClickListener { sendTvCommand("channel_down") }
        binding.buttonMic.setOnClickListener {
            Toast.makeText(this, "Microfono non disponibile da questa app", Toast.LENGTH_SHORT).show()
        }

        binding.buttonHome.setOnClickListener { sendTvDpad("home") }
        binding.buttonSettings.setOnClickListener { sendTvButton("menu") }

        binding.buttonDpadUp.setOnClickListener { sendTvDpad("up") }
        binding.buttonDpadDown.setOnClickListener { sendTvDpad("down") }
        binding.buttonDpadLeft.setOnClickListener { sendTvDpad("left") }
        binding.buttonDpadRight.setOnClickListener { sendTvDpad("right") }
        binding.buttonDpadEnter.setOnClickListener { sendTvDpad("enter") }

        binding.buttonDpadBack.setOnClickListener { sendTvDpad("back") }
        binding.buttonGuide.setOnClickListener { sendTvButton("guide") }

        binding.buttonQuickNetflix.setOnClickListener {
            sendCommand(JSONObject().put("type", "tv_launch_app").put("app_id", "netflix"))
            log("Avvio: Netflix")
        }
        binding.buttonQuickPrime.setOnClickListener {
            sendCommand(JSONObject().put("type", "tv_launch_app").put("app_id", "amazon"))
            log("Avvio: Prime Video")
        }

        binding.buttonTvRed.setOnClickListener { sendTvButton("red") }
        binding.buttonTvGreen.setOnClickListener { sendTvButton("green") }
        binding.buttonTvYellow.setOnClickListener { sendTvButton("yellow") }
        binding.buttonTvBlue.setOnClickListener { sendTvButton("blue") }

        binding.buttonPlay.setOnClickListener { sendTvButton("play") }
        binding.buttonPause.setOnClickListener { sendTvButton("pause") }
    }

    private fun wireSmartShare() {
        binding.buttonCastDiscover.setOnClickListener {
            sendCommand(JSONObject().put("type", "cast_discover"))
            log("Ricerca TV in corso...")
        }
        binding.buttonCastPlay.setOnClickListener {
            val file = binding.editCastFile.text.toString().trim()
            if (file.isNotEmpty()) {
                val mime = java.net.URLConnection.guessContentTypeFromName(file) ?: "video/mp4"
                castToTv(file, mime)
            }
        }
        binding.buttonCastPickFile.setOnClickListener { pickGalleryFile.launch("*/*") }
        binding.buttonCastPause.setOnClickListener {
            val session = castSession
            if (session != null) {
                session.remoteMediaClient?.pause()
                log("Google Cast: in pausa")
            } else {
                sendCommand(JSONObject().put("type", "cast_pause").put("renderer_index", selectedRendererIndex))
            }
        }
        binding.buttonCastStop.setOnClickListener {
            val session = castSession
            if (session != null) {
                session.remoteMediaClient?.stop()
                log("Google Cast: fermato")
            } else {
                sendCommand(JSONObject().put("type", "cast_stop").put("renderer_index", selectedRendererIndex))
            }
        }
    }

    private fun setUpGoogleCast() {
        try {
            CastButtonFactory.setUpMediaRouteButton(applicationContext, binding.mediaRouteButton)
            val castContext = CastContext.getSharedInstance(this)
            castContext.sessionManager.addSessionManagerListener(castSessionListener, CastSession::class.java)
            castSession = castContext.sessionManager.currentCastSession
            if (castSession?.isConnected == true) onCastConnected(castSession!!)
        } catch (e: Exception) {
            // Play Services / Google Cast non disponibile su questo dispositivo:
            // Smart Share resta comunque utilizzabile via DLNA.
            binding.textCastStatus.text = "Google Cast non disponibile su questo dispositivo"
            binding.mediaRouteButton.visibility = android.view.View.GONE
        }
    }

    private fun onCastConnected(session: CastSession) {
        castSession = session
        binding.textCastStatus.text = "Google Cast: connesso a ${session.castDevice?.friendlyName ?: "TV"}"
    }

    private fun onCastDisconnected() {
        castSession = null
        binding.textCastStatus.text = "Google Cast: non connesso"
    }

    /** Manda un file in riproduzione sulla TV: se e' attiva una sessione
     * Google Cast la usa (standard moderno, funziona anche sui Samsung
     * recenti), altrimenti ricade sulla TV DLNA selezionata sotto. */
    private fun castToTv(filename: String, mimeType: String) {
        val session = castSession
        if (session != null) {
            val mediaUrl = "http://$pcIp:8766/${Uri.encode(filename)}"
            val mediaInfo = MediaInfo.Builder(mediaUrl)
                .setStreamType(MediaInfo.STREAM_TYPE_BUFFERED)
                .setContentType(mimeType)
                .build()
            session.remoteMediaClient?.load(MediaLoadRequestData.Builder().setMediaInfo(mediaInfo).build())
            log("Google Cast: riproduzione di $filename")
        } else {
            sendCommand(JSONObject().put("type", "cast_play").put("file", filename).put("renderer_index", selectedRendererIndex))
            log("DLNA: inviato $filename")
        }
    }

    private fun renderRenderers() {
        binding.layoutRenderers.removeAllViews()
        if (rendererNames.isEmpty()) {
            binding.layoutRenderers.addView(TextView(this).apply {
                text = "nessuna TV trovata"
                setTextColor(getColor(R.color.text_faint))
                typeface = Typeface.MONOSPACE
                textSize = 11f
            })
            return
        }
        for ((index, name) in rendererNames.withIndex()) {
            binding.layoutRenderers.addView(TextView(this).apply {
                text = if (index == selectedRendererIndex) "● $name" else "○ $name"
                setTextColor(getColor(if (index == selectedRendererIndex) R.color.cyan else R.color.text_dim))
                typeface = Typeface.MONOSPACE
                textSize = 12f
                setPadding(4, 10, 4, 10)
                setOnClickListener {
                    selectedRendererIndex = index
                    renderRenderers()
                }
            })
        }
    }

    /** Carica il file scelto dalla galleria sul PC (endpoint /cast-upload/)
     * cosi' non serve che sia gia' presente in media/, poi lo manda in
     * riproduzione sulla TV selezionata. */
    private fun uploadAndCast(uri: Uri) {
        if (pcIp.isEmpty() || pcToken.isEmpty()) {
            log("Non connesso al PC")
            return
        }
        val filename = queryFileName(uri) ?: "condiviso_${System.currentTimeMillis()}"
        log("Caricamento di $filename sul PC...")
        Thread {
            try {
                val tempFile = File(cacheDir, filename)
                contentResolver.openInputStream(uri)?.use { input ->
                    tempFile.outputStream().use { output -> input.copyTo(output) }
                } ?: throw java.io.IOException("Impossibile leggere il file scelto")

                val contentType = contentResolver.getType(uri)
                val body = tempFile.asRequestBody(contentType?.toMediaTypeOrNull())
                val encodedName = Uri.encode(filename)
                val request = Request.Builder()
                    .url("http://$pcIp:8766/cast-upload/$encodedName?token=$pcToken")
                    .put(body)
                    .build()
                val response = httpClient.newCall(request).execute()
                tempFile.delete()
                if (!response.isSuccessful) throw java.io.IOException("HTTP ${response.code}")

                runOnUiThread {
                    log("Caricato, avvio riproduzione...")
                    castToTv(filename, contentType ?: "video/mp4")
                }
            } catch (e: Exception) {
                runOnUiThread { log("Caricamento fallito: ${e.message}") }
            }
        }.start()
    }

    private fun queryFileName(uri: Uri): String? {
        contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (nameIndex >= 0 && cursor.moveToFirst()) return cursor.getString(nameIndex)
        }
        return null
    }

    private fun requestInputs() {
        sendCommand(JSONObject().put("type", "tv_list_inputs"))
        log("Ricerca sorgenti...")
    }

    private fun showInputsPicker(inputs: JSONArray) {
        if (inputs.length() == 0) {
            Toast.makeText(this, "Nessuna sorgente trovata", Toast.LENGTH_SHORT).show()
            return
        }
        val labels = (0 until inputs.length()).map { inputs.getJSONObject(it).optString("label") }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle("Sorgente")
            .setItems(labels) { _, which ->
                val id = inputs.getJSONObject(which).optString("id")
                sendCommand(JSONObject().put("type", "tv_switch_input").put("input_id", id))
                log("Sorgente: ${labels[which]}")
            }
            .show()
    }

    private fun populateApps(apps: JSONArray) {
        binding.layoutTvApps.removeAllViews()
        for (i in 0 until apps.length()) {
            val app = apps.getJSONObject(i)
            val title = app.optString("title")
            val id = app.optString("id")
            val icon = app.optString("icon")
            binding.layoutTvApps.addView(makeAppTile(title, id, icon))
        }
    }

    private fun makeAppTile(title: String, appId: String, iconUrl: String): LinearLayout {
        val tile = LinearLayout(this)
        tile.orientation = LinearLayout.VERTICAL
        val density = resources.displayMetrics.density
        val tileParams = LinearLayout.LayoutParams((56 * density).toInt(), LinearLayout.LayoutParams.WRAP_CONTENT)
        tileParams.marginEnd = (14 * density).toInt()
        tile.layoutParams = tileParams
        tile.gravity = android.view.Gravity.CENTER_HORIZONTAL

        val image = ImageView(this)
        val size = (44 * density).toInt()
        val imageParams = LinearLayout.LayoutParams(size, size)
        image.layoutParams = imageParams
        image.setBackgroundResource(R.drawable.bg_app_tile)
        image.scaleType = ImageView.ScaleType.CENTER_CROP
        image.clipToOutline = true
        image.isClickable = true
        image.isFocusable = true
        image.setOnClickListener {
            sendCommand(JSONObject().put("type", "tv_launch_app").put("app_id", appId))
            log("Avvio: $title")
        }
        tile.addView(image)
        if (iconUrl.isNotEmpty()) loadIcon(iconUrl, image)

        val label = TextView(this)
        label.text = title
        label.textSize = 8f
        label.setTextColor(getColor(R.color.text_faint))
        label.typeface = android.graphics.Typeface.MONOSPACE
        label.gravity = android.view.Gravity.CENTER_HORIZONTAL
        label.maxLines = 1
        val labelParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        labelParams.topMargin = (4 * density).toInt()
        label.layoutParams = labelParams
        tile.addView(label)

        return tile
    }

    private fun loadIcon(url: String, target: ImageView) {
        Thread {
            try {
                val request = Request.Builder().url(url).build()
                httpClient.newCall(request).execute().use { response ->
                    val bytes = response.body?.bytes() ?: return@use
                    val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return@use
                    runOnUiThread { target.setImageBitmap(bitmap) }
                }
            } catch (e: Exception) {
                // icona non disponibile: resta il segnaposto
            }
        }.start()
    }

    private fun sendCommand(json: JSONObject) {
        if (!authenticated) {
            log("Non ancora autenticato")
            return
        }
        webSocket?.send(json.toString())
    }

    private fun sendTvCommand(action: String) = sendCommand(JSONObject().put("type", "tv_command").put("action", action))
    private fun sendTvDpad(direction: String) = sendCommand(JSONObject().put("type", "tv_dpad").put("direction", direction))
    private fun sendTvButton(name: String) = sendCommand(JSONObject().put("type", "tv_button").put("name", name))

    private fun connect(ip: String, token: String) {
        val request = Request.Builder().url("ws://$ip:8765").build()
        webSocket = httpClient.newWebSocket(request, object : WebSocketListener() {
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
                binding.textPairStatus.text = "connesso — se i tasti non rispondono, riabbina la TV"
                log("Connesso.")
                sendCommand(JSONObject().put("type", "tv_list_apps"))
            }
            "auth_error" -> log("Autenticazione fallita")
            "tv_ok" -> {
                binding.textPairStatus.text = "TV abbinata"
                log(json.optString("message"))
            }
            "tv_error" -> log("Errore TV: ${json.optString("message")}")
            "tv_apps" -> {
                val apps = json.optJSONArray("apps")
                if (apps != null) {
                    populateApps(apps)
                    log("Trovate ${apps.length()} app.")
                }
            }
            "tv_inputs" -> {
                val inputs = json.optJSONArray("inputs")
                if (inputs != null) {
                    lastInputs = inputs
                    showInputsPicker(inputs)
                }
            }
            "cast_discover_result" -> {
                val renderers = json.optJSONArray("renderers")
                rendererNames = if (renderers == null) emptyList()
                    else (0 until renderers.length()).map { renderers.getString(it) }
                selectedRendererIndex = 0
                renderRenderers()
            }
            "cast_ok" -> log(json.optString("message"))
            "cast_error" -> log("Errore: ${json.optString("message")}")
        }
    }

    private fun log(message: String) {
        binding.textStatus.text = message
    }

    override fun onDestroy() {
        webSocket?.close(1000, "Chiuso")
        try {
            CastContext.getSharedInstance(this).sessionManager
                .removeSessionManagerListener(castSessionListener, CastSession::class.java)
        } catch (e: Exception) {
            // Google Cast non era disponibile: niente da rimuovere
        }
        super.onDestroy()
    }
}
