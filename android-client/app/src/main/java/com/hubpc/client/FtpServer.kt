package com.hubpc.client

import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.RandomAccessFile
import java.net.ServerSocket
import java.net.Socket
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.SSLSocket

/** Server FTP minimale che espone TUTTO lo storage del telefono sulla rete
 * locale (non una cartella scelta: quella e' una cosa diversa, i "file
 * condivisi in app"), cosi' il PC puo' collegarcisi come unita' di rete.
 * Sottoinsieme del protocollo — sufficiente per l'uso occasionale a cui e'
 * pensata questa funzione, ma con un minimo di irrobustimento (Fase 3),
 * simmetrico a quanto fatto in ftp_server.py sul PC:
 *   - range di porte dati invece di una fissa, per trasferimenti simultanei;
 *   - REST per riprendere un trasferimento interrotto;
 *   - blocco temporaneo dopo troppi PASS sbagliati di fila dallo stesso IP;
 *   - AUTH TLS opzionale (FTPS esplicito), certificato auto-firmato via
 *     AndroidKeyStore (vedi FtpTls.kt). */
class FtpServer(
    private val root: File,
    private val password: String,
    private val readOnly: Boolean = false,
    private val controlPort: Int = 2121,
    private val dataPortRange: IntRange = 2122..2131,
) {
    @Volatile private var running = false
    private var serverSocket: ServerSocket? = null
    private val sslContext = FtpTls.getServerContext()

    companion object {
        // Per IP, condiviso da tutte le connessioni: altrimenti basterebbe
        // riconnettersi per azzerare il contatore. In memoria, non
        // persistente — un riavvio dell'app resetta i tentativi, accettabile
        // per un uso occasionale in LAN.
        private const val MAX_FAILED_ATTEMPTS = 5
        private const val LOCKOUT_MS = 60_000L
        private val failedAttempts = ConcurrentHashMap<String, Pair<Int, Long>>()

        private fun isLockedOut(ip: String): Boolean {
            val (count, firstAttempt) = failedAttempts[ip] ?: return false
            if (count < MAX_FAILED_ATTEMPTS) return false
            if (System.currentTimeMillis() - firstAttempt >= LOCKOUT_MS) {
                failedAttempts.remove(ip)
                return false
            }
            return true
        }

        private fun recordFailedAttempt(ip: String) {
            val now = System.currentTimeMillis()
            val (count, firstAttempt) = failedAttempts[ip] ?: (0 to now)
            if (now - firstAttempt >= LOCKOUT_MS) {
                failedAttempts[ip] = 1 to now
            } else {
                failedAttempts[ip] = (count + 1) to firstAttempt
            }
        }

        private fun clearFailedAttempts(ip: String) {
            failedAttempts.remove(ip)
        }
    }

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

    /** Prova le porte del range in ordine finche' una e' libera: permette a
     * due connessioni dati di sovrapporsi senza scontrarsi. */
    private fun openPassive(): Pair<ServerSocket, Int>? {
        for (port in dataPortRange) {
            try {
                return ServerSocket(port) to port
            } catch (e: Exception) {
                // porta occupata: prova la prossima
            }
        }
        return null
    }

    private fun handleClient(socket: Socket) {
        var sock = socket
        val clientIp = socket.inetAddress?.hostAddress ?: "sconosciuto"
        var out = sock.getOutputStream()
        var reader = BufferedReader(InputStreamReader(sock.getInputStream()))
        var authenticated = false
        var currentDir: File = root
        var passiveServer: ServerSocket? = null
        var restartOffset = 0L
        var tlsActive = false
        var protectData = false

        fun reply(line: String) {
            out.write("$line\r\n".toByteArray())
            out.flush()
        }

        fun closePassive() {
            try { passiveServer?.close() } catch (e: Exception) {}
            passiveServer = null
        }

        /** Accetta la connessione dati in attesa, cifrandola se il client ha
         * chiesto PROT P dopo AUTH TLS. */
        fun acceptData(): Socket {
            val data = passiveServer!!.accept()
            if (protectData && sslContext != null) {
                val ssl = sslContext.socketFactory.createSocket(data, null, data.port, true) as SSLSocket
                ssl.useClientMode = false
                ssl.startHandshake()
                return ssl
            }
            return data
        }

        try {
            reply("220 Connexus Phone FTP")
            while (true) {
                val line = reader.readLine() ?: break
                val spaceIndex = line.indexOf(' ')
                val cmd = (if (spaceIndex >= 0) line.substring(0, spaceIndex) else line).uppercase()
                val arg = if (spaceIndex >= 0) line.substring(spaceIndex + 1).trim() else ""

                when (cmd) {
                    "USER" -> reply("331 Password richiesta")
                    "PASS" -> {
                        if (isLockedOut(clientIp)) {
                            reply("530 Troppi tentativi falliti, riprova tra qualche minuto")
                        } else {
                            authenticated = password.isEmpty() || arg == password
                            if (authenticated) {
                                clearFailedAttempts(clientIp)
                                reply("230 Accesso eseguito")
                            } else {
                                recordFailedAttempt(clientIp)
                                reply("530 Password errata")
                            }
                        }
                    }
                    "SYST" -> reply("215 UNIX Type: L8")
                    "FEAT" -> {
                        val lines = mutableListOf("211-Funzioni supportate", " REST STREAM")
                        if (sslContext != null) {
                            lines.add(" AUTH TLS")
                            lines.add(" PBSZ")
                            lines.add(" PROT")
                        }
                        lines.add("211 End")
                        reply(lines.joinToString("\r\n"))
                    }
                    "AUTH" -> {
                        if (arg.uppercase() != "TLS") {
                            reply("504 Metodo non supportato")
                        } else if (sslContext == null) {
                            reply("502 TLS non disponibile")
                        } else if (tlsActive) {
                            reply("234 Gia' su TLS")
                        } else {
                            reply("234 AUTH TLS riuscito")
                            out.flush()
                            val ssl = sslContext.socketFactory.createSocket(sock, null, sock.port, true) as SSLSocket
                            ssl.useClientMode = false
                            ssl.startHandshake()
                            sock = ssl
                            out = ssl.outputStream
                            reader = BufferedReader(InputStreamReader(ssl.inputStream))
                            tlsActive = true
                        }
                    }
                    "PBSZ" -> reply("200 PBSZ=0")
                    "PROT" -> {
                        when (arg.uppercase()) {
                            "P" -> { protectData = true; reply("200 Canale dati protetto") }
                            "C" -> { protectData = false; reply("200 Canale dati in chiaro") }
                            else -> reply("504 Livello non supportato")
                        }
                    }
                    "PWD" -> reply("257 \"/${currentDir.relativeToRoot()}\"")
                    "TYPE" -> reply("200 OK")
                    "REST" -> {
                        if (!authenticated) { reply("530 Accesso negato"); continue }
                        val offset = arg.toLongOrNull()
                        if (offset == null || offset < 0) {
                            reply("501 Offset non valido")
                        } else {
                            restartOffset = offset
                            reply("350 Riprendi da $offset")
                        }
                    }
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
                        val opened = openPassive()
                        if (opened == null) {
                            reply("425 Nessuna porta dati disponibile")
                        } else {
                            val (dataSocket, port) = opened
                            passiveServer = dataSocket
                            val ip = sock.localAddress.hostAddress?.replace(".", ",") ?: "127,0,0,1"
                            reply("227 Passive Mode ($ip,${port / 256},${port % 256})")
                        }
                    }
                    "LIST" -> {
                        if (!authenticated) { reply("530 Accesso negato"); continue }
                        reply("150 Invio elenco")
                        try {
                            val data = acceptData()
                            data.use {
                                val writer = it.outputStream
                                for (entry in currentDir.listFiles().orEmpty()) {
                                    writer.write((formatListLine(entry) + "\r\n").toByteArray())
                                }
                                writer.flush()
                            }
                        } catch (e: Exception) {
                            // connessione dati interrotta: si chiude comunque sotto
                        }
                        closePassive()
                        reply("226 Fine elenco")
                    }
                    "RETR" -> {
                        if (!authenticated) { reply("530 Accesso negato"); continue }
                        val file = File(currentDir, arg)
                        val offset = restartOffset
                        restartOffset = 0
                        if (!file.isFile || !file.startsWith(root)) {
                            reply("550 File non trovato")
                        } else {
                            reply("150 Invio file")
                            try {
                                val data = acceptData()
                                data.use {
                                    RandomAccessFile(file, "r").use { raf ->
                                        if (offset > 0) raf.seek(offset)
                                        val buffer = ByteArray(65536)
                                        val out2 = it.outputStream
                                        while (true) {
                                            val n = raf.read(buffer)
                                            if (n <= 0) break
                                            out2.write(buffer, 0, n)
                                        }
                                        out2.flush()
                                    }
                                }
                            } catch (e: Exception) {
                                // connessione dati interrotta: si chiude comunque sotto
                            }
                            closePassive()
                            reply("226 Trasferimento completato")
                        }
                    }
                    "STOR" -> {
                        if (!authenticated) { reply("530 Accesso negato"); continue }
                        if (readOnly) { reply("550 Condivisione in sola lettura"); continue }
                        val target = File(currentDir, arg)
                        val offset = restartOffset
                        restartOffset = 0
                        if (!target.startsWith(root)) {
                            reply("550 Percorso non valido")
                        } else {
                            reply("150 Ricezione file")
                            try {
                                val data = acceptData()
                                data.use {
                                    RandomAccessFile(target, "rw").use { raf ->
                                        if (offset > 0) raf.seek(offset) else raf.setLength(0)
                                        val buffer = ByteArray(65536)
                                        val in2 = it.inputStream
                                        while (true) {
                                            val n = in2.read(buffer)
                                            if (n <= 0) break
                                            raf.write(buffer, 0, n)
                                        }
                                    }
                                }
                            } catch (e: Exception) {
                                // connessione dati interrotta: si chiude comunque sotto
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
            try { sock.close() } catch (e: Exception) {}
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
