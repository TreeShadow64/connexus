package com.hubpc.client

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class ConnectionProfile(val name: String, val ip: String, val token: String)

/** Elenco ordinato di combinazioni IP/token salvate: la prima e' la primaria,
 * usata per primo tentativo di connessione; se fallisce si passa alla successiva
 * nello stesso ordine. L'utente puo' riordinarle liberamente dalle impostazioni. */
object ConnectionProfiles {
    private const val KEY = "connection_profiles"

    fun load(context: Context): MutableList<ConnectionProfile> {
        val prefs = context.getSharedPreferences(HubApplication.PREFS_NAME, Context.MODE_PRIVATE)
        val raw = prefs.getString(KEY, null)
        if (raw == null) {
            // Migrazione dal singolo IP/token usato dalle versioni precedenti.
            val legacyIp = prefs.getString("ip", "").orEmpty()
            val legacyToken = prefs.getString("token", "").orEmpty()
            val migrated = if (legacyIp.isNotEmpty() && legacyToken.isNotEmpty()) {
                mutableListOf(ConnectionProfile("PC", legacyIp, legacyToken))
            } else {
                mutableListOf()
            }
            if (migrated.isNotEmpty()) save(context, migrated)
            return migrated
        }
        val array = JSONArray(raw)
        val list = mutableListOf<ConnectionProfile>()
        for (i in 0 until array.length()) {
            val obj = array.getJSONObject(i)
            list.add(ConnectionProfile(obj.getString("name"), obj.getString("ip"), obj.getString("token")))
        }
        return list
    }

    fun save(context: Context, profiles: List<ConnectionProfile>) {
        val array = JSONArray()
        for (p in profiles) {
            array.put(JSONObject().put("name", p.name).put("ip", p.ip).put("token", p.token))
        }
        context.getSharedPreferences(HubApplication.PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY, array.toString())
            .apply()
    }
}
