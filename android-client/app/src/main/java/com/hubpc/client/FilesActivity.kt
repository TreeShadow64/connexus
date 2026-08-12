package com.hubpc.client

import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.documentfile.provider.DocumentFile
import com.hubpc.client.databinding.ActivityFilesBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.io.File

/** Due schermate in una, selezionate dalle icone in basso:
 * TRASFERIMENTO — sfoglia una cartella del telefono scelta dall'utente (SAF) e
 * apre la pagina FTP separata (espone tutto lo storage, non solo la cartella).
 * PULIZIA — scansiona lo storage per spazio occupato, video, APK e file
 * di grandi dimensioni, con la possibilita' di eliminarli da qui. */
class FilesActivity : AppCompatActivity() {

    private enum class Tab { TRANSFER, CLEANER, EXPLORE }

    private lateinit var binding: ActivityFilesBinding
    private var rootFolder: DocumentFile? = null
    private val folderStack = mutableListOf<DocumentFile>()
    private var currentTab = Tab.TRANSFER
    private var scannedOnce = false

    private val exploreRoot: File = Environment.getExternalStorageDirectory()
    private val exploreStack = mutableListOf<File>()
    private var exploreLoaded = false

    private var webSocket: WebSocket? = null
    private var ip: String = ""
    private var token: String = ""

    private var largeFiles: MutableList<File> = mutableListOf()
    private var videoFiles: List<File> = emptyList()
    private var apkFiles: List<File> = emptyList()

    companion object {
        private const val PREFS_NAME = HubApplication.PREFS_NAME
        private const val PREF_FOLDER_URI = "phone_folder_uri"
        private const val LARGE_FILE_THRESHOLD = 50L * 1024 * 1024
        private val VIDEO_EXTENSIONS = setOf("mp4", "mkv", "avi", "mov", "3gp", "webm", "m4v")
    }

    private val pickFolder = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            contentResolver.takePersistableUriPermission(
                uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
            getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().putString(PREF_FOLDER_URI, uri.toString()).apply()
            openRoot(uri)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFilesBinding.inflate(layoutInflater)
        setContentView(binding.root)

        wireHelp()
        wireTabs()
        wireCleaner()

        binding.buttonChooseFolder.setOnClickListener { pickFolder.launch(null) }
        binding.buttonShareToggle.setOnClickListener {
            startActivity(Intent(this, FtpSettingsActivity::class.java).putExtra("ip", ip).putExtra("token", token))
        }

        ip = intent.getStringExtra("ip").orEmpty()
        token = intent.getStringExtra("token").orEmpty()
        if (ip.isNotEmpty() && token.isNotEmpty()) {
            connect(ip, token)
        }

        val savedUri = getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getString(PREF_FOLDER_URI, null)
        if (savedUri != null) {
            openRoot(Uri.parse(savedUri))
        }
    }

    override fun onResume() {
        super.onResume()
        requestShareList()
    }

    // --- selettore in basso ---

    private fun wireTabs() {
        binding.tabTransfer.setOnClickListener { showTab(Tab.TRANSFER) }
        binding.tabCleaner.setOnClickListener { showTab(Tab.CLEANER) }
        binding.tabExplore.setOnClickListener { showTab(Tab.EXPLORE) }
    }

    private fun showTab(tab: Tab) {
        currentTab = tab
        binding.pageTransfer.visibility = if (tab == Tab.TRANSFER) View.VISIBLE else View.GONE
        binding.pageCleaner.visibility = if (tab == Tab.CLEANER) View.VISIBLE else View.GONE
        binding.pageExplore.visibility = if (tab == Tab.EXPLORE) View.VISIBLE else View.GONE
        binding.textPageTitle.text = when (tab) {
            Tab.TRANSFER -> "GESTIONE FILE"
            Tab.CLEANER -> "PULIZIA"
            Tab.EXPLORE -> "ESPLORA"
        }
        binding.tabTransferIcon.imageTintList =
            ColorStateList.valueOf(getColor(if (tab == Tab.TRANSFER) R.color.cyan else R.color.text_faint))
        binding.tabCleanerIcon.imageTintList =
            ColorStateList.valueOf(getColor(if (tab == Tab.CLEANER) R.color.cyan else R.color.text_faint))
        binding.tabExploreIcon.imageTintList =
            ColorStateList.valueOf(getColor(if (tab == Tab.EXPLORE) R.color.cyan else R.color.text_faint))
        if (tab == Tab.CLEANER && !scannedOnce) scanStorage()
        if (tab == Tab.EXPLORE && !exploreLoaded) {
            exploreLoaded = true
            showExploreFolder(exploreRoot)
        }
    }

    private fun wireHelp() {
        binding.helpFiles.setOnClickListener {
            when (currentTab) {
                Tab.TRANSFER -> HelpDialogs.show(
                    this, "Gestione file",
                    "Scegli una cartella del telefono (foto, documenti, download...): resta quella predefinita finche' non la cambi. " +
                        "Tocca un file per aprirlo con l'app adatta sul telefono, tocca \"..\" in cima per risalire.\n\n" +
                        "CONDIVIDI IN RETE (FTP) apre una pagina a parte: espone TUTTO lo storage del telefono (non solo " +
                        "questa cartella) a qualsiasi programma FTP, finche' resta aperta quella pagina."
                )
                Tab.CLEANER -> HelpDialogs.show(
                    this, "Pulizia",
                    "Scansiona lo storage del telefono e mostra spazio occupato, video, file APK (installer) e file di " +
                        "grandi dimensioni (oltre 50 MB), con la possibilita' di eliminarli direttamente da qui.\n\n" +
                        "Non può cancellare la cache di altre app (WhatsApp, ecc.): Android non lo permette a un'app " +
                        "normale senza permessi di sistema, solo alle app di pulizia preinstallate dal produttore."
                )
                Tab.EXPLORE -> HelpDialogs.show(
                    this, "Esplora",
                    "Sfoglia tutta la memoria del telefono, senza dover scegliere prima nessuna cartella: tocca una " +
                        "cartella per entrarci, tocca \"..\" in cima per risalire, tocca un file per aprirlo con l'app adatta."
                )
            }
        }
    }

    // --- cartella locale (SAF) ---

    private fun openRoot(uri: Uri) {
        val doc = DocumentFile.fromTreeUri(this, uri)
        if (doc == null || !doc.exists()) {
            binding.textCurrentPath.text = "cartella non piu' accessibile, scegline un'altra"
            rootFolder = null
            return
        }
        rootFolder = doc
        folderStack.clear()
        showFolder(doc)
    }

    private fun navigateUp() {
        if (folderStack.isEmpty()) return
        folderStack.removeAt(folderStack.size - 1)
        showFolder(folderStack.lastOrNull() ?: rootFolder ?: return)
    }

    private fun openSubfolder(folder: DocumentFile) {
        val current = if (folderStack.isEmpty()) rootFolder else folderStack.last()
        if (current != null) folderStack.add(current)
        showFolder(folder)
    }

    private fun showFolder(folder: DocumentFile) {
        binding.textCurrentPath.text = folder.name ?: "/"
        val entries = folder.listFiles().sortedWith(compareBy({ !it.isDirectory }, { it.name?.lowercase() ?: "" }))
        binding.layoutFiles.removeAllViews()
        if (folderStack.isNotEmpty()) {
            binding.layoutFiles.addView(makeUpRow())
        }
        for (entry in entries) {
            binding.layoutFiles.addView(makeRow(entry))
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
        button.setOnClickListener { navigateUp() }
        val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        params.bottomMargin = 8
        button.layoutParams = params
        return button
    }

    private fun makeRow(entry: DocumentFile): Button {
        val button = Button(this)
        val name = entry.name ?: "?"
        button.text = if (entry.isDirectory) "📁 $name" else "📄 $name  (${formatSize(entry.length())})"
        button.isAllCaps = false
        button.typeface = Typeface.MONOSPACE
        button.textSize = 13f
        button.gravity = Gravity.START or Gravity.CENTER_VERTICAL
        button.setPadding(28, 24, 28, 24)
        button.setTextColor(getColor(R.color.text_primary))
        button.setBackgroundResource(R.drawable.bg_module_card)
        button.setOnClickListener {
            if (entry.isDirectory) {
                openSubfolder(entry)
            } else {
                openFile(entry)
            }
        }
        val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        params.bottomMargin = 8
        button.layoutParams = params
        return button
    }

    private fun openFile(entry: DocumentFile) {
        try {
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(entry.uri, entry.type ?: "*/*")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(intent)
        } catch (e: android.content.ActivityNotFoundException) {
            Toast.makeText(this, "Nessuna app in grado di aprire questo file", Toast.LENGTH_SHORT).show()
        }
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

    // --- esplora (tutta la memoria, senza scegliere una cartella) ---

    private fun showExploreFolder(folder: File) {
        if (!hasAllFilesAccess()) {
            binding.textExplorePath.text = "serve il permesso \"tutti i file\": aprilo dalla pagina FTP (CONDIVIDI IN RETE)"
            binding.layoutExplore.removeAllViews()
            return
        }
        binding.textExplorePath.text = if (folder == exploreRoot) "/" else folder.path.removePrefix(exploreRoot.path)
        val entries = (folder.listFiles() ?: emptyArray())
            .sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
        binding.layoutExplore.removeAllViews()
        if (exploreStack.isNotEmpty()) {
            binding.layoutExplore.addView(makeExploreUpRow())
        }
        for (entry in entries) {
            binding.layoutExplore.addView(makeExploreRow(entry))
        }
    }

    private fun navigateExploreUp() {
        if (exploreStack.isEmpty()) return
        val folder = exploreStack.removeAt(exploreStack.size - 1)
        showExploreFolder(folder)
    }

    private fun openExploreSubfolder(folder: File) {
        val current = if (exploreStack.isEmpty()) exploreRoot else exploreStack.last()
        exploreStack.add(current)
        showExploreFolder(folder)
    }

    private fun makeExploreUpRow(): Button {
        val button = Button(this)
        button.text = "📁 .."
        button.isAllCaps = false
        button.typeface = Typeface.MONOSPACE
        button.textSize = 13f
        button.gravity = Gravity.START or Gravity.CENTER_VERTICAL
        button.setPadding(28, 24, 28, 24)
        button.setTextColor(getColor(R.color.text_faint))
        button.setBackgroundResource(R.drawable.bg_module_card)
        button.setOnClickListener { navigateExploreUp() }
        val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        params.bottomMargin = 8
        button.layoutParams = params
        return button
    }

    private fun makeExploreRow(entry: File): Button {
        val button = Button(this)
        button.text = if (entry.isDirectory) "📁 ${entry.name}" else "📄 ${entry.name}  (${formatSize(entry.length())})"
        button.isAllCaps = false
        button.typeface = Typeface.MONOSPACE
        button.textSize = 13f
        button.gravity = Gravity.START or Gravity.CENTER_VERTICAL
        button.setPadding(28, 24, 28, 24)
        button.setTextColor(getColor(R.color.text_primary))
        button.setBackgroundResource(R.drawable.bg_module_card)
        button.setOnClickListener {
            if (entry.isDirectory) {
                openExploreSubfolder(entry)
            } else {
                openExploreFile(entry)
            }
        }
        val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        params.bottomMargin = 8
        button.layoutParams = params
        return button
    }

    private fun openExploreFile(entry: File) {
        try {
            val uri = androidx.core.content.FileProvider.getUriForFile(this, "${packageName}.fileprovider", entry)
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, contentResolver.getType(uri) ?: "*/*")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(intent)
        } catch (e: android.content.ActivityNotFoundException) {
            Toast.makeText(this, "Nessuna app in grado di aprire questo file", Toast.LENGTH_SHORT).show()
        }
    }

    // --- pulizia ---

    private fun wireCleaner() {
        binding.buttonRescan.setOnClickListener { scanStorage() }
        binding.buttonDeleteVideos.setOnClickListener { confirmDeleteAll(videoFiles, "video") }
        binding.buttonDeleteApks.setOnClickListener { confirmDeleteAll(apkFiles, "APK") }
    }

    private fun hasAllFilesAccess(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.R || Environment.isExternalStorageManager()

    private fun scanStorage() {
        if (!hasAllFilesAccess()) {
            binding.textStorageUsage.text = "serve il permesso \"tutti i file\": aprilo dalla pagina FTP (CONDIVIDI IN RETE)"
            return
        }
        scannedOnce = true
        binding.textStorageUsage.text = "scansione in corso..."
        Thread {
            val root = Environment.getExternalStorageDirectory()
            val stat = android.os.StatFs(root.path)
            val total = stat.totalBytes
            val free = stat.availableBytes
            val used = total - free

            val large = mutableListOf<File>()
            val videos = mutableListOf<File>()
            val apks = mutableListOf<File>()

            fun walk(dir: File) {
                val entries = dir.listFiles() ?: return
                for (entry in entries) {
                    if (entry.isDirectory) {
                        walk(entry)
                    } else {
                        if (entry.length() >= LARGE_FILE_THRESHOLD) large.add(entry)
                        when (entry.extension.lowercase()) {
                            in VIDEO_EXTENSIONS -> videos.add(entry)
                            "apk" -> apks.add(entry)
                        }
                    }
                }
            }
            try {
                walk(root)
            } catch (e: Exception) {
                // scansione interrotta da un errore di I/O: si mostra quel che si e' trovato finora
            }
            large.sortByDescending { it.length() }

            runOnUiThread {
                largeFiles = large.take(50).toMutableList()
                videoFiles = videos
                apkFiles = apks
                renderCleanerResults(used, total)
            }
        }.start()
    }

    private fun renderCleanerResults(used: Long, total: Long) {
        val percent = if (total > 0) (used * 100 / total).toInt() else 0
        binding.textStorageUsage.text = "${formatSize(used)} / ${formatSize(total)}"
        binding.donutStorage.percent = percent
        binding.textStoragePercent.text = "$percent%"
        binding.textVideoSummary.text = "${videoFiles.size} file — ${formatSize(videoFiles.sumOf { it.length() })}"
        binding.textApkSummary.text = "${apkFiles.size} file — ${formatSize(apkFiles.sumOf { it.length() })}"

        binding.layoutLargeFiles.removeAllViews()
        if (largeFiles.isEmpty()) {
            binding.layoutLargeFiles.addView(makeInfoRow("nessun file di grandi dimensioni trovato"))
        }
        for (file in largeFiles) {
            binding.layoutLargeFiles.addView(makeLargeFileRow(file))
        }
    }

    private fun makeLargeFileRow(file: File): LinearLayout {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundResource(R.drawable.bg_module_card)
            setPadding(24, 20, 24, 20)
            val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            params.bottomMargin = 8
            layoutParams = params
        }
        val label = TextView(this).apply {
            text = "${file.name}\n${formatSize(file.length())}"
            typeface = Typeface.MONOSPACE
            textSize = 12f
            setTextColor(getColor(R.color.text_primary))
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        row.addView(label)
        val deleteButton = Button(this).apply {
            text = "ELIMINA"
            textSize = 11f
            setOnClickListener { confirmDeleteFile(file, row) }
        }
        row.addView(deleteButton)
        return row
    }

    private fun confirmDeleteFile(file: File, row: View) {
        AlertDialog.Builder(this)
            .setTitle("Elimina file")
            .setMessage("Eliminare \"${file.name}\"?")
            .setPositiveButton("Elimina") { _, _ ->
                if (file.delete()) {
                    binding.layoutLargeFiles.removeView(row)
                    largeFiles.remove(file)
                } else {
                    Toast.makeText(this, "Impossibile eliminare il file", Toast.LENGTH_SHORT).show()
                }
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    private fun confirmDeleteAll(files: List<File>, label: String) {
        if (files.isEmpty()) {
            Toast.makeText(this, "Nessun file $label da eliminare", Toast.LENGTH_SHORT).show()
            return
        }
        AlertDialog.Builder(this)
            .setTitle("Elimina $label")
            .setMessage("Eliminare ${files.size} file $label (${formatSize(files.sumOf { it.length() })})?")
            .setPositiveButton("Elimina") { _, _ ->
                var deleted = 0
                for (file in files) {
                    if (file.delete()) deleted++
                }
                Toast.makeText(this, "$deleted file eliminati", Toast.LENGTH_SHORT).show()
                scanStorage()
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    // --- registro "cartelle condivise" (sola lettura qui: l'avvio vero e' nella pagina FTP) ---

    private fun connect(ip: String, token: String) {
        val client = OkHttpClient()
        val request = Request.Builder().url("ws://$ip:8765").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "auth").put("token", token).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runOnUiThread { handleMessage(text) }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {}

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {}
        })
    }

    private fun handleMessage(text: String) {
        val json = try { JSONObject(text) } catch (e: Exception) { return }
        when (json.optString("type")) {
            "auth_ok" -> requestShareList()
            "share_list" -> renderSharedDevices(json.optJSONArray("devices"))
        }
    }

    private fun requestShareList() {
        webSocket?.send(JSONObject().put("type", "list_shares").toString())
    }

    private fun renderSharedDevices(devices: org.json.JSONArray?) {
        binding.layoutSharedDevices.removeAllViews()
        if (devices == null || devices.length() == 0) {
            binding.layoutSharedDevices.addView(makeInfoRow("nessuna condivisione in rete al momento"))
            return
        }
        for (i in 0 until devices.length()) {
            val device = devices.optJSONObject(i) ?: continue
            val name = device.optString("name", "dispositivo")
            val folder = device.optString("folder", "")
            val ip = device.optString("ip", "")
            val port = device.optInt("ftp_port", 0)
            makeInfoRow("$name — \"$folder\"  ftp://$ip:$port").let { binding.layoutSharedDevices.addView(it) }
        }
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

    override fun onDestroy() {
        webSocket?.close(1000, "Chiuso")
        super.onDestroy()
    }
}
