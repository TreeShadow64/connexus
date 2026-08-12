package com.hubpc.client

import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.graphics.RectF
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.WindowManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityScreenBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import kotlin.math.min

/** Specchia lo schermo principale del PC e rimanda i tocchi come mouse assoluto.
 * Supporta pinch-to-zoom per guardare da vicino: il trascinamento con un dito
 * muove sempre il mouse (piu' preciso quando si e' zoomati), mentre il
 * trascinamento con due dita sposta la vista (pan) senza toccare il mouse. */
class ScreenActivity : AppCompatActivity() {

    private lateinit var binding: ActivityScreenBinding
    private lateinit var scaleDetector: ScaleGestureDetector
    private var webSocket: WebSocket? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    // Dimensioni del fotogramma ricevuto.
    private var frameWidth = 0
    private var frameHeight = 0
    private var lastFrameW = 0
    private var lastFrameH = 0

    // Matrice applicata all'ImageView (scaleType="matrix"): mappa le coordinate
    // del bitmap a coordinate della view. Parte come un "fitCenter" manuale,
    // poi pinch/pan la modificano sopra.
    private val matrix = Matrix()
    private var zoom = 1f
    private var lastFocusX = 0f
    private var lastFocusY = 0f

    private var downX = 0f
    private var downY = 0f
    private var downTime = 0L
    private var moved = false
    private var lastMoveSent = 0L

    private var lastTapTime = 0L
    private var lastTapX = 0f
    private var lastTapY = 0f
    private var pendingSingleTap: Runnable? = null

    private val decodeOptions = BitmapFactory.Options().apply {
        inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888
    }

    companion object {
        const val EXTRA_IP = "ip"
        const val EXTRA_TOKEN = "token"
        private const val SCREEN_PORT = 8767
        private const val TAP_MOVE_THRESHOLD = 16f
        private const val DOUBLE_TAP_MS = 260L
        private const val MOVE_THROTTLE_MS = 30L
        private const val MIN_ZOOM = 1f
        private const val MAX_ZOOM = 4f
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityScreenBinding.inflate(layoutInflater)
        setContentView(binding.root)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        scaleDetector = ScaleGestureDetector(this, ScaleListener())
        binding.imageScreen.setOnTouchListener { _, event -> handleTouch(event) }

        binding.buttonAcceptUac.setOnClickListener {
            webSocket?.send(JSONObject().put("type", "uac_accept").toString())
            Toast.makeText(this, "Comando UAC inviato", Toast.LENGTH_SHORT).show()
        }

        val ip = intent.getStringExtra(EXTRA_IP).orEmpty()
        val token = intent.getStringExtra(EXTRA_TOKEN).orEmpty()
        if (ip.isEmpty() || token.isEmpty()) {
            binding.textScreenStatus.text = "IP o token mancante"
            return
        }
        connect(ip, token)
    }

    private fun connect(ip: String, token: String) {
        binding.textScreenStatus.text = "Connessione allo schermo del PC..."
        val client = OkHttpClient.Builder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()
        val request = Request.Builder().url("ws://$ip:$SCREEN_PORT?token=$token").build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                val data = bytes.toByteArray()
                val bitmap = BitmapFactory.decodeByteArray(data, 0, data.size, decodeOptions)
                    ?: return
                runOnUiThread {
                    frameWidth = bitmap.width
                    frameHeight = bitmap.height
                    if (frameWidth != lastFrameW || frameHeight != lastFrameH) {
                        lastFrameW = frameWidth
                        lastFrameH = frameHeight
                        resetZoom()
                    }
                    binding.imageScreen.setImageBitmap(bitmap)
                    if (binding.textScreenStatus.visibility != android.view.View.GONE) {
                        binding.textScreenStatus.visibility = android.view.View.GONE
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val json = try {
                    JSONObject(text)
                } catch (e: Exception) {
                    return
                }
                when (json.optString("type")) {
                    "screen_error" -> runOnUiThread {
                        binding.textScreenStatus.text = "Errore: ${json.optString("message")}"
                    }
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { binding.textScreenStatus.text = "Errore: ${t.message}" }
            }
        })
    }

    /** Ricalcola la matrice base "fitCenter" per il fotogramma corrente e azzera lo zoom. */
    private fun resetZoom() {
        binding.imageScreen.post {
            val viewWidth = binding.imageScreen.width.toFloat()
            val viewHeight = binding.imageScreen.height.toFloat()
            if (viewWidth <= 0f || viewHeight <= 0f || frameWidth == 0 || frameHeight == 0) return@post
            val fit = min(viewWidth / frameWidth, viewHeight / frameHeight)
            matrix.reset()
            matrix.postScale(fit, fit)
            matrix.postTranslate(
                (viewWidth - frameWidth * fit) / 2f,
                (viewHeight - frameHeight * fit) / 2f
            )
            zoom = 1f
            binding.imageScreen.imageMatrix = matrix
        }
    }

    /** Impedisce che pan/zoom lascino la vista con bordi vuoti attorno all'immagine. */
    private fun clampMatrix() {
        val viewWidth = binding.imageScreen.width.toFloat()
        val viewHeight = binding.imageScreen.height.toFloat()
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

    private fun applyMatrix() {
        binding.imageScreen.imageMatrix = matrix
    }

    /** Pizzicare zooma, ma due dita che si muovono insieme (senza cambiare
     * distanza) spostano la vista: e' cosi' che si guarda in giro da zoomati
     * senza toccare il mouse, che resta sempre sotto il controllo di un
     * solo dito. */
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
            applyMatrix()
            return true
        }
    }

    /** Converte il tocco sulla view in coordinate normalizzate [0,1] del monitor
     * del PC, invertendo la matrice corrente (base + zoom + pan). */
    private fun normalize(touchX: Float, touchY: Float): Pair<Float, Float>? {
        if (frameWidth == 0 || frameHeight == 0) return null
        val inverse = Matrix()
        if (!matrix.invert(inverse)) return null
        val point = floatArrayOf(touchX, touchY)
        inverse.mapPoints(point)
        val normX = point[0] / frameWidth
        val normY = point[1] / frameHeight
        if (normX < 0f || normX > 1f || normY < 0f || normY > 1f) return null
        return normX to normY
    }

    private fun handleTouch(event: MotionEvent): Boolean {
        scaleDetector.onTouchEvent(event)

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.x
                downY = event.y
                downTime = System.currentTimeMillis()
                moved = false
                sendTouch("move", event.x, event.y)
            }

            MotionEvent.ACTION_POINTER_DOWN -> {
                // Un secondo dito indica un gesto di zoom/pan, non un tap:
                // da qui in poi il movimento e' gestito da scaleDetector.
                moved = true
            }

            MotionEvent.ACTION_MOVE -> {
                if (event.pointerCount == 1) {
                    if (abs(event.x - downX) > TAP_MOVE_THRESHOLD ||
                        abs(event.y - downY) > TAP_MOVE_THRESHOLD
                    ) {
                        moved = true
                    }
                    val now = System.currentTimeMillis()
                    if (now - lastMoveSent >= MOVE_THROTTLE_MS) {
                        lastMoveSent = now
                        sendTouch("move", event.x, event.y)
                    }
                }
            }

            MotionEvent.ACTION_UP -> {
                if (!moved && event.pointerCount == 1) {
                    val now = System.currentTimeMillis()
                    val isDoubleTap = now - lastTapTime <= DOUBLE_TAP_MS &&
                        abs(event.x - lastTapX) < TAP_MOVE_THRESHOLD &&
                        abs(event.y - lastTapY) < TAP_MOVE_THRESHOLD

                    if (isDoubleTap) {
                        pendingSingleTap?.let { mainHandler.removeCallbacks(it) }
                        pendingSingleTap = null
                        lastTapTime = 0L
                        sendTouch("double_tap", event.x, event.y)
                    } else {
                        lastTapTime = now
                        lastTapX = event.x
                        lastTapY = event.y
                        val tapX = event.x
                        val tapY = event.y
                        val runnable = Runnable { sendTouch("tap", tapX, tapY) }
                        pendingSingleTap = runnable
                        mainHandler.postDelayed(runnable, DOUBLE_TAP_MS)
                    }
                }
            }
        }
        return true
    }

    private fun sendTouch(action: String, touchX: Float, touchY: Float) {
        val (normX, normY) = normalize(touchX, touchY) ?: return
        webSocket?.send(
            JSONObject()
                .put("type", "touch")
                .put("action", action)
                .put("x", normX.toDouble())
                .put("y", normY.toDouble())
                .toString()
        )
    }

    override fun onDestroy() {
        pendingSingleTap?.let { mainHandler.removeCallbacks(it) }
        // Chiudere la connessione ferma la cattura del monitor sul PC.
        webSocket?.close(1000, "Schermo chiuso")
        webSocket = null
        super.onDestroy()
    }
}
