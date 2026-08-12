// Connexus PC Hub — dashboard client.

const POLL_INTERVAL_MS = 2000;
const DEVICES_POLL_INTERVAL_MS = 3000;

const DAY_NAMES = ["DOM", "LUN", "MAR", "MER", "GIO", "VEN", "SAB"];
const MONTH_NAMES = ["GEN", "FEB", "MAR", "APR", "MAG", "GIU", "LUG", "AGO", "SET", "OTT", "NOV", "DIC"];
const PC_ONLINE_THRESHOLD_MS = 3 * 60 * 1000;

let currentView = "overview";
let lastStatus = null;
let devicesPollHandle = null;

// ---------- orologio ----------

function tickClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, "0");
    const m = String(now.getMinutes()).padStart(2, "0");
    const s = String(now.getSeconds()).padStart(2, "0");
    document.getElementById("clockLabel").textContent = `${h}:${m}:${s}`;
    document.getElementById("dateLabel").textContent =
        `${DAY_NAMES[now.getDay()]} ${String(now.getDate()).padStart(2, "0")} ${MONTH_NAMES[now.getMonth()]}`;
}

// ---------- navigazione ----------

function switchView(name) {
    currentView = name;
    document.querySelectorAll(".view").forEach(el => {
        el.hidden = el.id !== `view-${name}`;
    });
    document.querySelectorAll(".app-nav-item").forEach(el => {
        el.classList.toggle("active", el.dataset.view === name);
    });
    if (name === "trova") {
        loadDevices();
        if (!devicesPollHandle) devicesPollHandle = setInterval(loadDevices, DEVICES_POLL_INTERVAL_MS);
    } else if (devicesPollHandle) {
        clearInterval(devicesPollHandle);
        devicesPollHandle = null;
    }
}

document.querySelectorAll(".app-nav-item").forEach(el => {
    el.addEventListener("click", () => switchView(el.dataset.view));
});

// ---------- azioni (pulsanti che comandano il backend) ----------

async function runAction(action, payload = {}) {
    try {
        const res = await fetch("/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, payload }),
        });
        return await res.json();
    } catch (e) {
        return { ok: false, error: String(e) };
    }
}

// ---------- card generiche ----------

function statCard(eyebrow, label, value, dotState, actionsHtml = "") {
    return `
        <div class="hud-panel">
            <div class="hud-panel-header">
                <span class="hud-eyebrow">${eyebrow}</span>
                <span class="hud-dot ${dotState}"></span>
            </div>
            <div class="hud-title" style="font-size:13px; margin-bottom:6px;">${label}</div>
            <div class="hud-value hud-mono" style="font-size:13px; margin-bottom:${actionsHtml ? "10px" : "0"};">${value}</div>
            ${actionsHtml}
        </div>
    `;
}

// ---------- overview ----------

function renderStatCards(status) {
    const cards = [
        statCard("condivisione schermo", "Schermo PC",
            status.screen_viewers > 0 ? `${status.screen_viewers} spettatori` : "0 spettatori",
            status.screen_viewers > 0 ? "on" : "off"),
        statCard("specchio dal telefono", "Projector",
            status.projector_active ? "in ricezione" : "fermo",
            status.projector_active ? "on" : "off"),
        statCard("webcam verso pc", "Virtual Camera",
            status.virtualcam_error ? "errore" : (status.virtualcam_active ? "attiva" : "non attiva"),
            status.virtualcam_error ? "warn" : (status.virtualcam_active ? "on" : "off")),
        statCard("webcam verso telefono", "Camera UVC",
            status.uvccam_active ? `attiva (cam ${status.uvccam_device})` : "non attiva",
            status.uvccam_active ? "on" : "off"),
        statCard("ftp / cartelle", "Condivisioni file",
            `${status.shares.length} attive`,
            status.shares.length > 0 ? "on" : "off"),
        statCard("sblocco uac da remoto", "Servizio elevato",
            status.service_installed ? "installato" : "non installato",
            status.service_installed ? "on" : "warn"),
    ];
    document.getElementById("statCards").innerHTML = cards.join("");
}

function renderTopbar(status) {
    document.getElementById("hostnameLabel").textContent = `HOST // ${status.hostname.toUpperCase()}`;
    document.getElementById("clientsLabel").textContent = `${status.connected_clients} DISPOSITIVI CONNESSI`;
    const accountEl = document.getElementById("accountStatus");
    if (status.account_paired) {
        accountEl.textContent = "abbinato";
        accountEl.style.color = "var(--green)";
    } else {
        accountEl.textContent = "non abbinato";
        accountEl.style.color = "var(--amber)";
    }
}

// ---------- schermo & proiezione ----------

function renderSchermoView(status) {
    const cards = [
        statCard(
            "condivisione schermo · in uscita verso il telefono", "Schermo PC",
            status.screen_viewers > 0 ? `${status.screen_viewers} spettatori collegati ora` : "nessuno spettatore",
            status.screen_viewers > 0 ? "on" : "off"
        ),
        statCard(
            "specchio dal telefono · in entrata, finestra nativa", "Projector",
            status.projector_active ? "in ricezione — vedi la finestra 'Specchio telefono'" : "fermo",
            status.projector_active ? "on" : "off",
            status.projector_active
                ? `<button class="hud-button danger" onclick="runAction('stop_projector').then(loadStatus)">CHIUDI FINESTRA</button>`
                : ""
        ),
    ];
    document.getElementById("schermoCards").innerHTML = cards.join("");
}

// ---------- webcam ----------

function renderWebcamView(status) {
    const cards = [
        statCard(
            "virtual camera · webcam del telefono verso il pc", "Virtual Camera",
            status.virtualcam_error ? status.virtualcam_error : (status.virtualcam_active ? "attiva — selezionabile come 'OBS Virtual Camera'" : "non attiva"),
            status.virtualcam_error ? "warn" : (status.virtualcam_active ? "on" : "off"),
            status.virtualcam_active
                ? `<button class="hud-button danger" onclick="runAction('stop_virtualcam').then(loadStatus)">FERMA</button>`
                : ""
        ),
        statCard(
            "camera uvc · webcam fisica del pc verso il telefono", "Camera UVC",
            status.uvccam_active ? `in streaming (dispositivo camera ${status.uvccam_device})` : "non attiva",
            status.uvccam_active ? "on" : "off",
            status.uvccam_active
                ? `<button class="hud-button danger" onclick="runAction('stop_uvccam').then(loadStatus)">FERMA</button>`
                : ""
        ),
    ];
    document.getElementById("webcamCards").innerHTML = cards.join("");
}

// ---------- file & rete ----------

function renderReteView(status) {
    const container = document.getElementById("shareRows");
    if (status.shares.length === 0) {
        container.innerHTML = `<div class="hud-panel hud-mono" style="color:var(--text-dim); font-size:12px;">nessuna condivisione attiva al momento</div>`;
        return;
    }
    container.innerHTML = status.shares.map(share => `
        <div class="hud-row">
            <div class="hud-row-main">
                <div class="hud-row-name" style="cursor:default;">${share.name || "dispositivo"}</div>
                <div class="hud-row-detail">"${share.folder || "?"}" — ftp://${share.ip}:${share.ftp_port}</div>
            </div>
            <span class="hud-dot on"></span>
        </div>
    `).join("");
}

// ---------- sistema ----------

function renderSistemaView(status) {
    const cards = [
        statCard(
            "servizio elevato · sblocco uac / schermata di blocco da remoto", "Servizio Windows",
            status.service_installed ? "installato e attivo" : "non installato",
            status.service_installed ? "on" : "warn"
        ),
        statCard(
            "trova dispositivo · relay firebase", "Abbinamento account",
            status.account_paired ? "abbinato, in ascolto" : "non abbinato — apri 'Trova dispositivo' dal telefono",
            status.account_paired ? "on" : "warn"
        ),
    ];
    if (!status.service_installed) {
        cards.push(`
            <div class="hud-panel">
                <div class="hud-panel-header"><span class="hud-eyebrow">come installarlo</span></div>
                <div class="hud-mono" style="font-size:11px; color:var(--text-dim); line-height:1.6;">
                    Esegui <b style="color:var(--white)">install_service.bat</b> nella cartella pc-server come amministratore.
                    Serve solo per sbloccare lo schermo del PC da remoto quando compare una finestra UAC.
                </div>
            </div>
        `);
    }
    document.getElementById("sistemaCards").innerHTML = cards.join("");
}

// ---------- trova dispositivo ----------

function formatLastSeen(iso) {
    if (!iso) return null;
    return new Date(iso).getTime();
}

async function loadDevices() {
    const result = await runAction("list_devices");
    const container = document.getElementById("deviceRows");
    if (!result.devices) {
        container.innerHTML = `<div class="hud-panel hud-mono" style="color:var(--amber); font-size:12px;">account non abbinato: apri "Trova dispositivo" dal telefono almeno una volta</div>`;
        return;
    }
    if (result.devices.length === 0) {
        container.innerHTML = `<div class="hud-panel hud-mono" style="color:var(--text-dim); font-size:12px;">nessun dispositivo registrato ancora</div>`;
        return;
    }
    const ownId = lastStatus ? lastStatus.own_device_id : null;
    container.innerHTML = result.devices
        .sort((a, b) => (a.type || "").localeCompare(b.type || ""))
        .map(device => renderDeviceRow(device, ownId))
        .join("");
}

function renderDeviceRow(device, ownId) {
    const isThisPc = device.id === ownId;
    const icon = device.type === "pc" ? "▣" : "▤";
    const loc = device.lastLocation;
    let locationHtml = `<span style="color:var(--text-faint);">posizione non ancora richiesta</span>`;
    if (loc && typeof loc.lat === "number" && typeof loc.lng === "number") {
        const when = loc.timestamp ? new Date(loc.timestamp).toLocaleString("it-IT") : "";
        locationHtml = `<a href="https://www.google.com/maps?q=${loc.lat},${loc.lng}" target="_blank" rel="noopener">${loc.lat.toFixed(4)}, ${loc.lng.toFixed(4)}</a>${when ? " — " + when : ""}`;
    }

    let onlineHtml = "";
    if (device.type === "pc") {
        const lastSeenMs = formatLastSeen(device.lastSeen);
        const online = lastSeenMs && (Date.now() - lastSeenMs) < PC_ONLINE_THRESHOLD_MS;
        onlineHtml = `<div class="hud-row-detail" style="color:${online ? "var(--green)" : "var(--red)"};">● ${online ? "online" : "offline"}</div>`;
    }

    const alarmActive = !!device.alarmActive;

    return `
        <div class="hud-row">
            <div class="hud-row-main">
                <div class="hud-row-name" onclick="renameDevicePrompt('${device.id}', ${JSON.stringify(device.name || "dispositivo")})">
                    ${icon} ${device.name || "dispositivo"}${isThisPc ? " (questo PC)" : ""}
                </div>
                ${onlineHtml}
                <div class="hud-row-detail">${locationHtml}</div>
            </div>
            <div class="hud-row-actions">
                <button class="hud-button" onclick="runAction('device_command', {device_id:'${device.id}', command:'locate'})">LOCALIZZA</button>
                <button class="hud-button ${alarmActive ? "danger" : ""}"
                        onclick="runAction('device_command', {device_id:'${device.id}', command:'${alarmActive ? "alarm_stop" : "alarm_start"}'}).then(loadDevices)">
                    ${alarmActive ? "FERMA ALLARME" : "SUONA ALLARME"}
                </button>
                ${!isThisPc ? `<button class="hud-button danger" onclick="removeDevicePrompt('${device.id}', ${JSON.stringify(device.name || "dispositivo")})">RIMUOVI</button>` : ""}
            </div>
        </div>
    `;
}

function renameDevicePrompt(deviceId, currentName) {
    const name = prompt("Rinomina dispositivo:", currentName);
    if (name && name.trim()) {
        runAction("rename_device", { device_id: deviceId, name: name.trim() }).then(loadDevices);
    }
}

function removeDevicePrompt(deviceId, name) {
    if (confirm(`Rimuovere "${name}" dall'elenco?`)) {
        runAction("remove_device", { device_id: deviceId }).then(loadDevices);
    }
}

// ---------- polling stato ----------

async function loadStatus() {
    try {
        const res = await fetch("/status.json", { cache: "no-store" });
        const status = await res.json();
        lastStatus = status;
        renderTopbar(status);
        if (currentView === "overview") renderStatCards(status);
        if (currentView === "schermo") renderSchermoView(status);
        if (currentView === "webcam") renderWebcamView(status);
        if (currentView === "rete") renderReteView(status);
        if (currentView === "sistema") renderSistemaView(status);
    } catch (e) {
        document.getElementById("systemStatus").textContent = "NON RAGGIUNGIBILE";
        document.getElementById("systemDot").className = "hud-dot off";
    }
}

loadStatus();
tickClock();
setInterval(loadStatus, POLL_INTERVAL_MS);
setInterval(tickClock, 1000);
