package com.hubpc.client.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import androidx.core.content.FileProvider
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File

/** Controllo e installazione degli aggiornamenti dell'app da GitHub Releases.
 * Il risultato resta in memoria: la barra di navigazione lo usa per mostrare il
 * puntino "aggiornamento disponibile" su Impostazioni senza ricontrollare. */
object AppUpdater {
    private const val RELEASES_API = "https://api.github.com/repos/TreeShadow64/connexus/releases/latest"
    private const val RECHECK_MS = 30 * 60 * 1000L

    data class Latest(val tag: String, val apkUrl: String?)

    @Volatile var latest: Latest? = null
        private set
    @Volatile private var lastCheck = 0L
    @Volatile private var installed = ""
    private val main = Handler(Looper.getMainLooper())
    val listeners = mutableSetOf<() -> Unit>()

    fun installedVersion(context: Context): String {
        if (installed.isEmpty()) {
            installed = try {
                context.packageManager.getPackageInfo(context.packageName, 0).versionName.orEmpty()
            } catch (e: Exception) {
                ""
            }
        }
        return installed
    }

    private fun parts(v: String): List<Int> = v.removePrefix("v").split(".").mapNotNull { it.toIntOrNull() }

    fun isNewer(tag: String, current: String): Boolean {
        val a = parts(tag)
        val b = parts(current)
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }
            val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return false
    }

    val updateAvailable: Boolean
        get() {
            val l = latest ?: return false
            return l.apkUrl != null && installed.isNotEmpty() && isNewer(l.tag, installed)
        }

    /** Controlla l'ultima release (al massimo ogni 30 minuti, salvo force). */
    fun check(context: Context, force: Boolean = false, onDone: ((Latest?, String?) -> Unit)? = null) {
        installedVersion(context)
        val cached = latest
        if (!force && cached != null && System.currentTimeMillis() - lastCheck < RECHECK_MS) {
            main.post { onDone?.invoke(cached, null) }
            return
        }
        Thread {
            try {
                val response = OkHttpClient().newCall(Request.Builder().url(RELEASES_API).build()).execute()
                if (!response.isSuccessful) throw java.io.IOException("HTTP ${response.code}")
                val release = JSONObject(response.body?.string().orEmpty())
                val assets = release.getJSONArray("assets")
                var apkUrl: String? = null
                for (i in 0 until assets.length()) {
                    val asset = assets.getJSONObject(i)
                    if (asset.getString("name").endsWith(".apk")) {
                        apkUrl = asset.getString("browser_download_url")
                        break
                    }
                }
                latest = Latest(release.optString("tag_name"), apkUrl)
                lastCheck = System.currentTimeMillis()
                main.post {
                    listeners.toList().forEach { it() }
                    onDone?.invoke(latest, null)
                }
            } catch (e: Exception) {
                main.post { onDone?.invoke(null, e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    /** Scarica l'APK e apre l'installazione di sistema (Android chiede la conferma). */
    fun downloadAndInstall(activity: Activity, apkUrl: String, onStatus: (String) -> Unit, onFinished: () -> Unit) {
        onStatus("Download in corso...")
        Thread {
            try {
                val response = OkHttpClient().newCall(Request.Builder().url(apkUrl).build()).execute()
                if (!response.isSuccessful) throw java.io.IOException("HTTP ${response.code}")
                val updatesDir = File(activity.cacheDir, "updates").apply { mkdirs() }
                val apkFile = File(updatesDir, "hub-client.apk")
                response.body?.byteStream()?.use { input ->
                    apkFile.outputStream().use { output -> input.copyTo(output) }
                }
                activity.runOnUiThread {
                    onStatus("Download completato: conferma l'installazione")
                    val apkUri = FileProvider.getUriForFile(activity, "${activity.packageName}.fileprovider", apkFile)
                    activity.startActivity(Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(apkUri, "application/vnd.android.package-archive")
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    })
                    onFinished()
                }
            } catch (e: Exception) {
                activity.runOnUiThread {
                    onStatus("Aggiornamento fallito: ${e.message}")
                    onFinished()
                }
            }
        }.start()
    }
}
