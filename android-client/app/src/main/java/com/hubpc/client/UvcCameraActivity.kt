package com.hubpc.client

import android.content.res.ColorStateList
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.graphics.RectF
import android.graphics.Typeface
import android.os.Bundle
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.SeekBar
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityUvcCameraBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.math.min

/** Mostra sul telefono una webcam USB gia' collegata al PC (Camera UVC: il
 * contrario della Virtual Camera). Permette di scegliere il dispositivo (se
 * il PC ne ha piu' di uno) e regolare luminosita'/contrasto/zoom, se
 * supportati dal driver della webcam. Nessun controllo del mouse: qui si
 * guarda soltanto, con pinch-to-zoom per i dettagli. */
class UvcCameraActivity : AppCompatActivity() {

    private lateinit var binding: ActivityUvcCameraBinding
    private lateinit var scaleDetector: ScaleGestureDetector
    private var webSocket: WebSocket? = null

    private var frameWidth = 0
    private var frameHeight = 0
    private var lastFrameW = 0
    private var lastFrameH = 0
    private val matrix = Matrix()
    private var zoom = 1f
    private var lastFocusX = 0f
    private var lastFocusY = 0f

    private var devices: List<Int> = emptyList()
    private var selectedDevice = 0
    private var running = false

    private var ip = ""
    private var token = ""

    private val decodeOptions = BitmapFactory.Options().apply {
        inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888
    }

    companion object {
        private const val UVCCAM_PORT = 8770
        private const val MIN_ZOOM = 1f
        private const val MAX_ZOOM = 4f
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityUvcCameraBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ip = intent.getStringExtra("ip").orEmpty()
        token = intent.getStringExtra("token").orEmpty()

        scaleDetector = ScaleGestureDetector(this, ScaleListener())
        binding.imageUvc.setOnTouchListener { _, event -> scaleDetector.onTouchEvent(event); true }

        binding.helpUvcCamera.setOnClickListener {
            HelpDialogs.show(
                this, "Camera UVC",
                "Guarda sul telefono una webcam USB gia' collegata al PC (non la fotocamera del telefono, " +
                    "quella e' \"Virtual camera\"). Se il PC ha piu' di una webcam, scegli quale usare.\n\n" +
                    "Luminosita'/contrasto funzionano solo se il driver della webcam li supporta: su alcune " +
                    "non hanno effetto.\n\n" +
                    "Pizzica per zoomare e vedere meglio i dettagli."
            )
        }

        binding.buttonUvcToggle.setOnClickListener {
            if (running) stopStream() else startStream()
        }

        binding.seekUvcBrightness.setOnSeekBarChangeListener(controlListener("brightness"))
        binding.seekUvcContrast.setOnSeekBarChangeListener(controlListener("contrast"))

        if (ip.isNotEmpty() && token.isNotEmpty()) {
            connect()
        } else {
            binding.textUvcStatus.text = "[ IP o token mancante ]"
        }
    }

    private fun controlListener(name: String) = object : SeekBar.OnSeekBarChangeListener {
        override fun onProgressChanged(seekBar: SeekBar, progress: Int, fromUser: Boolean) {
            if (fromUser && running) {
                webSocket?.send(JSONObject().put("type", "set_control").put("name", name).put("value", progress).toString())
            }
        }
        override fun onStartTrackingTouch(seekBar: SeekBar) {}
        override fun onStopTrackingTouch(seekBar: SeekBar) {}
    }

    private fun connect() {
        binding.textUvcStatus.text = "[ connessione... ]"
        val client = OkHttpClient.Builder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()
        val request = Request.Builder().url("ws://$ip:$UVCCAM_PORT?token=$token").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "list_devices").toString())
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                val data = bytes.toByteArray()
                val bitmap = BitmapFactory.decodeByteArray(data, 0, data.size, decodeOptions) ?: return
                runOnUiThread {
                    frameWidth = bitmap.width
                    frameHeight = bitmap.height
                    if (frameWidth != lastFrameW || frameHeight != lastFrameH) {
                        lastFrameW = frameWidth
                        lastFrameH = frameHeight
                        resetZoom()
                    }
                    binding.imageUvc.setImageBitmap(bitmap)
                    if (binding.textUvcStatus.visibility != View.GONE) {
                        binding.textUvcStatus.visibility = View.GONE
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val json = try { JSONObject(text) } catch (e: Exception) { return }
                if (json.optString("type") == "device_list") {
                    val array = json.optJSONArray("devices") ?: return
                    val list = mutableListOf<Int>()
                    for (i in 0 until array.length()) list.add(array.optInt(i))
                    runOnUiThread { renderDevices(list) }
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { binding.textUvcStatus.text = "[ errore: ${t.message} ]" }
            }
        })
    }

    private fun renderDevices(list: List<Int>) {
        devices = list
        binding.layoutUvcDevices.removeAllViews()
        if (list.isEmpty()) {
            binding.textUvcStatus.text = "[ nessuna webcam trovata sul pc ]"
            return
        }
        binding.textUvcStatus.text = "[ ferma ]"
        for (device in list) {
            val button = Button(this).apply {
                text = "CAMERA $device"
                textSize = 11f
                typeface = Typeface.MONOSPACE
                isAllCaps = false
                backgroundTintList = ColorStateList.valueOf(
                    getColor(if (device == selectedDevice) R.color.cyan else R.color.surface)
                )
                setOnClickListener {
                    selectedDevice = device
                    renderDevices(devices)
                    if (running) startStream()
                }
                val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                params.marginEnd = 8
                layoutParams = params
            }
            binding.layoutUvcDevices.addView(button)
        }
    }

    private fun startStream() {
        webSocket?.send(JSONObject().put("type", "start").put("device", selectedDevice).toString())
        running = true
        binding.buttonUvcToggle.text = "FERMA"
        binding.textUvcStatus.text = "[ in streaming... ]"
    }

    private fun stopStream() {
        webSocket?.send(JSONObject().put("type", "stop").toString())
        running = false
        binding.buttonUvcToggle.text = "AVVIA"
        binding.textUvcStatus.text = "[ ferma ]"
        binding.textUvcStatus.visibility = View.VISIBLE
        binding.imageUvc.setImageDrawable(null)
        lastFrameW = 0
        lastFrameH = 0
    }

    private fun resetZoom() {
        binding.imageUvc.post {
            val viewWidth = binding.imageUvc.width.toFloat()
            val viewHeight = binding.imageUvc.height.toFloat()
            if (viewWidth <= 0f || viewHeight <= 0f || frameWidth == 0 || frameHeight == 0) return@post
            val fit = min(viewWidth / frameWidth, viewHeight / frameHeight)
            matrix.reset()
            matrix.postScale(fit, fit)
            matrix.postTranslate(
                (viewWidth - frameWidth * fit) / 2f,
                (viewHeight - frameHeight * fit) / 2f
            )
            zoom = 1f
            binding.imageUvc.imageMatrix = matrix
        }
    }

    private fun clampMatrix() {
        val viewWidth = binding.imageUvc.width.toFloat()
        val viewHeight = binding.imageUvc.height.toFloat()
        if (viewWidth <= 0f || viewHeight <= 0f || frameWidth == 0 || frameHeight == 0) return

        val rect = RectF(0f, 0f, frameWidth.toFloat(), frameHeight.toFloat())
        matrix.mapRect(rect)

        val dx = if (rect.width() <= viewWidth) {
            (viewWidth - rect.width()) / 2f - rect.left
        } else if (rect.left > 0f) {
            -rect.left
        } else if (rect.right < viewWidth) {
            viewWidth - rect.right
        } else {
            0f
        }
        val dy = if (rect.height() <= viewHeight) {
            (viewHeight - rect.height()) / 2f - rect.top
        } else if (rect.top > 0f) {
            -rect.top
        } else if (rect.bottom < viewHeight) {
            viewHeight - rect.bottom
        } else {
            0f
        }
        matrix.postTranslate(dx, dy)
    }

    private inner class ScaleListener : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScaleBegin(detector: ScaleGestureDetector): Boolean {
            lastFocusX = detector.focusX
            lastFocusY = detector.focusY
            return true
        }

        override fun onScale(detector: ScaleGestureDetector): Boolean {
            val newZoom = (zoom * detector.scaleFactor).coerceIn(MIN_ZOOM, MAX_ZOOM)
            val factor = newZoom / zoom
            if (factor != 1f) {
                matrix.postScale(factor, factor, detector.focusX, detector.focusY)
                zoom = newZoom
            }
            matrix.postTranslate(detector.focusX - lastFocusX, detector.focusY - lastFocusY)
            lastFocusX = detector.focusX
            lastFocusY = detector.focusY
            clampMatrix()
            binding.imageUvc.imageMatrix = matrix
            return true
        }
    }

    override fun onDestroy() {
        if (running) webSocket?.send(JSONObject().put("type", "stop").toString())
        webSocket?.close(1000, "Camera UVC chiusa")
        webSocket = null
        super.onDestroy()
    }
}
