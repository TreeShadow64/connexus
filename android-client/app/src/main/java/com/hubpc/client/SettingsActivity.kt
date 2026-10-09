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

    private val qrScanner = registerForActivityResult(com.journeyapps.barcodescanner.ScanContract()) { result ->
        val contents = result.contents ?: return@registerForActivityResult
        val pairing = PairingQr.parse(contents)
        if (pairing == null) {
            android.widget.Toast.makeText(this, "Questo codice non e' di Connexus", android.widget.Toast.LENGTH_LONG).show()
            return@registerForActivityResult
        }
        // stesso PC gia' presente: si aggiorna invece di duplicarlo
        val existing = profiles.indexOfFirst { it.ip == pairing.ip }
        val profile = ConnectionProfile(pairing.name, pairing.ip, pairing.token)
        if (existing >= 0) profiles[existing] = profile else profiles.add(profile)
        ConnectionProfiles.save(this, profiles)
        renderProfiles()
        android.widget.Toast.makeText(this, "Collegato a ${pairing.name}", android.widget.Toast.LENGTH_LONG).show()
    }

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
        binding.buttonScanQr.setOnClickListener { qrScanner.launch(PairingQr.scanOptions()) }
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
