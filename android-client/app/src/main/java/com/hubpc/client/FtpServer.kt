package com.hubpc.client

import java.io.BufferedReader
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStreamReader
import java.net.ServerSocket
import java.net.Socket
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Server FTP minimale che espone TUTTO lo storage del telefono sulla rete
 * locale (non una cartella scelta: quella e' una cosa diversa, i "file
 * condivisi in app"), cosi' il PC puo' collegarcisi come unita' di rete.
 * Sottoinsieme del protocollo: solo modalita' passiva con porta dati fissa,
 * niente resume/MLSD/active mode — sufficiente per l'uso occasionale a cui
 * e' pensata questa funzione. */
class FtpServer(
    private val root: File,
    private val password: String,
    private val readOnly: Boolean = false,
    private val controlPort: Int = 2121,
    private val dataPort: Int = 2122,
) {
    @Volatile private var running = false
    private var serverSocket: ServerSocket? = null

    fun start() {
        if (running) return
        running = true
        val socket = ServerSocket(controlPort)
        serverSocket = socket
        Thread {
            while (running) {
                try {
                    val client = socket.accept()
                    Thread { handleClient(client) }.apply { isDaemon = true; start() }
                } catch (e: Exception) {
                    // socket chiuso da stop(): esce dal ciclo al prossimo controllo di "running"
                }
            }
        }.apply { isDaemon = true; start() }
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (e: Exception) {}
        serverSocket = null
    }

    private fun handleClient(socket: Socket) {
        socket.use { sock ->
            val out = sock.getOutputStream()
            val reader = BufferedReader(InputStreamReader(sock.getInputStream()))
            var authenticated = false
            var currentDir: File = root
            var passiveServer: ServerSocket? = null

            fun reply(line: String) {
                out.write("$line\r\n".toByteArray())
                out.flush()
            }

            fun closePassive() {
                try { passiveServer?.close() } catch (e: Exception) {}
                passiveServer = null
            }

            reply("220 Hub-PC-Telefono FTP")
            try {
                while (true) {
                    val line = reader.readLine() ?: break
                    val spaceIndex = line.indexOf(' ')
                    val cmd = (if (spaceIndex >= 0) line.substring(0, spaceIndex) else line).uppercase()
                    val arg = if (spaceIndex >= 0) line.substring(spaceIndex + 1).trim() else ""

                    when (cmd) {
                        "USER" -> reply("331 Password richiesta")
                        "PASS" -> {
                            authenticated = password.isEmpty() || arg == password
                            reply(if (authenticated) "230 Accesso eseguito" else "530 Password errata")
                        }
                        "SYST" -> reply("215 UNIX Type: L8")
                        "FEAT" -> reply("211 nessuna funzione extra\r\n211 End")
                        "PWD" -> reply("257 \"/${currentDir.relativeToRoot()}\"")
                        "TYPE" -> reply("200 OK")
                        "CDUP" -> {
                            currentDir = currentDir.parentFile?.takeIf { it.startsWith(root) } ?: root
                            reply("250 OK")
                        }
                        "CWD" -> {
                            if (!authenticated) { reply("530 Accesso negato"); continue }
                            val target = resolvePath(currentDir, arg)
                            if (target != null && target.isDirectory) {
                                currentDir = target
                                reply("250 OK")
                            } else {
                                reply("550 Cartella non trovata")
                            }
                        }
                        "PASV" -> {
                            if (!authenticated) { reply("530 Accesso negato"); continue }
                            closePassive()
                            passiveServer = ServerSocket(dataPort)
                            val ip = sock.localAddress.hostAddress?.replace(".", ",") ?: "127,0,0,1"
                            reply("227 Passive Mode ($ip,${dataPort / 256},${dataPort % 256})")
                        }
                        "LIST" -> {
                            if (!authenticated) { reply("530 Accesso negato"); continue }
                            reply("150 Invio elenco")
                            passiveServer?.accept()?.use { data ->
                                val writer = data.getOutputStream()
                                for (entry in currentDir.listFiles().orEmpty()) {
                                    writer.write((formatListLine(entry) + "\r\n").toByteArray())
                                }
                                writer.flush()
                            }
                            closePassive()
                            reply("226 Fine elenco")
                        }
                        "RETR" -> {
                            if (!authenticated) { reply("530 Accesso negato"); continue }
                            val file = File(currentDir, arg)
                            if (!file.isFile || !file.startsWith(root)) {
                                reply("550 File non trovato")
                            } else {
                                reply("150 Invio file")
                                passiveServer?.accept()?.use { data ->
                                    FileInputStream(file).use { input -> input.copyTo(data.getOutputStream()) }
                                }
                                closePassive()
                                reply("226 Trasferimento completato")
                            }
                        }
                        "STOR" -> {
                            if (!authenticated) { reply("530 Accesso negato"); continue }
                            if (readOnly) { reply("550 Condivisione in sola lettura"); continue }
                            val target = File(currentDir, arg)
                            if (!target.startsWith(root)) {
                                reply("550 Percorso non valido")
                            } else {
                                reply("150 Ricezione file")
                                passiveServer?.accept()?.use { data ->
                                    FileOutputStream(target).use { output -> data.getInputStream().copyTo(output) }
                                }
                                closePassive()
                                reply("226 Trasferimento completato")
                            }
                        }
                        "NOOP" -> reply("200 OK")
                        "QUIT" -> {
                            reply("221 Ciao")
                            return
                        }
                        else -> reply("502 Comando non supportato")
                    }
                }
            } catch (e: Exception) {
                // connessione interrotta dal client: nulla da fare
            } finally {
                try { passiveServer?.close() } catch (e: Exception) {}
            }
        }
    }

    private fun File.relativeToRoot(): String =
        if (this == root) "" else toRelativeString(root)

    private fun File.startsWith(other: File): Boolean =
        canonicalPath.startsWith(other.canonicalPath)

    /** Risolve un percorso restando sempre dentro root, anche con ".." ripetuti
     * nella richiesta: e' l'unico controllo di sicurezza che ha senso qui,
     * dato che la condivisione espone deliberatamente tutto lo storage. */
    private fun resolvePath(from: File, path: String): File? {
        if (path.isEmpty()) return from
        var node = if (path.startsWith("/")) root else from
        val segments = path.trim('/').split("/").filter { it.isNotEmpty() }
        for (segment in segments) {
            node = when (segment) {
                "." -> node
                ".." -> node.parentFile?.takeIf { it.startsWith(root) } ?: root
                else -> File(node, segment).takeIf { it.startsWith(root) } ?: return null
            }
        }
        return node
    }

    private fun formatListLine(entry: File): String {
        val perm = if (entry.isDirectory) "drwxr-xr-x" else "-rw-r--r--"
        val date = SimpleDateFormat("MMM dd HH:mm", Locale.US).format(Date(entry.lastModified()))
        return "$perm 1 owner group ${entry.length()} $date ${entry.name}"
    }
}
