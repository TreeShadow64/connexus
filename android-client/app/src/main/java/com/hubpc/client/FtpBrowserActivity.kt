package com.hubpc.client

import android.content.ContentValues
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import org.apache.commons.net.ftp.FTP
import org.apache.commons.net.ftp.FTPClient
import org.apache.commons.net.ftp.FTPFile
import org.apache.commons.net.ftp.FTPReply
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.OutputStream

/** Sfoglia una condivisione FTP di un altro dispositivo (oggi solo il PC, che
 * usa lo stesso protocollo minimale del telefono — vedi ftp_server.py).
 * Connessione senza stato: ogni azione (elenco, download, upload) apre una
 * connessione FTP nuova e la chiude subito dopo, invece di tenerne aperta una
 * per tutta la sessione di navigazione — piu' semplice e robusto su una LAN
 * dove la latenza e' trascurabile. */
class FtpBrowserActivity : AppCompatActivity() {

    private lateinit var host: String
    private var port: Int = 2130
    private lateinit var shareName: String
    private var password: String = ""

    /** Percorso relativo alla radice della condivisione, es. "" oppure "foto/2024". */
    private var currentPath = ""

    private lateinit var layoutEntries: LinearLayout
    private lateinit var textPath: TextView
    private lateinit var textStatus: TextView
    private lateinit var textLog: TextView

    private val pickUploadFile = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) uploadFile(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_ftp_browser)

        host = intent.getStringExtra("ftp_ip").orEmpty()
        port = intent.getIntExtra("ftp_port", 2130)
        shareName = intent.getStringExtra("share_name") ?: "dispositivo"
        password = intent.getStringExtra("ftp_password").orEmpty()

        layoutEntries = findViewById(R.id.layoutFtpEntries)
        textPath = findViewById(R.id.textFtpPath)
        textStatus = findViewById(R.id.textFtpConnStatus)
        textLog = findViewById(R.id.textFtpLog)

        findViewById<TextView>(R.id.textShareName).text = shareName.uppercase()
        findViewById<TextView>(R.id.helpFtpBrowser).setOnClickListener {
            HelpDialogs.show(
                this, "Sfoglia condivisione",
                "Tocca una cartella per entrarci, \"..\" in cima per risalire. Tocca un file per scaricarlo " +
                    "nella cartella Download del telefono. \"CARICA UN FILE QUI\" manda un file dal telefono " +
                    "alla cartella che stai guardando in questo momento."
            )
        }
        findViewById<Button>(R.id.buttonFtpUpload).setOnClickListener { pickUploadFile.launch("*/*") }

        if (host.isEmpty()) {
            textStatus.text = "indirizzo del dispositivo mancante"
            return
        }
        loadFolder("")
    }

    private fun connectClient(): FTPClient {
        val client = FTPClient()
        client.connectTimeout = 8000
        client.connect(host, port)
        if (!FTPReply.isPositiveCompletion(client.replyCode)) {
            client.disconnect()
            throw IOException("connessione rifiutata")
        }
        if (!client.login("connexus", password)) {
            client.disconnect()
            throw IOException("password errata")
        }
        client.enterLocalPassiveMode()
        client.setFileType(FTP.BINARY_FILE_TYPE)
        return client
    }

    private fun joinPath(base: String, name: String) = if (base.isEmpty()) name else "$base/$name"

    private fun parentOf(path: String): String {
        val idx = path.lastIndexOf('/')
        return if (idx < 0) "" else path.substring(0, idx)
    }

    private fun loadFolder(path: String) {
        textStatus.text = "connessione a $shareName ($host:$port)..."
        layoutEntries.removeAllViews()
        Thread {
            var client: FTPClient? = null
            try {
                client = connectClient()
                if (path.isNotEmpty() && !client.changeWorkingDirectory("/$path")) {
                    throw IOException("cartella non trovata")
                }
                val files = (client.listFiles() ?: emptyArray())
                    .filter { it.name != "." && it.name != ".." }
                    .sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
                client.logout()
                runOnUiThread {
                    currentPath = path
                    textStatus.text = "connesso a $shareName"
                    textPath.text = if (path.isEmpty()) "/" else "/$path"
                    renderEntries(files)
                }
            } catch (e: Exception) {
                runOnUiThread {
                    textStatus.text = "errore di connessione: ${e.message}"
                    layoutEntries.removeAllViews()
                }
            } finally {
                try { client?.disconnect() } catch (e: IOException) {}
            }
        }.start()
    }

    private fun renderEntries(files: List<FTPFile>) {
        layoutEntries.removeAllViews()
        if (currentPath.isNotEmpty()) {
            layoutEntries.addView(makeUpRow())
        }
        if (files.isEmpty()) {
            layoutEntries.addView(makeInfoRow("cartella vuota"))
        }
        for (f in files) {
            layoutEntries.addView(makeEntryRow(f))
        }
    }

    private fun makeUpRow(): Button {
        val button = Button(this)
        button.text = "📁 .."
        button.isAllCaps = false
        button.typeface = Typeface.MONOSPACE
        button.textSize = 13f
        button.gravity = Gravity.START or Gravity.CENTER_VERTICAL
        button.setPadding(28, 24, 28, 24)
        button.setTextColor(getColor(R.color.text_faint))
        button.setBackgroundResource(R.drawable.bg_module_card)
        button.setOnClickListener { loadFolder(parentOf(currentPath)) }
        val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        params.bottomMargin = 8
        button.layoutParams = params
        return button
    }

    private fun makeEntryRow(entry: FTPFile): Button {
        val button = Button(this)
        val name = entry.name
        button.text = if (entry.isDirectory) "📁 $name" else "📄 $name  (${formatSize(entry.size)})"
        button.isAllCaps = false
        button.typeface = Typeface.MONOSPACE
        button.textSize = 13f
        button.gravity = Gravity.START or Gravity.CENTER_VERTICAL
        button.setPadding(28, 24, 28, 24)
        button.setTextColor(getColor(R.color.text_primary))
        button.setBackgroundResource(R.drawable.bg_module_card)
        button.setOnClickListener {
            if (entry.isDirectory) {
                loadFolder(joinPath(currentPath, name))
            } else {
                downloadFile(name)
            }
        }
        val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        params.bottomMargin = 8
        button.layoutParams = params
        return button
    }

    private fun makeInfoRow(text: String): TextView {
        val view = TextView(this)
        view.text = "• $text"
        view.setTextColor(getColor(R.color.text_faint))
        view.textSize = 12f
        view.typeface = Typeface.MONOSPACE
        view.setPadding(4, 4, 4, 4)
        return view
    }

    private fun formatSize(bytes: Long): String {
        if (bytes <= 0) return "0 B"
        val units = arrayOf("B", "KB", "MB", "GB")
        var value = bytes.toDouble()
        var unitIndex = 0
        while (value >= 1024 && unitIndex < units.size - 1) {
            value /= 1024
            unitIndex++
        }
        return "%.1f %s".format(value, units[unitIndex])
    }

    // --- download (dalla condivisione alla cartella Download del telefono) ---

    private fun openDownloadStream(name: String): OutputStream? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream")
            }
            val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return null
            contentResolver.openOutputStream(uri)
        } else {
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if (!dir.exists()) dir.mkdirs()
            FileOutputStream(File(dir, name))
        }
    }

    private fun downloadFile(name: String) {
        textLog.text = "Scaricamento di $name..."
        Thread {
            var client: FTPClient? = null
            try {
                val out = openDownloadStream(name) ?: throw IOException("impossibile creare il file di destinazione")
                client = connectClient()
                if (currentPath.isNotEmpty()) client.changeWorkingDirectory("/$currentPath")
                val ok = out.use { client.retrieveFile(name, it) }
                client.logout()
                if (!ok) throw IOException("download fallito")
                runOnUiThread {
                    textLog.text = "Scaricato in Download: $name"
                    Toast.makeText(this, "Salvato in Download/$name", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                runOnUiThread { textLog.text = "Errore scaricando $name: ${e.message}" }
            } finally {
                try { client?.disconnect() } catch (e: IOException) {}
            }
        }.start()
    }

    // --- upload (dal telefono alla cartella che si sta guardando) ---

    private fun queryFileName(uri: Uri): String? {
        contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && cursor.moveToFirst()) return cursor.getString(idx)
        }
        return null
    }

    private fun uploadFile(uri: Uri) {
        val name = queryFileName(uri) ?: "file_${System.currentTimeMillis()}"
        textLog.text = "Caricamento di $name..."
        Thread {
            var client: FTPClient? = null
            try {
                client = connectClient()
                if (currentPath.isNotEmpty()) client.changeWorkingDirectory("/$currentPath")
                val ok = contentResolver.openInputStream(uri)?.use { input -> client.storeFile(name, input) } ?: false
                client.logout()
                if (!ok) throw IOException("caricamento fallito (condivisione in sola lettura?)")
                runOnUiThread {
                    textLog.text = "Caricato: $name"
                    loadFolder(currentPath)
                }
            } catch (e: Exception) {
                runOnUiThread { textLog.text = "Errore caricando $name: ${e.message}" }
            } finally {
                try { client?.disconnect() } catch (e: IOException) {}
            }
        }.start()
    }
}
