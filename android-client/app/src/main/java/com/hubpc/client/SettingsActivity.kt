package com.hubpc.client

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.core.content.FileProvider
import com.google.firebase.auth.FirebaseAuth
import com.hubpc.client.databinding.ActivitySettingsBinding
import com.hubpc.client.databinding.DialogProfileBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private var profiles: MutableList<ConnectionProfile> = mutableListOf()

    companion object {
        private const val PREFS_NAME = HubApplication.PREFS_NAME
        private const val PREF_THEME = HubApplication.PREF_THEME
        private const val FEEDBACK_EMAIL = "dario.ryzza@gmail.com"
        private const val RELEASES_API = "https://api.github.com/repos/TreeShadow64/connexus/releases/latest"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)

        val user = FirebaseAuth.getInstance().currentUser
        binding.textAccountEmail.text = user?.email ?: "accesso non configurato"
        binding.buttonSignOut.setOnClickListener {
            FirebaseAuth.getInstance().signOut()
            val intent = Intent(this, LoginActivity::class.java)
            intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
            startActivity(intent)
            finish()
        }

        val currentTheme = prefs.getString(PREF_THEME, HubApplication.THEME_DARK)
        updateThemeToggle(currentTheme == HubApplication.THEME_LIGHT)
        binding.toggleThemeDark.setOnClickListener { setTheme(HubApplication.THEME_DARK, prefs) }
        binding.toggleThemeLight.setOnClickListener { setTheme(HubApplication.THEME_LIGHT, prefs) }

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

        binding.buttonResetData.setOnClickListener { confirmReset() }
    }

    private fun renderProfiles() {
        binding.profilesContainer.removeAllViews()
        val density = resources.displayMetrics.density
        for ((index, profile) in profiles.withIndex()) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setBackgroundResource(R.drawable.bg_module_card)
                setPadding((12 * density).toInt(), (10 * density).toInt(), (10 * density).toInt(), (10 * density).toInt())
                val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                params.topMargin = (6 * density).toInt()
                layoutParams = params
                isClickable = true
                isFocusable = true
                setOnClickListener { showProfileDialog(index) }
            }

            val labelColumn = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                val params = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                layoutParams = params
            }
            val nameText = TextView(this).apply {
                text = if (index == 0) "${profile.name} (primaria)" else profile.name
                setTextColor(getColor(R.color.cyan))
                typeface = android.graphics.Typeface.MONOSPACE
                textSize = 12f
            }
            val ipText = TextView(this).apply {
                text = profile.ip
                setTextColor(getColor(R.color.text_faint))
                typeface = android.graphics.Typeface.MONOSPACE
                textSize = 10f
            }
            labelColumn.addView(nameText)
            labelColumn.addView(ipText)
            row.addView(labelColumn)

            row.addView(makeRowButton("▲", index > 0) { moveProfile(index, index - 1) })
            row.addView(makeRowButton("▼", index < profiles.size - 1) { moveProfile(index, index + 1) })
            row.addView(makeRowButton("×", true, R.color.red) { deleteProfile(index) })

            binding.profilesContainer.addView(row)
        }
    }

    private fun makeRowButton(label: String, enabled: Boolean, colorRes: Int = R.color.text_dim, onClick: () -> Unit): TextView {
        val density = resources.displayMetrics.density
        return TextView(this).apply {
            text = label
            setTextColor(if (enabled) getColor(colorRes) else getColor(R.color.text_faint))
            textSize = 15f
            gravity = Gravity.CENTER
            val size = (34 * density).toInt()
            val params = LinearLayout.LayoutParams(size, size)
            params.marginStart = (4 * density).toInt()
            layoutParams = params
            isClickable = enabled
            isFocusable = enabled
            if (enabled) setOnClickListener { onClick() }
        }
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

    private fun startUpdate() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            binding.textUpdateStatus.text = "Concedi il permesso di installare app, poi tocca di nuovo AGGIORNA APP"
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
            return
        }
        downloadAndInstall()
    }

    /** Scarica l'ultima release da GitHub invece che dal PC abbinato: deve
     * funzionare anche fuori casa e col PC spento, non solo in LAN. */
    private fun downloadAndInstall() {
        binding.buttonUpdateApp.isEnabled = false
        binding.textUpdateStatus.text = "Controllo aggiornamenti..."
        Thread {
            try {
                val client = OkHttpClient()
                val apiResponse = client.newCall(Request.Builder().url(RELEASES_API).build()).execute()
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
                if (apkUrl == null) throw java.io.IOException("Nessun APK nell'ultima release")

                runOnUiThread { binding.textUpdateStatus.text = "Download in corso..." }
                val response = client.newCall(Request.Builder().url(apkUrl).build()).execute()
                if (!response.isSuccessful) throw java.io.IOException("HTTP ${response.code}")

                val updatesDir = File(cacheDir, "updates").apply { mkdirs() }
                val apkFile = File(updatesDir, "hub-client.apk")
                response.body?.byteStream()?.use { input ->
                    apkFile.outputStream().use { output -> input.copyTo(output) }
                }

                runOnUiThread {
                    binding.buttonUpdateApp.isEnabled = true
                    binding.textUpdateStatus.text = "Download completato, avvio installazione..."
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

    private fun setTheme(theme: String, prefs: android.content.SharedPreferences) {
        prefs.edit().putString(PREF_THEME, theme).apply()
        AppCompatDelegate.setDefaultNightMode(
            if (theme == HubApplication.THEME_LIGHT) AppCompatDelegate.MODE_NIGHT_NO else AppCompatDelegate.MODE_NIGHT_YES
        )
    }

    private fun updateThemeToggle(isLight: Boolean) {
        binding.toggleThemeDark.background = if (!isLight) getDrawable(R.drawable.bg_toggle_selected) else null
        binding.toggleThemeDark.setTextColor(getColor(if (!isLight) R.color.bg_deep else R.color.text_faint))
        binding.toggleThemeLight.background = if (isLight) getDrawable(R.drawable.bg_toggle_selected) else null
        binding.toggleThemeLight.setTextColor(getColor(if (isLight) R.color.bg_deep else R.color.text_faint))
    }

    private fun confirmReset() {
        AlertDialog.Builder(this)
            .setTitle("Cancella dati salvati")
            .setMessage("Tutte le connessioni salvate e le preferenze verranno cancellate e dovrai rifare la configurazione iniziale. Continuare?")
            .setPositiveButton("Cancella") { _, _ ->
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().clear().apply()
                AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
                val intent = Intent(this, OnboardingActivity::class.java)
                intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
                startActivity(intent)
                finish()
            }
            .setNegativeButton("Annulla", null)
            .show()
    }
}
