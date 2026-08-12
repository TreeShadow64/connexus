package com.hubpc.client

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import com.google.android.gms.location.CurrentLocationRequest
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.firestore.FieldValue
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.SetOptions

/** Esegue in background i comandi "Trova dispositivo" arrivati via push (o
 * avviati dall'attivita' mentre l'app e' aperta): localizza il telefono una
 * volta, oppure suona un allarme al volume massimo finche' non arriva lo
 * stop o passano 2 minuti (timeout di sicurezza, cosi' non resta a suonare
 * per sempre se il comando di stop si perde). */
class FindDeviceService : Service() {

    private var mediaPlayer: MediaPlayer? = null
    private val handler = Handler(Looper.getMainLooper())
    private var alarmTimeoutRunnable: Runnable? = null

    companion object {
        const val ACTION_LOCATE = "com.hubpc.client.action.LOCATE"
        const val ACTION_ALARM_START = "com.hubpc.client.action.ALARM_START"
        const val ACTION_ALARM_STOP = "com.hubpc.client.action.ALARM_STOP"
        const val PREF_ALARM_SOUND_URI = "alarm_sound_uri"
        private const val CHANNEL_ID = "find_device"
        private const val NOTIF_ID = 501
        private const val ALARM_TIMEOUT_MS = 120_000L
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ensureChannel()
        when (intent?.action) {
            ACTION_LOCATE -> {
                startForegroundWithType("Localizzazione in corso...", ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
                locateAndStop()
            }
            ACTION_ALARM_START -> {
                startForegroundWithType("Allarme in corso", ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
                startAlarm()
            }
            ACTION_ALARM_STOP -> {
                stopAlarm(updateFirestore = true)
                stopSelf()
            }
            else -> stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Trova dispositivo", NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    private fun startForegroundWithType(text: String, type: Int) {
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Trova dispositivo")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notification, type)
        } else {
            startForeground(NOTIF_ID, notification)
        }
    }

    private fun ownDeviceDoc() = FirebaseAuth.getInstance().currentUser?.uid?.let { uid ->
        FirebaseFirestore.getInstance()
            .collection("users").document(uid)
            .collection("devices").document(DeviceRegistryService.deviceId(this))
    }

    private fun locateAndStop() {
        if (ActivityCompat.checkSelfPermission(this, android.Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            stopSelf()
            return
        }
        val client = LocationServices.getFusedLocationProviderClient(this)
        val request = CurrentLocationRequest.Builder()
            .setPriority(Priority.PRIORITY_BALANCED_POWER_ACCURACY)
            .build()
        client.getCurrentLocation(request, CancellationTokenSource().token)
            .addOnSuccessListener { location ->
                if (location != null) {
                    ownDeviceDoc()?.set(
                        mapOf(
                            "lastLocation" to mapOf(
                                "lat" to location.latitude,
                                "lng" to location.longitude,
                                "accuracy" to location.accuracy,
                                "timestamp" to FieldValue.serverTimestamp(),
                            ),
                            "lastSeen" to FieldValue.serverTimestamp(),
                        ),
                        SetOptions.merge()
                    )
                }
                stopSelf()
            }
            .addOnFailureListener { stopSelf() }
    }

    private fun startAlarm() {
        if (mediaPlayer != null) return
        val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        audioManager.setStreamVolume(
            AudioManager.STREAM_ALARM,
            audioManager.getStreamMaxVolume(AudioManager.STREAM_ALARM),
            0
        )
        val savedUri = getSharedPreferences(HubApplication.PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_ALARM_SOUND_URI, null)
        val uri = savedUri?.let { Uri.parse(it) }
            ?: RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
        mediaPlayer = MediaPlayer().apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
            )
            setDataSource(this@FindDeviceService, uri)
            isLooping = true
            prepare()
            start()
        }
        ownDeviceDoc()?.set(mapOf("alarmActive" to true), SetOptions.merge())
        val runnable = Runnable {
            stopAlarm(updateFirestore = true)
            stopSelf()
        }
        alarmTimeoutRunnable = runnable
        handler.postDelayed(runnable, ALARM_TIMEOUT_MS)
    }

    private fun stopAlarm(updateFirestore: Boolean) {
        mediaPlayer?.let {
            it.stop()
            it.release()
        }
        mediaPlayer = null
        alarmTimeoutRunnable?.let { handler.removeCallbacks(it) }
        alarmTimeoutRunnable = null
        if (updateFirestore) {
            ownDeviceDoc()?.set(mapOf("alarmActive" to false), SetOptions.merge())
        }
    }

    override fun onDestroy() {
        stopAlarm(updateFirestore = false)
        super.onDestroy()
    }
}
