package com.hubpc.client

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityProjectorBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

/** Manda lo schermo del TELEFONO al PC (l'opposto di "Schermo PC"): cattura
 * via MediaProjection, incapsula ogni fotogramma in JPEG e lo spedisce sul
 * canale WebSocket dedicato. Il PC lo mostra in una finestra nativa. */
class ProjectorActivity : AppCompatActivity() {

    private lateinit var binding: ActivityProjectorBinding
    private lateinit var projectionManager: MediaProjectionManager

    private var webSocket: WebSocket? = null
    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var encoderThread: HandlerThread? = null
    private var running = false
    private var lastFrameSent = 0L
    private var serviceBound = false

    private var ip = ""
    private var token = ""
    private var pendingResultCode = 0
    private var pendingData: Intent? = null

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: android.os.IBinder?) {
            // A questo punto ProjectionService.onCreate() (e quindi
            // startForeground()) e' gia' concluso: sicuro chiedere la proiezione.
            serviceBound = true
            val data = pendingData
            if (data != null) beginProjection(pendingResultCode, data)
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            serviceBound = false
        }
    }

    companion object {
        private const val PROJECTOR_PORT = 8768
        private const val TARGET_WIDTH = 720
        private const val JPEG_QUALITY = 60
        private const val FRAME_INTERVAL_MS = 120L // ~8 fps: basta per uno specchio, non serve di piu'
    }

    private val requestCapture = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            startCapture(result.resultCode, result.data!!)
        } else {
            binding.textProjectorStatus.text = "Permesso negato"
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityProjectorBinding.inflate(layoutInflater)
        setContentView(binding.root)

        projectionManager = getSystemService(MediaProjectionManager::class.java)

        binding.helpProjector.setOnClickListener {
            HelpDialogs.show(
                this, "Projector",
                "Manda lo schermo di questo telefono al PC, che lo mostra in una finestra separata — utile per " +
                    "presentazioni o per mostrare qualcosa dal telefono senza cavi. Android chiedera' un permesso " +
                    "di sistema per la cattura schermo la prima volta.\n\n" +
                    "FERMA interrompe la trasmissione: il telefono torna a funzionare normalmente."
            )
        }

        ip = intent.getStringExtra("ip").orEmpty()
        token = intent.getStringExtra("token").orEmpty()

        binding.buttonProjectorToggle.setOnClickListener {
            if (running) stopCapture() else requestCapture.launch(projectionManager.createScreenCaptureIntent())
        }
    }

    private fun startCapture(resultCode: Int, data: Intent) {
        pendingResultCode = resultCode
        pendingData = data
        val serviceIntent = Intent(this, ProjectionService::class.java)
        startForegroundService(serviceIntent)
        bindService(serviceIntent, serviceConnection, Context.BIND_AUTO_CREATE)
    }

    private fun beginProjection(resultCode: Int, data: Intent) {
        val projection = projectionManager.getMediaProjection(resultCode, data)
        mediaProjection = projection

        val metrics = resources.displayMetrics
        val scale = TARGET_WIDTH.toFloat() / metrics.widthPixels
        val width = TARGET_WIDTH
        val height = (metrics.heightPixels * scale).toInt()

        val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        imageReader = reader

        val thread = HandlerThread("projector-encoder").apply { start() }
        encoderThread = thread
        val handler = Handler(thread.looper)

        // Obbligatorio dalle versioni recenti di Android: senza una callback
        // registrata, createVirtualDisplay() rifiuta di partire.
        projection.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                runOnUiThread { if (running) stopCapture() }
            }
        }, handler)

        virtualDisplay = projection.createVirtualDisplay(
            "HubProjector", width, height, metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader.surface, null, handler
        )

        connectWebSocket()

        reader.setOnImageAvailableListener({ imageReader ->
            val image = imageReader.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                val now = System.currentTimeMillis()
                if (now - lastFrameSent < FRAME_INTERVAL_MS) return@setOnImageAvailableListener
                lastFrameSent = now
                sendFrame(image, width, height)
            } finally {
                image.close()
            }
        }, handler)

        running = true
        binding.buttonProjectorToggle.text = "FERMA"
        binding.textProjectorStatus.text = "In trasmissione al PC..."
    }

    private fun sendFrame(image: android.media.Image, width: Int, height: Int) {
        val plane = image.planes[0]
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val bitmapWidth = rowStride / pixelStride

        val bitmap = Bitmap.createBitmap(bitmapWidth, height, Bitmap.Config.ARGB_8888)
        bitmap.copyPixelsFromBuffer(plane.buffer)
        val cropped = if (bitmapWidth != width) Bitmap.createBitmap(bitmap, 0, 0, width, height) else bitmap

        val out = ByteArrayOutputStream()
        cropped.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
        webSocket?.send(ByteString.of(*out.toByteArray()))

        if (cropped !== bitmap) bitmap.recycle()
        cropped.recycle()
    }

    private fun connectWebSocket() {
        val client = OkHttpClient.Builder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()
        val request = Request.Builder().url("ws://$ip:$PROJECTOR_PORT?token=$token").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread {
                    binding.textProjectorStatus.text = "Errore: ${t.message}"
                }
            }
        })
    }

    private fun stopCapture() {
        running = false
        imageReader?.setOnImageAvailableListener(null, null)
        virtualDisplay?.release()
        mediaProjection?.stop()
        imageReader?.close()
        encoderThread?.quitSafely()
        webSocket?.close(1000, "Specchio fermato")
        webSocket = null
        virtualDisplay = null
        mediaProjection = null
        imageReader = null
        encoderThread = null
        if (serviceBound) {
            unbindService(serviceConnection)
            serviceBound = false
        }
        stopService(Intent(this, ProjectionService::class.java))

        binding.buttonProjectorToggle.text = "AVVIA SPECCHIO SUL PC"
        binding.textProjectorStatus.text = "Ferma"
    }

    override fun onDestroy() {
        if (running) stopCapture()
        super.onDestroy()
    }
}
