package com.hubpc.client

import android.os.Bundle
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityTaskManagerBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONArray
import org.json.JSONObject

/** Elenco dei processi del PC con uso di memoria, e possibilita' di
 * chiuderli da remoto. Canale WebSocket condiviso (porta 8765) come le
 * altre sezioni "leggere" dell'app. */
class TaskManagerActivity : AppCompatActivity() {

    private lateinit var binding: ActivityTaskManagerBinding
    private var webSocket: WebSocket? = null
    private var authenticated = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityTaskManagerBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.helpTaskManager.setOnClickListener {
            HelpDialogs.show(
                this, "Task manager",
                "Elenco dei processi del PC in questo momento, ordinati per memoria usata (i piu' pesanti prima). " +
                    "Tocca CHIUDI per terminare un processo — usalo con attenzione, chiudere un processo di sistema " +
                    "puo' rendere instabile il PC."
            )
        }
        binding.buttonRefreshProcesses.setOnClickListener { requestProcesses() }

        val ip = intent.getStringExtra("ip").orEmpty()
        val token = intent.getStringExtra("token").orEmpty()
        connect(ip, token)
    }

    private fun connect(ip: String, token: String) {
        val client = OkHttpClient()
        val request = Request.Builder().url("ws://$ip:8765").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "auth").put("token", token).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runOnUiThread { handleMessage(text) }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {}

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { binding.textTaskManagerStatus.text = "Errore: ${t.message}" }
            }
        })
    }

    private fun handleMessage(text: String) {
        val json = try { JSONObject(text) } catch (e: Exception) { return }
        when (json.optString("type")) {
            "auth_ok" -> {
                authenticated = true
                binding.textTaskManagerStatus.text = "connesso"
                requestProcesses()
            }
            "auth_error" -> binding.textTaskManagerStatus.text = "autenticazione fallita"
            "process_list" -> renderProcesses(json.optJSONArray("processes"))
            "kill_result" -> {
                binding.textTaskManagerStatus.text = json.optString("message")
                requestProcesses()
            }
        }
    }

    private fun requestProcesses() {
        if (!authenticated) return
        binding.textTaskManagerStatus.text = "aggiornamento..."
        webSocket?.send(JSONObject().put("type", "list_processes").toString())
    }

    private fun renderProcesses(processes: JSONArray?) {
        binding.layoutProcesses.removeAllViews()
        if (processes == null || processes.length() == 0) {
            binding.textTaskManagerStatus.text = "nessun processo trovato"
            return
        }
        binding.textTaskManagerStatus.text = "${processes.length()} processi"
        for (i in 0 until processes.length()) {
            val process = processes.optJSONObject(i) ?: continue
            binding.layoutProcesses.addView(makeRow(process))
        }
    }

    private fun makeRow(process: JSONObject): android.widget.LinearLayout {
        val pid = process.optInt("pid")
        val name = process.optString("name", "?")
        val memoryMb = process.optDouble("memory_mb", 0.0)

        val row = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
            setBackgroundResource(R.drawable.bg_module_card)
            setPadding(24, 20, 24, 20)
            val params = android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.MATCH_PARENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT
            )
            params.bottomMargin = 8
            layoutParams = params
        }

        val label = android.widget.TextView(this).apply {
            text = "$name\npid $pid — ${"%.1f".format(memoryMb)} MB"
            typeface = android.graphics.Typeface.MONOSPACE
            textSize = 12f
            setTextColor(getColor(R.color.text_primary))
            val params = android.widget.LinearLayout.LayoutParams(0, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            layoutParams = params
        }
        row.addView(label)

        val killButton = android.widget.Button(this).apply {
            text = "CHIUDI"
            textSize = 11f
            setOnClickListener { confirmKill(pid, name) }
        }
        row.addView(killButton)

        return row
    }

    private fun confirmKill(pid: Int, name: String) {
        AlertDialog.Builder(this)
            .setTitle("Chiudi processo")
            .setMessage("Terminare \"$name\" (pid $pid)?")
            .setPositiveButton("Chiudi") { _, _ ->
                webSocket?.send(JSONObject().put("type", "kill_process").put("pid", pid).toString())
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    override fun onDestroy() {
        webSocket?.close(1000, "Chiuso")
        super.onDestroy()
    }
}
