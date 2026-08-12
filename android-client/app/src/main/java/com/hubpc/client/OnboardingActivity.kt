package com.hubpc.client

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityOnboardingBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject

/** Primo avvio: chiede IP e token del PC una sola volta, verifica che la
 * connessione funzioni davvero prima di entrare nella app. */
class OnboardingActivity : AppCompatActivity() {

    private lateinit var binding: ActivityOnboardingBinding
    private var webSocket: WebSocket? = null

    companion object {
        private const val PREFS_NAME = HubApplication.PREFS_NAME
        private const val PREF_ONBOARDED = "onboarded"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityOnboardingBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.buttonStart.setOnClickListener { testAndSave() }
        binding.helpConnection.setOnClickListener { HelpDialogs.showConnectionHelp(this) }
    }

    private fun testAndSave() {
        val ip = binding.editIp.text.toString().trim()
        val token = binding.editToken.text.toString().trim()
        if (ip.isEmpty() || token.isEmpty()) {
            binding.textOnboardingStatus.text = "Inserisci sia l'IP che il token"
            return
        }

        binding.buttonStart.isEnabled = false
        binding.textOnboardingStatus.text = "Connessione in corso..."

        val client = OkHttpClient()
        val request = Request.Builder().url("ws://$ip:8765").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "auth").put("token", token).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runOnUiThread { handleMessage(text, ip, token) }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {}

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread {
                    binding.buttonStart.isEnabled = true
                    binding.textOnboardingStatus.text = "Impossibile raggiungere il PC: ${t.message}"
                }
            }
        })
    }

    private fun handleMessage(text: String, ip: String, token: String) {
        val json = try { JSONObject(text) } catch (e: Exception) { return }
        when (json.optString("type")) {
            "auth_ok" -> {
                ConnectionProfiles.save(this, mutableListOf(ConnectionProfile("PC", ip, token)))
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                    .putBoolean(PREF_ONBOARDED, true)
                    .apply()
                webSocket?.close(1000, "Onboarding completato")
                startActivity(Intent(this, MainActivity::class.java))
                finish()
            }
            "auth_error" -> {
                binding.buttonStart.isEnabled = true
                binding.textOnboardingStatus.text = "Token non valido"
            }
        }
    }

    override fun onDestroy() {
        webSocket?.close(1000, "Chiuso")
        super.onDestroy()
    }
}
