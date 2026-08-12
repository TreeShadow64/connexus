package com.hubpc.client

import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import com.hubpc.client.databinding.ActivityFtpAdvancedBinding

/** Impostazioni aggiuntive della condivisione FTP: si salvano da sole a ogni
 * modifica, non serve un pulsante "salva" — le legge FtpSettingsActivity al
 * prossimo avvio della condivisione. */
class FtpAdvancedSettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivityFtpAdvancedBinding

    companion object {
        private const val PREFS_NAME = HubApplication.PREFS_NAME
        const val PREF_FTP_PASSWORD = "ftp_password"
        const val PREF_FTP_PORT = "ftp_port"
        const val PREF_FTP_READ_ONLY = "ftp_read_only"
        const val PREF_FTP_KEEP_SCREEN_ON = "ftp_keep_screen_on"
        const val PREF_FTP_RANDOM_PORT = "ftp_random_port"
        const val PREF_FTP_ANONYMOUS = "ftp_anonymous"
        const val DEFAULT_FTP_PORT = 2121
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFtpAdvancedBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)

        binding.switchKeepScreenOn.isChecked = prefs.getBoolean(PREF_FTP_KEEP_SCREEN_ON, true)
        binding.switchRandomPort.isChecked = prefs.getBoolean(PREF_FTP_RANDOM_PORT, false)
        binding.editFtpPort.setText(prefs.getInt(PREF_FTP_PORT, DEFAULT_FTP_PORT).toString())
        val anonymous = prefs.getBoolean(PREF_FTP_ANONYMOUS, true)
        binding.switchAnonymous.isChecked = anonymous
        binding.editFtpPassword.setText(prefs.getString(PREF_FTP_PASSWORD, ""))
        binding.switchReadOnly.isChecked = prefs.getBoolean(PREF_FTP_READ_ONLY, false)

        updatePortFieldVisibility()
        updatePasswordFieldVisibility()

        binding.switchKeepScreenOn.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_FTP_KEEP_SCREEN_ON, checked).apply()
        }
        binding.switchRandomPort.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_FTP_RANDOM_PORT, checked).apply()
            updatePortFieldVisibility()
        }
        binding.editFtpPort.addTextChangedListener(object : TextWatcher {
            override fun afterTextChanged(s: Editable?) {
                val port = s.toString().toIntOrNull() ?: DEFAULT_FTP_PORT
                prefs.edit().putInt(PREF_FTP_PORT, port).apply()
            }
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
        })
        binding.switchAnonymous.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_FTP_ANONYMOUS, checked).apply()
            updatePasswordFieldVisibility()
        }
        binding.editFtpPassword.addTextChangedListener(object : TextWatcher {
            override fun afterTextChanged(s: Editable?) {
                prefs.edit().putString(PREF_FTP_PASSWORD, s.toString()).apply()
            }
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
        })
        binding.switchReadOnly.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_FTP_READ_ONLY, checked).apply()
        }
    }

    private fun updatePortFieldVisibility() {
        val visible = if (binding.switchRandomPort.isChecked) View.GONE else View.VISIBLE
        binding.labelFtpPort.visibility = visible
        binding.editFtpPort.visibility = visible
    }

    private fun updatePasswordFieldVisibility() {
        val visible = if (binding.switchAnonymous.isChecked) View.GONE else View.VISIBLE
        binding.labelFtpPassword.visibility = visible
        binding.editFtpPassword.visibility = visible
    }
}
