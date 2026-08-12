package com.hubpc.client

import android.content.Context
import android.os.Build
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.firestore.FieldValue
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.SetOptions
import com.google.firebase.messaging.FirebaseMessaging
import java.util.UUID

/** Registra questo telefono nel registro "Trova dispositivo" su Firestore,
 * cosi' e' visibile e raggiungibile (localizzazione, allarme) da qualunque
 * altro dispositivo sullo stesso account Firebase — anche se questo
 * telefono non apre mai la relativa schermata (es. il telefono di un
 * familiare, localizzabile dall'account principale). */
object DeviceRegistryService {

    private const val PREFS_NAME = HubApplication.PREFS_NAME
    private const val PREF_DEVICE_ID = "find_device_id"
    private const val PREF_DEVICE_NAME = "find_device_name"

    fun deviceId(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val existing = prefs.getString(PREF_DEVICE_ID, null)
        if (existing != null) return existing
        val id = UUID.randomUUID().toString()
        prefs.edit().putString(PREF_DEVICE_ID, id).apply()
        return id
    }

    fun deviceName(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.getString(PREF_DEVICE_NAME, null) ?: "${Build.MANUFACTURER} ${Build.MODEL}".trim()
    }

    fun setDeviceName(context: Context, name: String) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit().putString(PREF_DEVICE_NAME, name).apply()
        registerThisDevice(context)
    }

    /** Da chiamare a ogni avvio app con utente autenticato: crea o aggiorna
     * il documento del dispositivo (nome, tipo, ultimo accesso, token FCM
     * corrente per ricevere i comandi anche ad app chiusa). */
    fun registerThisDevice(context: Context) {
        val uid = FirebaseAuth.getInstance().currentUser?.uid ?: return
        val doc = deviceDoc(context, uid)
        doc.set(
            mapOf(
                "name" to deviceName(context),
                "type" to "phone",
                "lastSeen" to FieldValue.serverTimestamp(),
            ),
            SetOptions.merge()
        )
        FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
            doc.set(mapOf("fcmToken" to token), SetOptions.merge())
        }
    }

    fun updateToken(context: Context, token: String) {
        val uid = FirebaseAuth.getInstance().currentUser?.uid ?: return
        deviceDoc(context, uid).set(mapOf("fcmToken" to token), SetOptions.merge())
    }

    private fun deviceDoc(context: Context, uid: String) =
        FirebaseFirestore.getInstance()
            .collection("users").document(uid)
            .collection("devices").document(deviceId(context))
}
