package com.hubpc.client

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.media.RingtoneManager
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.firestore.FieldValue
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.ListenerRegistration
import com.google.firebase.firestore.SetOptions
import com.hubpc.client.databinding.ActivityFindDeviceBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Localizza su richiesta e fa suonare un allarme su qualunque dispositivo
 * (telefoni + PC) registrato sullo stesso account Firebase, ovunque si
 * trovi — non serve essere sulla stessa rete Wi-Fi. I comandi passano da
 * Cloud Firestore; il PC fa da relay per svegliare i telefoni via notifica
 * push, dato che i client Firebase non possono mandarsi notifiche a vicenda
 * da soli. */
class FindDeviceActivity : AppCompatActivity() {

    private lateinit var binding: ActivityFindDeviceBinding
    private var devicesListener: ListenerRegistration? = null
    private var webSocket: WebSocket? = null
    private val dateFormat = SimpleDateFormat("dd/MM HH:mm", Locale.getDefault())

    // Quando si preme LOCALIZZA la mappa non c'e' ancora: si apre da sola
    // appena arriva una posizione piu' recente della richiesta, cosi' non
    // serve tornare a cercare/toccare la riga della posizione a mano.
    private var pendingLocateDeviceId: String? = null
    private var pendingLocateSince: Long = 0L

    companion object {
        // Il PC aggiorna lastSeen ogni 60s: una soglia piu' larga assorbe
        // ritardi/jitter senza dare falsi "offline".
        private const val PC_ONLINE_THRESHOLD_MS = 3 * 60_000L
    }

    private val requestLocationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) {
            Toast.makeText(this, "Senza il permesso questo telefono non potra' essere localizzato", Toast.LENGTH_LONG).show()
        }
    }

    private val pickAlarmSound = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val uri = result.data?.getParcelableExtra<Uri>(RingtoneManager.EXTRA_RINGTONE_PICKED_URI)
        val prefs = getSharedPreferences(HubApplication.PREFS_NAME, MODE_PRIVATE)
        if (uri != null) {
            prefs.edit().putString(FindDeviceService.PREF_ALARM_SOUND_URI, uri.toString()).apply()
        } else {
            prefs.edit().remove(FindDeviceService.PREF_ALARM_SOUND_URI).apply()
        }
        updateAlarmSoundLabel()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFindDeviceBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.helpFindDevice.setOnClickListener {
            HelpDialogs.show(
                this, "Trova dispositivo",
                "Localizza o fa suonare un allarme su qualunque dispositivo registrato sul tuo account, " +
                    "anche fuori casa: non serve essere sulla stessa rete Wi-Fi.\n\n" +
                    "La posizione del PC e' stimata dall'indirizzo IP (precisione a livello di citta', i " +
                    "computer normalmente non hanno GPS). Quella del telefono e' quella vera del GPS.\n\n" +
                    "I comandi passano dal PC (deve essere acceso e connesso a internet) — se e' spento " +
                    "restano in coda e arrivano appena torna online.\n\n" +
                    "SCEGLI SUONO apre il selettore di Android: ognuno puo' scegliere il proprio, anche uno " +
                    "scaricato da app come Zedge (basta che l'app lo salvi tra i suoni \"Allarme\" del telefono)."
            )
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestLocationPermission.launch(Manifest.permission.ACCESS_FINE_LOCATION)
        }

        binding.buttonChooseAlarmSound.setOnClickListener { openAlarmSoundPicker() }
        updateAlarmSoundLabel()

        DeviceRegistryService.registerThisDevice(this)
        registerPcOnce()
        listenDevices()
    }

    /** Apre il selettore di suoni di sistema: mostra sia i suoni predefiniti
     * di Android sia quelli aggiunti da altre app (es. Zedge li registra
     * come suoni "Allarme" del telefono), niente integrazione dedicata. */
    private fun openAlarmSoundPicker() {
        val prefs = getSharedPreferences(HubApplication.PREFS_NAME, MODE_PRIVATE)
        val current = prefs.getString(FindDeviceService.PREF_ALARM_SOUND_URI, null)?.let { Uri.parse(it) }
            ?: RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_ALARM)
        val intent = Intent(RingtoneManager.ACTION_RINGTONE_PICKER).apply {
            putExtra(RingtoneManager.EXTRA_RINGTONE_TYPE, RingtoneManager.TYPE_ALARM)
            putExtra(RingtoneManager.EXTRA_RINGTONE_SHOW_DEFAULT, true)
            putExtra(RingtoneManager.EXTRA_RINGTONE_SHOW_SILENT, false)
            putExtra(RingtoneManager.EXTRA_RINGTONE_EXISTING_URI, current)
            putExtra(RingtoneManager.EXTRA_RINGTONE_TITLE, "Suono allarme")
        }
        pickAlarmSound.launch(intent)
    }

    private fun updateAlarmSoundLabel() {
        val prefs = getSharedPreferences(HubApplication.PREFS_NAME, MODE_PRIVATE)
        val saved = prefs.getString(FindDeviceService.PREF_ALARM_SOUND_URI, null)
        binding.textAlarmSoundName.text = if (saved == null) {
            "predefinito di sistema"
        } else {
            try {
                RingtoneManager.getRingtone(this, Uri.parse(saved))?.getTitle(this) ?: "suono personalizzato"
            } catch (e: Exception) {
                "suono personalizzato"
            }
        }
    }

    /** Manda al PC (sul canale WebSocket gia' autenticato via LAN) un custom
     * token ottenuto dal Worker Cloudflare: gli serve una sola volta per
     * autenticarsi su Firestore con gli stessi diritti di un client
     * qualunque, senza che il PC tenga mai una credenziale admin. Se il PC
     * non e' raggiungibile ora va bene lo stesso se era gia' stato abbinato
     * in passato (tiene il refresh token da quella volta). */
    private fun registerPcOnce() {
        val ip = intent.getStringExtra("ip").orEmpty()
        val token = intent.getStringExtra("token").orEmpty()
        if (ip.isEmpty() || token.isEmpty() || FirebaseAuth.getInstance().currentUser == null) return

        RelayApi.mintPcToken { customToken ->
            if (customToken == null) {
                runOnUiThread {
                    binding.textFindStatus.text = "Impossibile preparare l'abbinamento col PC (relay non raggiungibile)"
                }
                return@mintPcToken
            }
            val client = OkHttpClient()
            val request = Request.Builder().url("ws://$ip:8765").build()
            webSocket = client.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocket.send(JSONObject().put("type", "auth").put("token", token).toString())
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    val json = try { JSONObject(text) } catch (e: Exception) { return }
                    when (json.optString("type")) {
                        "auth_ok" -> webSocket.send(
                            JSONObject().put("type", "register_account").put("custom_token", customToken).toString()
                        )
                        "account_ok" -> runOnUiThread { binding.textFindStatus.text = json.optString("message") }
                        "account_error" -> runOnUiThread { binding.textFindStatus.text = "PC: ${json.optString("message")}" }
                    }
                }

                override fun onMessage(webSocket: WebSocket, bytes: ByteString) {}

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    runOnUiThread {
                        binding.textFindStatus.text =
                            "PC non raggiungibile in rete locale per l'abbinamento (se e' gia' abbinato non serve rifarlo)"
                    }
                }
            })
        }
    }

    private fun listenDevices() {
        val uid = FirebaseAuth.getInstance().currentUser?.uid ?: return
        devicesListener = FirebaseFirestore.getInstance()
            .collection("users").document(uid).collection("devices")
            .addSnapshotListener { snapshot, error ->
                if (error != null || snapshot == null) return@addSnapshotListener
                binding.layoutDevices.removeAllViews()
                val docs = snapshot.documents.sortedBy { it.getString("type") }
                if (docs.isEmpty()) {
                    binding.layoutDevices.addView(makeInfoRow("nessun dispositivo registrato ancora"))
                }
                for (doc in docs) {
                    binding.layoutDevices.addView(makeDeviceCard(doc.id, doc.data ?: emptyMap()))
                    maybeAutoOpenMap(doc.id, doc.data ?: emptyMap())
                }
            }
    }

    /** Se questa e' la posizione che stavamo aspettando dopo un LOCALIZZA
     * (arrivata dopo la richiesta), apre la mappa da sola una volta sola. */
    private fun maybeAutoOpenMap(deviceId: String, data: Map<String, Any?>) {
        if (deviceId != pendingLocateDeviceId) return
        val location = data["lastLocation"] as? Map<*, *> ?: return
        val ts = (location["timestamp"] as? com.google.firebase.Timestamp)?.toDate()?.time ?: return
        if (ts <= pendingLocateSince) return
        val lat = (location["lat"] as? Number)?.toDouble() ?: return
        val lng = (location["lng"] as? Number)?.toDouble() ?: return
        pendingLocateDeviceId = null
        openMap(lat, lng)
    }

    private fun openMap(lat: Double, lng: Double) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("geo:$lat,$lng?q=$lat,$lng")))
        } catch (e: android.content.ActivityNotFoundException) {
            Toast.makeText(this, "Nessuna app mappe installata", Toast.LENGTH_SHORT).show()
        }
    }

    private fun makeDeviceCard(deviceId: String, data: Map<String, Any?>): LinearLayout {
        val isThisPhone = deviceId == DeviceRegistryService.deviceId(this)
        val name = data["name"] as? String ?: "dispositivo"
        val type = data["type"] as? String ?: "phone"
        val alarmActive = data["alarmActive"] as? Boolean ?: false
        val location = data["lastLocation"] as? Map<*, *>

        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.bg_module_card)
            setPadding(28, 24, 28, 24)
            val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            params.bottomMargin = 10
            layoutParams = params
        }

        val icon = if (type == "pc") "▣" else "▤"
        card.addView(TextView(this).apply {
            text = "$icon  $name${if (isThisPhone) " (questo telefono)" else ""}"
            typeface = Typeface.MONOSPACE
            textSize = 13f
            setTextColor(getColor(R.color.text_primary))
            setOnClickListener { renameDevice(deviceId, name) }
        })

        if (type == "pc") {
            val lastSeen = (data["lastSeen"] as? com.google.firebase.Timestamp)?.toDate()?.time
            val online = lastSeen != null && (System.currentTimeMillis() - lastSeen) < PC_ONLINE_THRESHOLD_MS
            card.addView(TextView(this).apply {
                text = if (online) {
                    "● online"
                } else {
                    "● offline" + (lastSeen?.let { " — ultimo contatto ${dateFormat.format(Date(it))}" } ?: " — mai visto")
                }
                typeface = Typeface.MONOSPACE
                textSize = 10f
                setTextColor(getColor(if (online) R.color.green else R.color.red))
                setPadding(0, 4, 0, 0)
            })
        }

        val locationText = if (location != null) {
            val lat = (location["lat"] as? Number)?.toDouble()
            val lng = (location["lng"] as? Number)?.toDouble()
            val ts = (location["timestamp"] as? com.google.firebase.Timestamp)?.toDate()
            "posizione: $lat, $lng" + (ts?.let { " — ${dateFormat.format(it)}" } ?: "")
        } else {
            "posizione: non ancora richiesta"
        }
        val locationRow = TextView(this).apply {
            text = locationText
            typeface = Typeface.MONOSPACE
            textSize = 10f
            setTextColor(getColor(R.color.text_faint))
            setPadding(0, 8, 0, 12)
        }
        card.addView(locationRow)
        if (location != null) {
            locationRow.setOnClickListener {
                val lat = (location["lat"] as? Number)?.toDouble() ?: return@setOnClickListener
                val lng = (location["lng"] as? Number)?.toDouble() ?: return@setOnClickListener
                openMap(lat, lng)
            }
        }

        val buttonRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        buttonRow.addView(smallButton("LOCALIZZA") {
            pendingLocateDeviceId = deviceId
            pendingLocateSince = System.currentTimeMillis()
            sendCommand(deviceId, "locate")
        })

        // Il pulsante allarme cambia etichetta subito al tocco (senza aspettare
        // il giro completo telefono -> Firestore -> PC -> conferma): la vera
        // stato arriva comunque poco dopo dal listener e ridisegna la card,
        // ma l'utente non deve restare a fissare un pulsante che non reagisce.
        lateinit var alarmButton: Button
        alarmButton = smallButton(if (alarmActive) "FERMA ALLARME" else "SUONA ALLARME") {
            val startingAlarm = alarmButton.text == "SUONA ALLARME"
            alarmButton.text = if (startingAlarm) "FERMA ALLARME" else "SUONA ALLARME"
            sendCommand(deviceId, if (startingAlarm) "alarm_start" else "alarm_stop")
        }
        buttonRow.addView(alarmButton)
        card.addView(buttonRow)

        // Non si puo' rimuovere il telefono che si sta usando ora: verrebbe
        // solo ricreato al prossimo avvio dell'app, quindi confonderebbe.
        if (!isThisPhone) {
            card.addView(smallButton("RIMUOVI") { confirmRemoveDevice(deviceId, name) }.apply {
                // smallButton() e' pensato per righe orizzontali (peso 1, larghezza
                // 0dp): qui invece va aggiunto come figlio diretto della card
                // verticale, quindi serve una larghezza vera e propria.
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { topMargin = 6 }
            })
        }

        return card
    }

    private fun renameDevice(deviceId: String, currentName: String) {
        val input = EditText(this).apply {
            setText(currentName)
            setSelection(text.length)
        }
        AlertDialog.Builder(this)
            .setTitle("Rinomina dispositivo")
            .setView(input)
            .setPositiveButton("Salva") { _, _ ->
                val newName = input.text.toString().trim()
                if (newName.isEmpty()) return@setPositiveButton
                if (deviceId == DeviceRegistryService.deviceId(this)) {
                    DeviceRegistryService.setDeviceName(this, newName)
                } else {
                    val uid = FirebaseAuth.getInstance().currentUser?.uid ?: return@setPositiveButton
                    FirebaseFirestore.getInstance()
                        .collection("users").document(uid)
                        .collection("devices").document(deviceId)
                        .set(mapOf("name" to newName), SetOptions.merge())
                }
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    private fun confirmRemoveDevice(deviceId: String, name: String) {
        AlertDialog.Builder(this)
            .setTitle("Rimuovi dispositivo")
            .setMessage("\"$name\" sparira' dall'elenco. Se l'app e' ancora installata li', potrebbe ricomparire al prossimo avvio.")
            .setPositiveButton("Rimuovi") { _, _ ->
                val uid = FirebaseAuth.getInstance().currentUser?.uid ?: return@setPositiveButton
                FirebaseFirestore.getInstance()
                    .collection("users").document(uid)
                    .collection("devices").document(deviceId)
                    .delete()
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    private fun smallButton(label: String, onClick: () -> Unit): Button = Button(this).apply {
        text = label
        textSize = 10f
        isAllCaps = false
        typeface = Typeface.MONOSPACE
        setOnClickListener { onClick() }
        val params = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        params.marginEnd = 6
        layoutParams = params
    }

    private fun sendCommand(deviceId: String, type: String) {
        val uid = FirebaseAuth.getInstance().currentUser?.uid ?: return
        FirebaseFirestore.getInstance()
            .collection("users").document(uid)
            .collection("devices").document(deviceId)
            .collection("commands")
            .add(mapOf("type" to type, "status" to "pending", "requestedAt" to FieldValue.serverTimestamp()))
        binding.textFindStatus.text = "Comando '$type' inviato"
        // Il PC ora ascolta in polling solo i comandi diretti a se stesso:
        // per gli altri dispositivi (altri telefoni) serve svegliarli via
        // push subito, invece di dipendere da un PC acceso che faccia da
        // relay come prima.
        if (deviceId != DeviceRegistryService.deviceId(this)) {
            RelayApi.sendPush(deviceId, type)
        }
    }

    private fun makeInfoRow(text: String): TextView = TextView(this).apply {
        this.text = "• $text"
        setTextColor(getColor(R.color.text_faint))
        textSize = 12f
        typeface = Typeface.MONOSPACE
        setPadding(4, 4, 4, 4)
    }

    override fun onDestroy() {
        devicesListener?.remove()
        webSocket?.close(1000, "Chiuso")
        super.onDestroy()
    }
}
