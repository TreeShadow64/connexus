package com.hubpc.client

import android.os.Bundle
import android.view.MotionEvent
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityPcControlBinding
import com.hubpc.client.databinding.DialogKeyboardBinding
import com.hubpc.client.databinding.DialogPowerBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import kotlin.math.abs

/** Touchpad e rotella sempre visibili; alimentazione e tastiera (testo, tasti
 * speciali, volume) vivono in due popup separati aperti dalle iconcine in alto,
 * cosi' la vista principale resta solo il mouse. Canale WebSocket dedicato. */
class PcControlActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPcControlBinding
    private var webSocket: WebSocket? = null
    private var authenticated = false
    private var mouseLocked = false
    private var serviceStatusText = "servizio UAC: sconosciuto"
    private var serviceInstalled = false

    private var touchStartX = 0f
    private var touchStartY = 0f
    private var lastX = 0f
    private var lastY = 0f
    private var moved = false

    private var wheelStartY = 0f
    private var wheelLastY = 0f
    private var wheelMoved = false

    private var lastVolumeProgress = 50

    companion object {
        private const val CLICK_MOVE_THRESHOLD = 12f
        private const val MOUSE_SENSITIVITY = 1.5f
        private const val WHEEL_MOVE_THRESHOLD = 10f
        private const val WHEEL_STEP_PX = 24f
        private const val PREFS_NAME = HubApplication.PREFS_NAME
        private const val PREF_MAC = "mac"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityPcControlBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val ip = intent.getStringExtra("ip").orEmpty()
        val token = intent.getStringExtra("token").orEmpty()

        binding.helpPcControl.setOnClickListener {
            HelpDialogs.show(
                this, "Mouse / tastiera",
                "Trascina il riquadro come un touchpad per muovere il mouse del PC, tocca per fare clic sinistro. " +
                    "La barra a destra si trascina su/giu' per scorrere le pagine.\n\n" +
                    "L'icona di alimentazione apre avvio/spegnimento/riavvio/sospensione. " +
                    "L'icona tastiera apre testo, tasti speciali, volume e accetta UAC."
            )
        }

        binding.touchpad.setOnTouchListener { _, event -> handleTouchpad(event) }
        binding.buttonClickLeft.setOnClickListener { sendClick("left") }
        binding.buttonClickMiddle.setOnClickListener { sendClick("middle") }
        binding.buttonClickRight.setOnClickListener { sendClick("right") }
        binding.scrollWheel.setOnTouchListener { _, event -> handleWheel(event) }

        binding.buttonMouseLock.setOnClickListener {
            mouseLocked = !mouseLocked
            sendCommand(JSONObject().put("type", "mouse_lock").put("enabled", mouseLocked))
        }

        binding.buttonOpenPower.setOnClickListener { showPowerDialog() }
        binding.buttonOpenKeyboard.setOnClickListener { showKeyboardDialog() }

        connect(ip, token)
    }

    // --- popup alimentazione ---

    private fun showPowerDialog() {
        val dialogBinding = DialogPowerBinding.inflate(layoutInflater)
        dialogBinding.buttonWakeOnLan.setOnClickListener {
            val mac = getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getString(PREF_MAC, "").orEmpty()
            if (mac.isEmpty()) {
                log("Nessun MAC salvato: vai in Sistema e premi OTTIENI mentre sei connesso")
            } else {
                WolHelper.send(mac) { _, message -> runOnUiThread { log(message) } }
            }
        }
        dialogBinding.buttonSleep.setOnClickListener { confirmPower("sleep", "Sospendere il PC?") }
        dialogBinding.buttonRestart.setOnClickListener { confirmPower("restart", "Riavviare il PC?") }
        dialogBinding.buttonShutdown.setOnClickListener { confirmPower("shutdown", "Spegnere il PC?") }
        AlertDialog.Builder(this)
            .setTitle("Alimentazione PC")
            .setView(dialogBinding.root)
            .setNegativeButton("Chiudi", null)
            .show()
    }

    private fun confirmPower(action: String, message: String) {
        AlertDialog.Builder(this)
            .setTitle("Conferma")
            .setMessage(message)
            .setPositiveButton("Conferma") { _, _ ->
                sendCommand(JSONObject().put("type", "system_power").put("action", action))
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    // --- popup tastiera ---

    private fun showKeyboardDialog() {
        val dialogBinding = DialogKeyboardBinding.inflate(layoutInflater)
        dialogBinding.buttonSendText.setOnClickListener {
            val text = dialogBinding.editText.text.toString()
            if (text.isNotEmpty()) {
                sendCommand(JSONObject().put("type", "text").put("value", text))
                dialogBinding.editText.setText("")
            }
        }
        dialogBinding.buttonEnter.setOnClickListener { sendKey("enter") }
        dialogBinding.buttonBackspace.setOnClickListener { sendKey("backspace") }
        dialogBinding.buttonSpace.setOnClickListener { sendKey("space") }

        lastVolumeProgress = dialogBinding.seekVolume.progress
        dialogBinding.seekVolume.setOnSeekBarChangeListener(object : android.widget.SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: android.widget.SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser) return
                val delta = progress - lastVolumeProgress
                lastVolumeProgress = progress
                repeat(abs(delta)) { sendKey(if (delta > 0) "volume_up" else "volume_down") }
            }
            override fun onStartTrackingTouch(seekBar: android.widget.SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: android.widget.SeekBar?) {}
        })

        dialogBinding.textServiceStatus.text = serviceStatusText
        dialogBinding.buttonAcceptUac.setOnClickListener {
            sendCommand(JSONObject().put("type", "uac_accept"))
        }

        AlertDialog.Builder(this)
            .setTitle("Tastiera")
            .setView(dialogBinding.root)
            .setNegativeButton("Chiudi", null)
            .show()
    }

    // --- touchpad e rotella ---

    private fun handleTouchpad(event: MotionEvent): Boolean {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                binding.touchpad.parent.requestDisallowInterceptTouchEvent(true)
                touchStartX = event.x
                touchStartY = event.y
                lastX = event.x
                lastY = event.y
                moved = false
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = event.x - lastX
                val dy = event.y - lastY
                if (abs(event.x - touchStartX) > CLICK_MOVE_THRESHOLD ||
                    abs(event.y - touchStartY) > CLICK_MOVE_THRESHOLD
                ) {
                    moved = true
                }
                if (dx != 0f || dy != 0f) {
                    sendCommand(
                        JSONObject()
                            .put("type", "move")
                            .put("dx", (dx * MOUSE_SENSITIVITY).toInt())
                            .put("dy", (dy * MOUSE_SENSITIVITY).toInt())
                    )
                }
                lastX = event.x
                lastY = event.y
            }
            MotionEvent.ACTION_UP -> {
                binding.touchpad.parent.requestDisallowInterceptTouchEvent(false)
                if (!moved) sendClick("left")
            }
        }
        return true
    }

    private fun handleWheel(event: MotionEvent): Boolean {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                binding.scrollWheel.parent.requestDisallowInterceptTouchEvent(true)
                wheelStartY = event.y
                wheelLastY = event.y
                wheelMoved = false
            }
            MotionEvent.ACTION_MOVE -> {
                val dy = event.y - wheelLastY
                if (abs(event.y - wheelStartY) > WHEEL_MOVE_THRESHOLD) wheelMoved = true
                if (abs(dy) >= WHEEL_STEP_PX) {
                    val notches = (dy / WHEEL_STEP_PX).toInt()
                    if (notches != 0) {
                        sendCommand(JSONObject().put("type", "scroll").put("amount", -notches))
                        wheelLastY = event.y
                    }
                }
            }
            MotionEvent.ACTION_UP -> {
                binding.scrollWheel.parent.requestDisallowInterceptTouchEvent(false)
                if (!wheelMoved) sendClick("middle")
            }
        }
        return true
    }

    private fun sendClick(button: String) {
        sendCommand(JSONObject().put("type", "click").put("button", button))
    }

    private fun sendKey(value: String) {
        sendCommand(JSONObject().put("type", "key").put("value", value))
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
            "mouse_lock_state" -> {
                mouseLocked = json.optBoolean("locked")
                binding.textMouseLockStatus.text =
                    if (mouseLocked) "mouse: bloccato sullo schermo principale"
                    else "mouse: libero tra gli schermi"
            }
            "service_status_result" -> {
                serviceInstalled = json.optBoolean("installed")
                serviceStatusText =
                    if (serviceInstalled) "servizio UAC: ATTIVO" else "servizio UAC: non installato"
            }
            "power_ok" -> log(json.optString("message"))
            "power_error" -> log("Errore: ${json.optString("message")}")
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
