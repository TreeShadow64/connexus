package com.hubpc.client

import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException

/** Cerca il PC sulla rete locale con un broadcast UDP (porta 8774): serve quando il modem gli ha
 * dato un indirizzo nuovo e quello salvato nel telefono non risponde piu'. La risposta contiene
 * solo nome, indirizzo e porta: l'accesso resta protetto dal token. */
object PcLocator {
    private const val PORT = 8774
    private val MAGIC = "CONNEXUS?".toByteArray()

    data class Found(val name: String, val ip: String, val port: Int)

    /** Da chiamare su un thread secondario. */
    fun discover(timeoutMs: Int = 2500): List<Found> {
        val found = LinkedHashMap<String, Found>()
        var socket: DatagramSocket? = null
        try {
            socket = DatagramSocket().apply {
                broadcast = true
                soTimeout = 300
            }
            val targets = mutableListOf<InetAddress>(InetAddress.getByName("255.255.255.255"))
            try {   // anche il broadcast della sottorete: alcuni modem scartano quello generico
                for (ni in NetworkInterface.getNetworkInterfaces()) {
                    if (!ni.isUp || ni.isLoopback) continue
                    for (ia in ni.interfaceAddresses) {
                        ia.broadcast?.let { targets.add(it) }
                    }
                }
            } catch (_: Exception) {
            }
            val deadline = System.currentTimeMillis() + timeoutMs
            var lastSend = 0L
            val buf = ByteArray(512)
            while (System.currentTimeMillis() < deadline) {
                if (System.currentTimeMillis() - lastSend > 700) {
                    for (target in targets) {
                        try {
                            socket.send(DatagramPacket(MAGIC, MAGIC.size, target, PORT))
                        } catch (_: Exception) {
                        }
                    }
                    lastSend = System.currentTimeMillis()
                }
                try {
                    val packet = DatagramPacket(buf, buf.size)
                    socket.receive(packet)
                    val json = JSONObject(String(packet.data, 0, packet.length))
                    if (json.optString("app") == "connexus") {
                        val ip = json.optString("ip").ifEmpty { packet.address.hostAddress.orEmpty() }
                        if (ip.isNotEmpty()) {
                            found[ip] = Found(json.optString("name", "PC"), ip, json.optInt("port", 8765))
                        }
                    }
                } catch (_: SocketTimeoutException) {
                } catch (_: Exception) {
                }
            }
        } catch (_: Exception) {
        } finally {
            socket?.close()
        }
        return found.values.toList()
    }
}
