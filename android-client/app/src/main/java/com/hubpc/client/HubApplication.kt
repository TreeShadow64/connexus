package com.hubpc.client

import android.app.Application
import androidx.appcompat.app.AppCompatDelegate

/** Applica il tema salvato (chiaro/scuro) prima che qualsiasi Activity venga creata. */
class HubApplication : Application() {

    companion object {
        const val PREFS_NAME = "hub_client_prefs"
        const val PREF_THEME = "theme"
        const val THEME_DARK = "dark"
        const val THEME_LIGHT = "light"
    }

    override fun onCreate() {
        super.onCreate()
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val theme = prefs.getString(PREF_THEME, THEME_DARK)
        AppCompatDelegate.setDefaultNightMode(
            if (theme == THEME_LIGHT) AppCompatDelegate.MODE_NIGHT_NO else AppCompatDelegate.MODE_NIGHT_YES
        )
    }
}
