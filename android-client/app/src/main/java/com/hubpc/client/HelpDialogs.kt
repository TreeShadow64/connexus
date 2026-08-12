package com.hubpc.client

import android.content.Context
import androidx.appcompat.app.AlertDialog

/** Popup informativo riusabile per i pulsanti "?" di ogni sezione. */
object HelpDialogs {
    fun show(context: Context, title: String, message: String) {
        AlertDialog.Builder(context)
            .setTitle(title)
            .setMessage(message)
            .setPositiveButton("OK", null)
            .show()
    }

    fun showConnectionHelp(context: Context) {
        show(
            context, "IP e token del PC",
            "INDIRIZZO IP — sul PC, apri il Prompt dei comandi e digita \"ipconfig\": cerca la riga " +
                "\"Indirizzo IPv4\" della tua rete (di solito inizia con 192.168. o 10.). Quel numero è " +
                "l'IP da inserire qui — resta lo stesso finché non cambi rete.\n\n" +
                "TOKEN — viene generato automaticamente e stampato nella finestra nera del server ogni volta " +
                "che lo avvii sul PC (\"server.py\"), sotto la scritta \"TOKEN DI ACCESSO\". È sempre lo stesso " +
                "finché non cancelli il file auth_config.json sul PC.\n\n" +
                "Entrambi funzionano solo se telefono e PC sono sulla stessa rete Wi-Fi (oppure collegati con Tailscale)."
        )
    }
}
