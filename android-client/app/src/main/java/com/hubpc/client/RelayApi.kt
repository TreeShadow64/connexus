package com.hubpc.client

import com.google.firebase.auth.FirebaseAuth
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException

/** Ponte verso il Worker Cloudflare (cloudflare-relay/): l'unico posto che
 * tiene le credenziali per mandare push per conto di un utente o firmare un
 * token per il PC. Ogni chiamata porta il proprio idToken Firebase (gia'
 * loggato in app), mai una password ne' una chiave admin. */
object RelayApi {
    private const val BASE_URL = "https://connexus-relay.homeconnexus.workers.dev"
    private val client = OkHttpClient()
    private val JSON = "application/json; charset=utf-8".toMediaType()

    /** Chiede al relay di inoltrare un comando via push a un altro
     * dispositivo dello stesso account: i client Firebase non possono
     * mandarsi notifiche a vicenda da soli. Fire-and-forget — se fallisce
     * (relay irraggiungibile), il comando resta comunque in coda su
     * Firestore e verra' visto al prossimo avvio dell'app di destinazione. */
    fun sendPush(deviceId: String, cmd: String) {
        val user = FirebaseAuth.getInstance().currentUser ?: return
        user.getIdToken(false).addOnSuccessListener { result ->
            val idToken = result.token ?: return@addOnSuccessListener
            val body = JSONObject()
                .put("idToken", idToken)
                .put("deviceId", deviceId)
                .put("cmd", cmd)
                .toString().toRequestBody(JSON)
            val request = Request.Builder().url("$BASE_URL/send-push").post(body).build()
            client.newCall(request).enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {}
                override fun onResponse(call: Call, response: Response) = response.close()
            })
        }
    }

    /** Scambia l'idToken del telefono con un custom token che il PC puo'
     * usare per autenticarsi su Firestore con gli stessi diritti di un
     * client qualunque (nessun accesso admin locale sul PC). */
    fun mintPcToken(onResult: (String?) -> Unit) {
        val user = FirebaseAuth.getInstance().currentUser
        if (user == null) {
            onResult(null)
            return
        }
        user.getIdToken(false)
            .addOnSuccessListener { result ->
                val idToken = result.token
                if (idToken == null) {
                    onResult(null)
                    return@addOnSuccessListener
                }
                val body = JSONObject().put("idToken", idToken).toString().toRequestBody(JSON)
                val request = Request.Builder().url("$BASE_URL/mint-pc-token").post(body).build()
                client.newCall(request).enqueue(object : Callback {
                    override fun onFailure(call: Call, e: IOException) = onResult(null)
                    override fun onResponse(call: Call, response: Response) {
                        val text = response.body?.string()
                        response.close()
                        val token = try {
                            JSONObject(text ?: "").optString("customToken").ifEmpty { null }
                        } catch (e: Exception) {
                            null
                        }
                        onResult(token)
                    }
                })
            }
            .addOnFailureListener { onResult(null) }
    }
}
