package com.hubpc.client

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.LinearLayout
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import com.google.firebase.auth.FirebaseAuth
import com.hubpc.client.databinding.ActivitySettingsBinding
import com.hubpc.client.databinding.DialogProfileBinding
import com.hubpc.client.ui.Hud
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private var profiles: MutableList<ConnectionProfile> = mutableListOf()

    companion object {
        private const val PREFS_NAME = HubApplication.PREFS_NAME
        private const val FEEDBACK_EMAIL = "dario.ryzza@gmail.com"
        private const val RELEASES_API = "https://api.github.com/repos/TreeShadow64/connexus/releases/latest"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val user = FirebaseAuth.getInstance().currentUser
        binding.textAccountEmail.text = user?.email ?: "accesso non configurato"
        binding.buttonSignOut.setOnClickListener {
            FirebaseAuth.getInstance().signOut()
            val intent = Intent(this, LoginActivity::class.java)
            intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
            startActivity(intent)
            finish()
        }

        binding.helpConnection.setOnClickListener { HelpDialogs.showConnectionHelp(this) }
        binding.buttonAddProfile.setOnClickListener { showProfileDialog(null) }
        profiles = ConnectionProfiles.load(this)
        renderProfiles()

        binding.buttonFeedback.setOnClickListener {
            val intent = Intent(Intent.ACTION_SENDTO).apply {
                data = Uri.parse("mailto:")
                putExtra(Intent.EXTRA_EMAIL, arrayOf(FEEDBACK_EMAIL))
                putExtra(Intent.EXTRA_SUBJECT, "Connexus - Feedback")
            }
            startActivity(Intent.createChooser(intent, "Invia feedback"))
        }

        binding.textAppVersion.text = "versione ${packageManager.getPackageInfo(packageName, 0).versionName}"

        binding.buttonUpdateApp.setOnClickListener { startUpdate() }
        checkForUpdate()

        binding.buttonResetData.setOnClickListener { confirmReset() }
    }

    private fun renderProfiles() {
        binding.profilesContainer.removeAllViews()
        for ((index, profile) in profiles.withIndex()) {
            val row = Hud.row(
                this,
                name = if (index == 0) "${profile.name} (primaria)" else profile.name,
                detail = profile.ip,
                onClick = { showProfileDialog(index) },
            )
            if (index > 0) row.addView(makeRowButton("▲") { moveProfile(index, index - 1) })
            if (index < profiles.size - 1) row.addView(makeRowButton("▼") { moveProfile(index, index + 1) })
            row.addView(makeRowButton("×", Hud.Variant.DANGER) { deleteProfile(index) })
            binding.profilesContainer.addView(row)
        }
    }

    private fun makeRowButton(label: String, variant: Hud.Variant = Hud.Variant.NORMAL, onClick: () -> Unit): View =
        Hud.button(this, label, variant, onClick).apply {
            minWidth = 0
            minimumWidth = 0
            minHeight = Hud.dp(this@SettingsActivity, 34)
            setPadding(Hud.dp(this@SettingsActivity, 10), 0, Hud.dp(this@SettingsActivity, 10), 0)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = Hud.dp(this@SettingsActivity, 6) }
        }

    private fun moveProfile(from: Int, to: Int) {
        val item = profiles.removeAt(from)
        profiles.add(to, item)
        ConnectionProfiles.save(this, profiles)
        renderProfiles()
    }

    private fun deleteProfile(index: Int) {
        AlertDialog.Builder(this)
            .setTitle("Rimuovi connessione")
            .setMessage("Rimuovere \"${profiles[index].name}\"?")
            .setPositiveButton("Rimuovi") { _, _ ->
                profiles.removeAt(index)
                ConnectionProfiles.save(this, profiles)
                renderProfiles()
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    private fun showProfileDialog(editIndex: Int?) {
        val dialogBinding = DialogProfileBinding.inflate(layoutInflater)
        if (editIndex != null) {
            val profile = profiles[editIndex]
            dialogBinding.editProfileName.setText(profile.name)
            dialogBinding.editProfileIp.setText(profile.ip)
            dialogBinding.editProfileToken.setText(profile.token)
        }
        AlertDialog.Builder(this)
            .setTitle(if (editIndex == null) "Nuova connessione" else "Modifica connessione")
            .setView(dialogBinding.root)
            .setPositiveButton("Salva") { _, _ ->
                val name = dialogBinding.editProfileName.text.toString().trim().ifEmpty { "PC" }
                val ip = dialogBinding.editProfileIp.text.toString().trim()
                val token = dialogBinding.editProfileToken.text.toString().trim()
                if (ip.isEmpty() || token.isEmpty()) return@setPositiveButton
                val profile = ConnectionProfile(name, ip, token)
                if (editIndex == null) profiles.add(profile) else profiles[editIndex] = profile
                ConnectionProfiles.save(this, profiles)
                renderProfiles()
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    private var latestTag: String? = null
    private var latestApkUrl: String? = null

    private fun installedVersion(): String =
        try { packageManager.getPackageInfo(packageName, 0).versionName.orEmpty() } catch (e: Exception) { "" }

    private fun versionParts(v: String): List<Int> =
        v.removePrefix("v").split(".").mapNotNull { it.toIntOrNull() }

    private fun isNewer(tag: String, current: String): Boolean {
        val a = versionParts(tag)
        val b = versionParts(current)
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }
            val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return false
    }

    /** Controllo silenzioso: all'apertura della pagina dice subito se c'e' una
     * versione nuova, senza scaricare nulla. */
    private fun checkForUpdate(onDone: (() -> Unit)? = null) {
        Thread {
            try {
                val apiResponse = OkHttpClient().newCall(Request.Builder().url(RELEASES_API).build()).execute()
                if (!apiResponse.isSuccessful) throw java.io.IOException("HTTP ${apiResponse.code}")
                val release = JSONObject(apiResponse.body?.string().orEmpty())
                val assets = release.getJSONArray("assets")
                var apkUrl: String? = null
                for (i in 0 until assets.length()) {
                    val asset = assets.getJSONObject(i)
                    if (asset.getString("name").endsWith(".apk")) {
                        apkUrl = asset.getString("browser_download_url")
                        break
                    }
                }
                val tag = release.optString("tag_name")
                runOnUiThread {
                    latestTag = tag
                    latestApkUrl = apkUrl
                    val current = installedVersion()
                    when {
                        apkUrl == null -> binding.textUpdateStatus.text = "Nessun APK nell'ultima release"
                        isNewer(tag, current) -> {
                            binding.textUpdateStatus.text = "Disponibile la versione ${tag.removePrefix("v")} (hai la $current)"
                            binding.buttonUpdateApp.text = "AGGIORNA A ${tag.removePrefix("v")}"
                        }
                        else -> binding.textUpdateStatus.text = "Sei gia' alla versione piu' recente ($current)"
                    }
                    onDone?.invoke()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    binding.textUpdateStatus.text = "Controllo aggiornamenti non riuscito: ${e.message}"
                    onDone?.invoke()
                }
            }
        }.start()
    }

    private fun startUpdate() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            binding.textUpdateStatus.text = "Concedi il permesso di installare app, poi tocca di nuovo AGGIORNA"
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
            return
        }
        binding.buttonUpdateApp.isEnabled = false
        binding.textUpdateStatus.text = "Controllo aggiornamenti..."
        checkForUpdate {
            val tag = latestTag
            val url = latestApkUrl
            if (tag != null && url != null && isNewer(tag, installedVersion())) {
                downloadAndInstall(url)
            } else {
                binding.buttonUpdateApp.isEnabled = true
            }
        }
    }

    /** Scarica l'ultima release da GitHub invece che dal PC abbinato: deve
     * funzionare anche fuori casa e col PC spento, non solo in LAN. */
    private fun downloadAndInstall(apkUrl: String) {
        binding.buttonUpdateApp.isEnabled = false
        binding.textUpdateStatus.text = "Download in corso..."
        Thread {
            try {
                val response = OkHttpClient().newCall(Request.Builder().url(apkUrl).build()).execute()
                if (!response.isSuccessful) throw java.io.IOException("HTTP ${response.code}")

                val updatesDir = File(cacheDir, "updates").apply { mkdirs() }
                val apkFile = File(updatesDir, "hub-client.apk")
                response.body?.byteStream()?.use { input ->
                    apkFile.outputStream().use { output -> input.copyTo(output) }
                }

                runOnUiThread {
                    binding.buttonUpdateApp.isEnabled = true
                    binding.textUpdateStatus.text = "Download completato: conferma l'installazione"
                    val apkUri = FileProvider.getUriForFile(this, "$packageName.fileprovider", apkFile)
                    val installIntent = Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(apkUri, "application/vnd.android.package-archive")
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    startActivity(installIntent)
                }
            } catch (e: Exception) {
                runOnUiThread {
                    binding.buttonUpdateApp.isEnabled = true
                    binding.textUpdateStatus.text = "Aggiornamento fallito: ${e.message}"
                }
            }
        }.start()
    }

    private fun confirmReset() {
        AlertDialog.Builder(this)
            .setTitle("Cancella dati salvati")
            .setMessage("Tutte le connessioni salvate e le preferenze verranno cancellate e dovrai rifare la configurazione iniziale. Continuare?")
            .setPositiveButton("Cancella") { _, _ ->
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().clear().apply()
                val intent = Intent(this, OnboardingActivity::class.java)
                intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
                startActivity(intent)
                finish()
            }
            .setNegativeButton("Annulla", null)
            .show()
    }
}
