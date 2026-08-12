package com.hubpc.client

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.IBinder

/** Servizio in primo piano vuoto: da Android 14 in poi la cattura schermo
 * (MediaProjection) richiede che un servizio di questo tipo sia gia' attivo,
 * altrimenti la cattura fallisce silenziosamente. Non fa altro: la logica
 * di cattura/invio vive in ProjectorActivity, che vive e muore con lo schermo
 * aperto, come le altre sezioni dell'app. */
class ProjectionService : Service() {

    companion object {
        private const val CHANNEL_ID = "projector_service"
        private const val NOTIFICATION_ID = 42
    }

    inner class LocalBinder : Binder() {
        // Nessun metodo: serve solo a far scattare onServiceConnected() lato
        // Activity DOPO che onCreate() (e quindi startForeground()) e' gia'
        // girato, cosi' MediaProjection non parte prima che il servizio sia
        // davvero promosso a foreground (altrimenti Android lo rifiuta).
    }

    private val binder = LocalBinder()

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Specchio schermo", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Specchio schermo attivo")
            .setContentText("Il telefono sta trasmettendo lo schermo al PC")
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    override fun onBind(intent: android.content.Intent?): IBinder = binder
}
