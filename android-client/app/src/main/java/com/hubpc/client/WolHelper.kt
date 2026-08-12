package com.hubpc.client

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/** Invio del "magic packet" Wake-on-LAN, condiviso tra Sistema e il popup
 * di alimentazione di Mouse/Tastiera. */
object WolHelper {
    private const val WOL_PORT = 9

    fun send(macAddress: String, onResult: (success: Boolean, message: String) -> Unit) {
        Thread {
            try {
                val macBytes = macAddress.split(":", "-").map { it.toInt(16).toByte() }.toByteArray()
                if (macBytes.size != 6) {
                    onResult(false, "Indirizzo MAC non valido: $macAddress")
                    return@Thread
                }
                val repeated = ByteArray(96)
                for (i in 0 until 16) macBytes.copyInto(repeated, i * 6)
                val packet = ByteArray(6) { 0xFF.toByte() } + repeated
                val socket = DatagramSocket()
                socket.broadcast = true
                val address = InetAddress.getByName("255.255.255.255")
                socket.send(DatagramPacket(packet, packet.size, address, WOL_PORT))
                socket.close()
                onResult(true, "Magic packet inviato a $macAddress")
            } catch (e: Exception) {
                onResult(false, "Errore Wake-on-LAN: ${e.message}")
            }
        }.start()
    }
}
