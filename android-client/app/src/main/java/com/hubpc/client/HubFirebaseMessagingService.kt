package com.hubpc.client

import android.content.Intent
import androidx.core.content.ContextCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/** Riceve i comandi "Trova dispositivo" mandati dal PC (relay) via push,
 * anche ad app chiusa: li gira a FindDeviceService, che fa il lavoro vero
 * (localizzare o suonare l'allarme) da un servizio in primo piano. */
class HubFirebaseMessagingService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        DeviceRegistryService.updateToken(this, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)
        val action = when (message.data["cmd"]) {
            "locate" -> FindDeviceService.ACTION_LOCATE
            "alarm_start" -> FindDeviceService.ACTION_ALARM_START
            "alarm_stop" -> FindDeviceService.ACTION_ALARM_STOP
            else -> return
        }
        ContextCompat.startForegroundService(this, Intent(this, FindDeviceService::class.java).setAction(action))
    }
}
