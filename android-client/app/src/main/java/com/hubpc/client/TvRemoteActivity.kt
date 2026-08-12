package com.hubpc.client

import android.graphics.BitmapFactory
import android.os.Bundle
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityTvRemoteBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONArray
import org.json.JSONObject

/** Telecomando TV (replica fedele del LG Magic Remote) e Smart Share riuniti
 * in un'unica schermata, con selettore in basso per passare dall'uno all'altro. */
class TvRemoteActivity : AppCompatActivity() {

    private lateinit var binding: ActivityTvRemoteBinding
    private val httpClient = OkHttpClient()
    private var webSocket: WebSocket? = null
    private var authenticated = false
    private var lastInputs: JSONArray? = null
    private var onRemoteTab = true

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityTvRemoteBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val ip = intent.getStringExtra("ip").orEmpty()
        val token = intent.getStringExtra("token").orEmpty()

        wireHelp()
        wireRemoteButtons()
        wireSmartShare()
        wireTabs()

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
                    "CERCA TV IN RETE: trova le TV/renderer DLNA raggiungibili sulla stessa rete Wi-Fi del PC. " +
                        "Scrivi il nome di un file presente nella cartella media/ del PC e premi RIPRODUCI SU TV per mandarlo in riproduzione sulla prima TV trovata."
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
                sendCommand(JSONObject().put("type", "cast_play").put("file", file).put("renderer_index", 0))
                log("Inviato: riproduci $file")
            }
        }
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
                if (renderers == null || renderers.length() == 0) {
                    binding.textRenderers.text = "nessuna TV trovata"
                } else {
                    val names = (0 until renderers.length()).joinToString(", ") { renderers.getString(it) }
                    binding.textRenderers.text = names
                }
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
        super.onDestroy()
    }
}
