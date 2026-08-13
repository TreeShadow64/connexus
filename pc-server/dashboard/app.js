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
                <div class="hud-row-name" onclick='openFtpBrowser(${JSON.stringify(share)})'>${share.name || "dispositivo"}</div>
                <div class="hud-row-detail">"${share.folder || "?"}" — ftp://${share.ip}:${share.ftp_port} (tocca il nome per sfogliare)</div>
            </div>
            <span class="hud-dot on"></span>
        </div>
    `).join("");
}

// ---------- browser FTP condivisioni ----------

let ftpState = null; // { ip, port, password, name, path }

function openFtpBrowser(share) {
    const password = prompt(`Password FTP per "${share.name || "dispositivo"}":`);
    if (password === null) return;
    ftpState = { ip: share.ip, port: share.ftp_port, password, name: share.name || "dispositivo", path: "" };
    document.getElementById("ftpModalTitle").textContent = ftpState.name;
    document.getElementById("ftpModalBackdrop").hidden = false;
    loadFtpList();
}

function closeFtpBrowser() {
    document.getElementById("ftpModalBackdrop").hidden = true;
    ftpState = null;
}

function ftpUrl(endpoint) {
    const p = new URLSearchParams({
        ip: ftpState.ip, port: ftpState.port, password: ftpState.password, path: ftpState.path,
    });
    return `${endpoint}?${p.toString()}`;
}

async function loadFtpList() {
    document.getElementById("ftpModalPath").textContent = "/" + ftpState.path;
    document.getElementById("ftpModalEntries").innerHTML = `<div class="hud-mono" style="font-size:12px; color:var(--text-dim);">caricamento...</div>`;
    try {
        const res = await fetch(ftpUrl("/ftp/list"));
        const result = await res.json();
        if (!result.ok) throw new Error(result.error || "errore sconosciuto");
        renderFtpEntries(result.entries);
    } catch (e) {
        document.getElementById("ftpModalEntries").innerHTML = `<div class="hud-mono" style="font-size:12px; color:var(--red);">Errore: ${e.message}</div>`;
    }
}

function renderFtpEntries(entries) {
    const container = document.getElementById("ftpModalEntries");
    const rows = [];
    if (ftpState.path) {
        rows.push(`<div class="ftp-entry" onclick="navigateFtpUp()">.. (su)</div>`);
    }
    if (entries.length === 0) {
        rows.push(`<div class="hud-mono" style="font-size:12px; color:var(--text-faint); padding:8px 4px;">cartella vuota</div>`);
    }
    for (const entry of entries) {
        const icon = entry.is_dir ? "▤" : "▢";
        const sizeText = entry.is_dir ? "" : `<span class="ftp-entry-size">${formatBytes(entry.size)}</span>`;
        rows.push(`
            <div class="ftp-entry" onclick='onFtpEntryClick(${JSON.stringify(entry.name)}, ${entry.is_dir})'>
                <span>${icon} ${entry.name}</span>
                ${sizeText}
            </div>
        `);
    }
    container.innerHTML = rows.join("");
}

function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function onFtpEntryClick(name, isDir) {
    if (isDir) {
        ftpState.path = ftpState.path ? `${ftpState.path}/${name}` : name;
        loadFtpList();
    } else {
        const path = ftpState.path ? `${ftpState.path}/${name}` : name;
        const savedPath = ftpState.path;
        ftpState.path = path;
        window.location.href = ftpUrl("/ftp/download");
        ftpState.path = savedPath;
    }
}

function navigateFtpUp() {
    const parts = ftpState.path.split("/");
    parts.pop();
    ftpState.path = parts.join("/");
    loadFtpList();
}

async function onFtpUploadSelected(event) {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file || !ftpState) return;
    const targetPath = ftpState.path ? `${ftpState.path}/${file.name}` : file.name;
    const statusEl = document.getElementById("ftpModalStatus");
    statusEl.textContent = `caricamento di ${file.name}...`;
    const savedPath = ftpState.path;
    ftpState.path = targetPath;
    try {
        const res = await fetch(ftpUrl("/ftp/upload"), { method: "PUT", body: file });
        const result = await res.json();
        statusEl.textContent = result.ok ? "caricato." : `errore: ${result.error}`;
    } catch (e) {
        statusEl.textContent = `errore: ${e.message}`;
    } finally {
        ftpState.path = savedPath;
        loadFtpList();
    }
}

// ---------- sistema ----------

let updateCheckResult = null;

async function checkForUpdate() {
    updateCheckResult = { checking: true };
    renderSistemaView(lastStatus);
    updateCheckResult = await runAction("check_update");
    renderSistemaView(lastStatus);
}

async function applyUpdate(zipUrl) {
    if (!confirm("Il PC si riavvierà per installare l'aggiornamento. Continuare?")) return;
    updateCheckResult = { applying: true };
    renderSistemaView(lastStatus);
    const result = await runAction("start_update", { zip_url: zipUrl });
    if (!result.ok) {
        alert("Aggiornamento fallito: " + (result.error || "errore sconosciuto"));
        updateCheckResult = null;
        renderSistemaView(lastStatus);
    }
    // se ok, il processo attuale sta per chiudersi da solo per lasciare
    // aggiornare i file: status.json smettera' di rispondere per un attimo.
}

function renderSistemaView(status) {
    let updateValue = `versione ${status.app_version}`;
    let updateActions = `<button class="hud-button" onclick="checkForUpdate()">CONTROLLA AGGIORNAMENTI</button>`;
    let updateDot = "on";
    if (updateCheckResult) {
        if (updateCheckResult.checking) {
            updateValue = `versione ${status.app_version} — controllo in corso...`;
            updateActions = "";
        } else if (updateCheckResult.applying) {
            updateValue = "installazione in corso, il programma si riavvia tra poco...";
            updateActions = "";
            updateDot = "warn";
        } else if (updateCheckResult.update_available) {
            updateValue = `versione ${status.app_version} — disponibile ${updateCheckResult.version}`;
            updateActions = `<button class="hud-button" onclick="applyUpdate('${updateCheckResult.zip_url}')">AGGIORNA ORA</button>`;
            updateDot = "warn";
        } else {
            updateValue = `versione ${status.app_version} — già aggiornato`;
        }
    }

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
        statCard("aggiornamenti · github releases", "Versione PC", updateValue, updateDot, updateActions),
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
