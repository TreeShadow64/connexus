package com.hubpc.client

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.Rect
import android.graphics.YuvImage
import android.os.Bundle
import android.util.Log
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import com.hubpc.client.databinding.ActivityVirtualCameraBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

/** Usa la fotocamera del TELEFONO come webcam del PC: cattura fotogrammi via
 * CameraX, li incapsula in JPEG e li spedisce sul canale WebSocket dedicato.
 * Il PC li inoltra a una webcam virtuale di sistema (driver OBS Virtual
 * Camera), selezionabile in Zoom/Teams/Discord/browser come una webcam vera. */
class VirtualCameraActivity : AppCompatActivity() {

    private lateinit var binding: ActivityVirtualCameraBinding

    private var webSocket: WebSocket? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var running = false
    private var lastFrameSent = 0L
    private var usingFrontCamera = true

    private var ip = ""
    private var token = ""

    companion object {
        private const val VIRTUALCAM_PORT = 8769
        private const val JPEG_QUALITY = 65
        private const val FRAME_INTERVAL_MS = 66L // ~15 fps
    }

    private val requestCameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            bindCameraUseCases()
            startSharing()
        } else {
            binding.textVirtualCameraStatus.text = "Permesso fotocamera negato"
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityVirtualCameraBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ip = intent.getStringExtra("ip").orEmpty()
        token = intent.getStringExtra("token").orEmpty()

        binding.helpVirtualCamera.setOnClickListener {
            HelpDialogs.show(
                this, "Virtual camera",
                "Usa la fotocamera del telefono come webcam del PC: una volta avviata, in Zoom/Teams/Discord o " +
                    "in qualsiasi programma puoi scegliere \"OBS Virtual Camera\" come sorgente video e vedrai " +
                    "l'inquadratura del telefono.\n\n" +
                    "Serve OBS Studio installato sul PC (basta averlo installato una volta, non serve tenerlo aperto).\n\n" +
                    "L'icona in basso a sinistra cambia tra fotocamera anteriore e posteriore."
            )
        }

        startPreview()
        bindCameraUseCases()

        binding.buttonSwitchCamera.setOnClickListener {
            usingFrontCamera = !usingFrontCamera
            bindCameraUseCases()
        }

        binding.buttonVirtualCameraToggle.setOnClickListener {
            if (running) stopSharing() else requestCameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    private fun startPreview() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            cameraProvider = providerFuture.get()
            bindCameraUseCases()
        }, ContextCompat.getMainExecutor(this))
    }

    private var imageAnalysis: ImageAnalysis? = null

    private fun bindCameraUseCases() {
        val provider = cameraProvider ?: return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) return

        val preview = Preview.Builder().build().also {
            it.setSurfaceProvider(binding.cameraPreview.surfaceProvider)
        }

        val analysis = ImageAnalysis.Builder()
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .build()
        analysis.setAnalyzer(ContextCompat.getMainExecutor(this)) { image -> analyzeFrame(image) }
        imageAnalysis = analysis

        val selector = if (usingFrontCamera) CameraSelector.DEFAULT_FRONT_CAMERA else CameraSelector.DEFAULT_BACK_CAMERA

        try {
            provider.unbindAll()
            provider.bindToLifecycle(this, selector, preview, analysis)
        } catch (e: Exception) {
            Log.w("VirtualCamera", "Impossibile agganciare la fotocamera: ${e.message}")
            binding.textVirtualCameraStatus.text = "Fotocamera non disponibile"
        }
    }

    private fun analyzeFrame(image: ImageProxy) {
        try {
            if (!running) return
            val now = System.currentTimeMillis()
            if (now - lastFrameSent < FRAME_INTERVAL_MS) return
            lastFrameSent = now

            val nv21 = yuv420888ToNv21(image)
            val yuvImage = YuvImage(nv21, ImageFormat.NV21, image.width, image.height, null)
            val out = ByteArrayOutputStream()
            yuvImage.compressToJpeg(Rect(0, 0, image.width, image.height), JPEG_QUALITY, out)
            val rawBytes = out.toByteArray()

            val rotation = image.imageInfo.rotationDegrees
            val finalBytes = if (rotation == 0) {
                rawBytes
            } else {
                val bitmap = BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.size)
                val matrix = Matrix().apply { postRotate(rotation.toFloat()) }
                val rotated = Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
                val rotatedOut = ByteArrayOutputStream()
                rotated.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, rotatedOut)
                bitmap.recycle()
                rotated.recycle()
                rotatedOut.toByteArray()
            }

            webSocket?.send(ByteString.of(*finalBytes))
        } finally {
            image.close()
        }
    }

    private fun yuv420888ToNv21(image: ImageProxy): ByteArray {
        val yBuffer = image.planes[0].buffer
        val uBuffer = image.planes[1].buffer
        val vBuffer = image.planes[2].buffer
        val ySize = yBuffer.remaining()
        val uSize = uBuffer.remaining()
        val vSize = vBuffer.remaining()
        val nv21 = ByteArray(ySize + uSize + vSize)
        yBuffer.get(nv21, 0, ySize)
        vBuffer.get(nv21, ySize, vSize)
        uBuffer.get(nv21, ySize + vSize, uSize)
        return nv21
    }

    private fun startSharing() {
        connectWebSocket()
        running = true
        binding.buttonVirtualCameraToggle.text = "FERMA"
        binding.textVirtualCameraStatus.text = "in trasmissione come webcam del pc..."
    }

    private fun connectWebSocket() {
        val client = OkHttpClient.Builder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()
        val request = Request.Builder().url("ws://$ip:$VIRTUALCAM_PORT?token=$token").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread {
                    binding.textVirtualCameraStatus.text = "errore: ${t.message}"
                }
            }
        })
    }

    private fun stopSharing() {
        running = false
        webSocket?.close(1000, "Virtual camera fermata")
        webSocket = null
        binding.buttonVirtualCameraToggle.text = "AVVIA COME WEBCAM PC"
        binding.textVirtualCameraStatus.text = "ferma"
    }

    override fun onDestroy() {
        if (running) stopSharing()
        imageAnalysis?.clearAnalyzer()
        cameraProvider?.unbindAll()
        super.onDestroy()
    }
}
