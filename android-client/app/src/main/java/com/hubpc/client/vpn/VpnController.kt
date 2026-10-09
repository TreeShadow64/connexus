package com.hubpc.client.vpn

import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.Handler
import android.os.Looper
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.util.concurrent.TimeUnit

/** Accende e spegne la VPN dentro Connexus (motore WireGuard ufficiale): nessuna
 * altra app da aprire. Lo stato e' quello reale del tunnel, non quello presunto. */
object VpnController {

    private val main = Handler(Looper.getMainLooper())
    private var backend: GoBackend? = null

    @Volatile var activeName: String? = null
        private set

    private val tunnel = object : Tunnel {
        override fun getName() = "connexus"
        override fun onStateChange(newState: Tunnel.State) {
            if (newState == Tunnel.State.DOWN) activeName = null
        }
    }

    private fun backend(context: Context): GoBackend =
        backend ?: GoBackend(context.applicationContext).also { backend = it }

    /** Intent da lanciare la prima volta: Android chiede il consenso alla VPN. */
    fun permissionIntent(context: Context): Intent? = VpnService.prepare(context)

    fun isUp(context: Context): Boolean =
        backend(context).getState(tunnel) == Tunnel.State.UP

    /** Handshake con il server riuscito = tunnel davvero funzionante. */
    fun handshakeDone(context: Context): Boolean = try {
        val stats = backend(context).getStatistics(tunnel)
        stats.peers().any { (stats.peer(it)?.latestHandshakeEpochMillis() ?: 0L) > 0L }
    } catch (e: Exception) {
        false
    }

    fun traffic(context: Context): Pair<Long, Long> = try {
        val stats = backend(context).getStatistics(tunnel)
        Pair(stats.totalRx(), stats.totalTx())
    } catch (e: Exception) {
        Pair(0L, 0L)
    }

    fun connect(context: Context, name: String, configText: String, onDone: (String?) -> Unit) {
        Thread {
            val error = try {
                val config = Config.parse(ByteArrayInputStream(configText.toByteArray()))
                backend(context).setState(tunnel, Tunnel.State.UP, config)
                activeName = name
                null
            } catch (e: Exception) {
                e.message ?: e.javaClass.simpleName
            }
            main.post { onDone(error) }
        }.start()
    }

    fun disconnect(context: Context, onDone: (String?) -> Unit) {
        Thread {
            val error = try {
                backend(context).setState(tunnel, Tunnel.State.DOWN, null)
                activeName = null
                null
            } catch (e: Exception) {
                e.message ?: e.javaClass.simpleName
            }
            main.post { onDone(error) }
        }.start()
    }

    /** IP pubblico e posizione visti da fuori (servizio ipinfo.io), per mostrare
     * a quale uscita siamo agganciati. */
    fun publicInfo(onDone: (String?) -> Unit) {
        Thread {
            val text = try {
                val client = OkHttpClient.Builder().callTimeout(8, TimeUnit.SECONDS).build()
                val body = client.newCall(Request.Builder().url("https://ipinfo.io/json").build())
                    .execute().use { it.body?.string().orEmpty() }
                val json = JSONObject(body)
                val place = listOf(json.optString("city"), json.optString("country")).filter { it.isNotEmpty() }
                "IP pubblico ${json.optString("ip")}" + if (place.isNotEmpty()) " · ${place.joinToString(", ")}" else ""
            } catch (e: Exception) {
                android.util.Log.w("VpnController", "ipinfo non raggiungibile: ${e.javaClass.simpleName}: ${e.message}")
                null
            }
            main.post { onDone(text) }
        }.start()
    }
}
