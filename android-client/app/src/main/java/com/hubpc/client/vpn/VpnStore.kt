package com.hubpc.client.vpn

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Configurazioni WireGuard importate. Contengono chiavi private: stanno solo in
 * questo archivio cifrato (chiave nel Keystore di Android) e non vengono mai
 * scritte in chiaro, nei log o nel progetto. */
class VpnStore(context: Context) {

    private val prefs = EncryptedSharedPreferences.create(
        context,
        "vpn_configs",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun names(): List<String> = prefs.all.keys
        .filter { it.startsWith(CONFIG_PREFIX) }
        .map { it.removePrefix(CONFIG_PREFIX) }
        .sortedWith(compareBy({ countryName(it) }, { it }))

    fun get(name: String): String? = prefs.getString(CONFIG_PREFIX + name, null)

    fun put(name: String, text: String) {
        prefs.edit().putString(CONFIG_PREFIX + name, text).apply()
    }

    fun remove(name: String) {
        prefs.edit().remove(CONFIG_PREFIX + name).apply()
        if (selected == name) selected = null
    }

    var selected: String?
        get() = prefs.getString(SELECTED_KEY, null)?.takeIf { get(it) != null }
        set(value) {
            prefs.edit().putString(SELECTED_KEY, value).apply()
        }

    companion object {
        private const val CONFIG_PREFIX = "cfg:"
        private const val SELECTED_KEY = "selected"

        private val COUNTRIES = mapOf(
            "CA" to "Canada", "CH" to "Svizzera", "JP" to "Giappone", "MX" to "Messico",
            "NL" to "Paesi Bassi", "NO" to "Norvegia", "PL" to "Polonia", "RO" to "Romania",
            "SG" to "Singapore", "US" to "Stati Uniti", "DE" to "Germania", "FR" to "Francia",
            "GB" to "Regno Unito", "IT" to "Italia", "ES" to "Spagna", "SE" to "Svezia",
        )

        /** "wg-JP-FREE-6.conf" -> "JP-FREE#6"; altri nomi: solo caratteri sicuri. */
        fun nameFromFile(fileName: String): String {
            val base = fileName.removeSuffix(".conf").removePrefix("wg-")
            val match = Regex("^([A-Za-z]{2})-FREE-(\\d+)$").find(base)
            if (match != null) return "${match.groupValues[1].uppercase()}-FREE#${match.groupValues[2]}"
            return base.replace(Regex("[^A-Za-z0-9#_.-]"), "_").take(40).ifEmpty { "vpn" }
        }

        fun countryName(name: String): String {
            val code = name.take(2).uppercase()
            return COUNTRIES[code] ?: code
        }

        /** "JP-FREE#6" -> "Giappone · JP-FREE#6" */
        fun displayName(name: String): String = "${countryName(name)} · $name"
    }
}
