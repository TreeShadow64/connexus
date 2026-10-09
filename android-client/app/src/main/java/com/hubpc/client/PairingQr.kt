package com.hubpc.client

import com.journeyapps.barcodescanner.ScanOptions

/** Codice QR mostrato dalla dashboard del PC: connexus://pair?ip=...&token=...&name=... */
object PairingQr {

    data class Pairing(val name: String, val ip: String, val token: String)

    fun scanOptions(): ScanOptions = ScanOptions()
        .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
        .setPrompt("Inquadra il codice QR mostrato sul PC")
        .setBeepEnabled(false)
        .setOrientationLocked(false)

    /** Accetta solo il formato atteso e valori puliti: un codice di altro tipo
     * (o malevolo) viene ignorato invece di finire tra le connessioni. */
    fun parse(text: String?): Pairing? {
        if (text.isNullOrBlank()) return null
        val uri = try { java.net.URI(text.trim()) } catch (e: Exception) { return null }
        if (uri.scheme != "connexus" || uri.host != "pair") return null
        val params = (uri.rawQuery ?: return null).split("&").mapNotNull {
            val parts = it.split("=", limit = 2)
            if (parts.size == 2) parts[0] to java.net.URLDecoder.decode(parts[1], "UTF-8") else null
        }.toMap()
        val ip = params["ip"]?.trim().orEmpty()
        val token = params["token"]?.trim().orEmpty()
        val name = params["name"]?.trim().orEmpty()
            .replace(Regex("[^\\p{L}\\p{N} _.-]"), "_").take(40).ifEmpty { "PC" }
        if (!Regex("^[A-Za-z0-9.-]{3,100}$").matches(ip)) return null
        if (!Regex("^[A-Za-z0-9]{16,128}$").matches(token)) return null
        return Pairing(name, ip, token)
    }
}
