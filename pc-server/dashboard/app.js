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
    if (name === "rete") showReteSubview(currentReteSubview);
}

document.querySelectorAll(".app-nav-item").forEach(el => {
    el.addEventListener("click", () => switchView(el.dataset.view));
});

document.getElementById("gearButton").addEventListener("click", () => switchView("sistema"));

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

// ---------- webcam ----------


// ---------- file & rete ----------

let currentReteSubview = "attivita";
let fzLocalPath = "";
let fzRemoteShare = null; // { ip, port, password, name }
let fzRemotePath = "";
let fzPasswordCache = {};

document.querySelectorAll("#view-rete .hud-subnav-item").forEach(el => {
    el.addEventListener("click", () => showReteSubview(el.dataset.subview));
});

function showReteSubview(name) {
    currentReteSubview = name;
    document.querySelectorAll("#view-rete .hud-subnav-item").forEach(el => {
        el.classList.toggle("active", el.dataset.subview === name);
    });
    document.getElementById("subview-attivita").hidden = name !== "attivita";
    document.getElementById("subview-filezilla").hidden = name !== "filezilla";
    if (name === "attivita") loadTransferLog();
    if (name === "filezilla") {
        fzRefreshShares();
        if (document.getElementById("fzLocalEntries").children.length === 0) fzLoadLocal();
    }
}

function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// ---------- attività trasferimenti ----------

async function loadTransferLog() {
    const container = document.getElementById("transferLog");
    try {
        const res = await fetch("/ftp/activity");
        const result = await res.json();
        const entries = result.entries || [];
        if (entries.length === 0) {
            container.innerHTML = `<div class="hud-panel hud-mono" style="color:var(--text-dim); font-size:12px;">nessun trasferimento ancora</div>`;
            return;
        }
        container.innerHTML = entries.map(e => `
            <div class="hud-row">
                <div class="hud-row-main">
                    <div class="hud-row-name" style="cursor:default;">${e.direction === "upload" ? "↑" : "↓"} ${e.filename}</div>
                    <div class="hud-row-detail">${e.direction === "upload" ? "verso" : "da"} ${e.share} — ${new Date(e.timestamp * 1000).toLocaleTimeString("it-IT")}${e.ok ? "" : " — errore: " + e.error}</div>
                </div>
                <span class="hud-dot ${e.ok ? "on" : "off"}"></span>
            </div>
        `).join("");
    } catch (e) {
        container.innerHTML = `<div class="hud-panel hud-mono" style="color:var(--red); font-size:12px;">Errore: ${e.message}</div>`;
    }
}

// ---------- Trasferimento File: pannello dual-pane ----------

let fzLogLines = [];

function fzLogAdd(text, isError) {
    const time = new Date().toLocaleTimeString("it-IT");
    fzLogLines.push({ text: `${time}  ${text}`, isError: !!isError });
    if (fzLogLines.length > 60) fzLogLines.shift();
    const el = document.getElementById("fzLog");
    el.innerHTML = fzLogLines.map(l => `<div class="fz-log-line ${l.isError ? "err" : ""}">${l.text}</div>`).join("");
    el.scrollTop = el.scrollHeight;
}

function fzRefreshShares() {
    const status = lastStatus || { shares: [] };
    const select = document.getElementById("fzShareSelect");
    const currentValue = select.value;
    select.innerHTML = ['<option value="">— scegli una condivisione —</option>']
        .concat(status.shares.map((s, i) => `<option value="${i}">${s.name || "dispositivo"} (${s.ip}:${s.ftp_port})</option>`))
        .join("");
    select.dataset.shares = JSON.stringify(status.shares);
    if ([...select.options].some(o => o.value === currentValue)) select.value = currentValue;
}

function fzOnShareSelected() {
    const select = document.getElementById("fzShareSelect");
    const shares = JSON.parse(select.dataset.shares || "[]");
    const share = shares[parseInt(select.value, 10)];
    const statusEl = document.getElementById("fzConnStatus");
    if (!share) {
        fzRemoteShare = null;
        document.getElementById("fzRemotePath").textContent = "non connesso";
        document.getElementById("fzRemoteEntries").innerHTML = "";
        document.getElementById("fzRemoteFooter").textContent = "non connesso";
        statusEl.textContent = "non connesso";
        statusEl.classList.remove("connected");
        return;
    }
    const cacheKey = `${share.ip}:${share.ftp_port}`;
    let password = fzPasswordCache[cacheKey];
    if (password === undefined) {
        password = prompt(`Password FTP per "${share.name || "dispositivo"}":`);
        if (password === null) { select.value = ""; return; }
        fzPasswordCache[cacheKey] = password;
    }
    fzRemoteShare = { ip: share.ip, port: share.ftp_port, password, name: share.name || "dispositivo" };
    fzRemotePath = "";
    statusEl.textContent = `connesso a ${fzRemoteShare.name}`;
    statusEl.classList.add("connected");
    fzLogAdd(`Connessione a ${fzRemoteShare.ip}:${fzRemoteShare.port}...`);
    fzLoadRemote();
}

async function fzLoadLocal() {
    document.getElementById("fzLocalPath").textContent = fzLocalPath || "unità disco";
    document.getElementById("fzLocalEntries").innerHTML = `<div class="hud-mono" style="font-size:12px; color:var(--text-dim);">caricamento...</div>`;
    try {
        const res = await fetch(`/local/list?path=${encodeURIComponent(fzLocalPath)}`);
        const result = await res.json();
        if (!result.ok) throw new Error(result.error || "errore sconosciuto");
        renderFzEntries("fzLocalEntries", "fzLocalFooter", result.entries, true, fzLocalPath !== "");
        fzLoadLocalTree(result.entries);
    } catch (e) {
        document.getElementById("fzLocalEntries").innerHTML = `<div class="hud-mono" style="font-size:12px; color:var(--red);">Errore: ${e.message}</div>`;
    }
}

async function fzLoadRemote() {
    if (!fzRemoteShare) return;
    document.getElementById("fzRemotePath").textContent = "/" + fzRemotePath;
    document.getElementById("fzRemoteEntries").innerHTML = `<div class="hud-mono" style="font-size:12px; color:var(--text-dim);">caricamento...</div>`;
    try {
        const p = new URLSearchParams({ ip: fzRemoteShare.ip, port: fzRemoteShare.port, password: fzRemoteShare.password, path: fzRemotePath });
        const res = await fetch(`/ftp/list?${p.toString()}`);
        const result = await res.json();
        if (!result.ok) throw new Error(result.error || "errore sconosciuto");
        renderFzEntries("fzRemoteEntries", "fzRemoteFooter", result.entries, false, fzRemotePath !== "");
        fzLogAdd(`LIST /${fzRemotePath} — ${result.entries.length} elementi`);
        fzLoadRemoteTree(result.entries);
    } catch (e) {
        document.getElementById("fzRemoteEntries").innerHTML = `<div class="hud-mono" style="font-size:12px; color:var(--red);">Errore: ${e.message}</div>`;
        fzLogAdd(`Errore: ${e.message}`, true);
    }
}

// ---------- albero cartelle (livello genitore + figli della cartella corrente) ----------

function fzLocalParentPath(path) {
    if (!path) return null; // radice (elenco unità): nessun genitore
    const trimmed = path.endsWith("\\") ? path.slice(0, -1) : path;
    const idx = trimmed.lastIndexOf("\\");
    let parent = idx >= 0 ? trimmed.substring(0, idx) : "";
    if (parent.length === 2 && parent.endsWith(":")) parent += "\\";
    return parent;
}

function fzLocalCurrentName(path) {
    const trimmed = path.endsWith("\\") ? path.slice(0, -1) : path;
    const idx = trimmed.lastIndexOf("\\");
    return idx >= 0 ? trimmed.substring(idx + 1) : trimmed;
}

function fzRemoteParentPath(path) {
    if (!path) return null;
    const parts = path.split("/");
    parts.pop();
    return parts.join("/");
}

function fzRemoteCurrentName(path) {
    const parts = path.split("/");
    return parts[parts.length - 1];
}

function renderFzTreeFlat(container, entries, openFn) {
    const rows = entries.filter(e => e.is_dir).map(e =>
        `<div class="fz-tree-node" onclick='${openFn}(${JSON.stringify(e.name)})'>▤ ${e.name}</div>`
    );
    container.innerHTML = rows.join("") || `<div class="fz-tree-node" style="color:var(--text-faint); cursor:default;">—</div>`;
}

function renderFzTreeNested(container, siblings, currentName, children, siblingGoFn, childOpenFn) {
    const rows = [];
    for (const entry of siblings) {
        if (!entry.is_dir) continue;
        const isCurrent = entry.name === currentName;
        rows.push(`<div class="fz-tree-node ${isCurrent ? "current" : ""}" onclick='${siblingGoFn}(${JSON.stringify(entry.name)})'>▤ ${entry.name}</div>`);
        if (isCurrent) {
            for (const child of children) {
                if (!child.is_dir) continue;
                rows.push(`<div class="fz-tree-node child" onclick='${childOpenFn}(${JSON.stringify(child.name)})'>▤ ${child.name}</div>`);
            }
        }
    }
    container.innerHTML = rows.join("") || `<div class="fz-tree-node" style="color:var(--text-faint); cursor:default;">—</div>`;
}

async function fzLoadLocalTree(childEntries) {
    const container = document.getElementById("fzLocalTree");
    const parent = fzLocalParentPath(fzLocalPath);
    if (parent === null) {
        renderFzTreeFlat(container, childEntries, "fzLocalOpen");
        return;
    }
    try {
        const res = await fetch(`/local/list?path=${encodeURIComponent(parent)}`);
        const result = await res.json();
        if (!result.ok) throw new Error(result.error);
        renderFzTreeNested(container, result.entries, fzLocalCurrentName(fzLocalPath), childEntries, "fzLocalTreeGo", "fzLocalOpen");
    } catch (e) {
        container.innerHTML = `<div class="fz-tree-node" style="color:var(--red); cursor:default;">errore</div>`;
    }
}

async function fzLoadRemoteTree(childEntries) {
    const container = document.getElementById("fzRemoteTree");
    if (!fzRemoteShare) { container.innerHTML = ""; return; }
    const parent = fzRemoteParentPath(fzRemotePath);
    if (parent === null) {
        renderFzTreeFlat(container, childEntries, "fzRemoteOpen");
        return;
    }
    try {
        const p = new URLSearchParams({ ip: fzRemoteShare.ip, port: fzRemoteShare.port, password: fzRemoteShare.password, path: parent });
        const res = await fetch(`/ftp/list?${p.toString()}`);
        const result = await res.json();
        if (!result.ok) throw new Error(result.error);
        renderFzTreeNested(container, result.entries, fzRemoteCurrentName(fzRemotePath), childEntries, "fzRemoteTreeGo", "fzRemoteOpen");
    } catch (e) {
        container.innerHTML = `<div class="fz-tree-node" style="color:var(--red); cursor:default;">errore</div>`;
    }
}

function fzLocalTreeGo(name) {
    const parent = fzLocalParentPath(fzLocalPath);
    if (parent === null) fzLocalPath = name;
    else if (parent === "" || parent.endsWith("\\")) fzLocalPath = parent + name;
    else fzLocalPath = parent + "\\" + name;
    fzLoadLocal();
}

function fzRemoteTreeGo(name) {
    const parent = fzRemoteParentPath(fzRemotePath);
    fzRemotePath = parent ? `${parent}/${name}` : name;
    fzLoadRemote();
}

function renderFzEntries(containerId, footerId, entries, isLocal, canGoUp) {
    const container = document.getElementById(containerId);
    const rows = [];
    if (canGoUp) {
        rows.push(`<div class="ftp-entry" onclick="${isLocal ? "fzLocalUp()" : "fzRemoteUp()"}"><span class="ftp-entry-name">.. (su)</span></div>`);
    }
    if (entries.length === 0) {
        rows.push(`<div class="hud-mono" style="font-size:12px; color:var(--text-faint); padding:8px 4px;">cartella vuota</div>`);
    }
    let fileCount = 0, dirCount = 0, totalSize = 0;
    for (const entry of entries) {
        if (entry.is_dir) dirCount++; else { fileCount++; totalSize += entry.size; }
        const icon = entry.is_dir ? "▤" : "▢";
        const sizeText = entry.is_dir ? "" : formatBytes(entry.size);
        const nav = entry.is_dir
            ? `onclick='${isLocal ? "fzLocalOpen(" : "fzRemoteOpen("}${JSON.stringify(entry.name)})'`
            : "";
        const arrow = !entry.is_dir
            ? (isLocal
                ? `<span class="ftp-entry-arrow" title="carica sulla condivisione" onclick='fzUpload(${JSON.stringify(entry.name)}); event.stopPropagation();'>→</span>`
                : `<span class="ftp-entry-arrow" title="scarica sul PC" onclick='fzDownload(${JSON.stringify(entry.name)}); event.stopPropagation();'>←</span>`)
            : "";
        rows.push(`
            <div class="ftp-entry" ${nav}>
                <span class="ftp-entry-name">${icon} ${entry.name}</span>
                <span class="ftp-entry-size">${sizeText}</span>
                <span class="ftp-entry-date">${entry.modified || ""}</span>
                ${arrow}
            </div>
        `);
    }
    container.innerHTML = rows.join("");
    document.getElementById(footerId).textContent =
        `${fileCount} file e ${dirCount} cartelle — ${formatBytes(totalSize)} totali`;
}

function fzLocalOpen(name) {
    if (!fzLocalPath) {
        fzLocalPath = name; // scelta di un'unità (es. "C:\\"), già completa
    } else if (fzLocalPath.endsWith("\\")) {
        fzLocalPath += name;
    } else {
        fzLocalPath += "\\" + name;
    }
    fzLoadLocal();
}

function fzLocalUp() {
    if (!fzLocalPath) return;
    const trimmed = fzLocalPath.endsWith("\\") ? fzLocalPath.slice(0, -1) : fzLocalPath;
    const idx = trimmed.lastIndexOf("\\");
    let parent = idx >= 0 ? trimmed.substring(0, idx) : "";
    if (parent.length === 2 && parent.endsWith(":")) parent += "\\"; // radice unità, es. "C:" -> "C:\\"
    fzLocalPath = parent;
    fzLoadLocal();
}

function fzRemoteOpen(name) {
    fzRemotePath = fzRemotePath ? `${fzRemotePath}/${name}` : name;
    fzLoadRemote();
}

function fzRemoteUp() {
    const parts = fzRemotePath.split("/");
    parts.pop();
    fzRemotePath = parts.join("/");
    fzLoadRemote();
}

function setFzStatus(text) {
    document.getElementById("fzStatus").textContent = text;
}

async function fzUpload(filename) {
    if (!fzRemoteShare) { setFzStatus("Seleziona prima una condivisione remota."); return; }
    const localPath = fzLocalPath.endsWith("\\") ? fzLocalPath + filename : `${fzLocalPath}\\${filename}`;
    setFzStatus(`Caricamento di ${filename}...`);
    try {
        const res = await fetch("/local/upload-to-remote", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                local_path: localPath, remote_path: fzRemotePath,
                ip: fzRemoteShare.ip, port: fzRemoteShare.port, password: fzRemoteShare.password,
                share_name: fzRemoteShare.name,
            }),
        });
        const result = await res.json();
        setFzStatus(result.ok ? `${filename} caricato.` : `Errore: ${result.error}`);
        fzLogAdd(result.ok ? `STOR ${filename} — completato` : `STOR ${filename} — ${result.error}`, !result.ok);
        if (result.ok) fzLoadRemote();
    } catch (e) {
        setFzStatus(`Errore: ${e.message}`);
        fzLogAdd(`STOR ${filename} — ${e.message}`, true);
    }
}

async function fzDownload(filename) {
    if (!fzRemoteShare) return;
    const remotePath = fzRemotePath ? `${fzRemotePath}/${filename}` : filename;
    setFzStatus(`Download di ${filename}...`);
    try {
        const res = await fetch("/local/download-from-remote", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                remote_path: remotePath, local_dir: fzLocalPath,
                ip: fzRemoteShare.ip, port: fzRemoteShare.port, password: fzRemoteShare.password,
                share_name: fzRemoteShare.name,
            }),
        });
        const result = await res.json();
        setFzStatus(result.ok ? `${filename} scaricato.` : `Errore: ${result.error}`);
        fzLogAdd(result.ok ? `RETR ${filename} — completato` : `RETR ${filename} — ${result.error}`, !result.ok);
        if (result.ok) fzLoadLocal();
    } catch (e) {
        setFzStatus(`Errore: ${e.message}`);
        fzLogAdd(`RETR ${filename} — ${e.message}`, true);
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
        statCard(
            "schermo pc (in uscita) + projector (in entrata) · avviati dal telefono", "Condivisione schermo",
            `Schermo PC: ${status.screen_viewers > 0 ? status.screen_viewers + " spettatori" : "nessuno spettatore"} — `
                + `Projector: ${status.projector_active ? "in ricezione" : "fermo"}`,
            (status.screen_viewers > 0 || status.projector_active) ? "on" : "off",
            status.projector_active
                ? `<button class="hud-button danger" onclick="runAction('stop_projector').then(loadStatus)">CHIUDI FINESTRA PROJECTOR</button>`
                : ""
        ),
        statCard(
            "nega nuove connessioni e interrompe quelle in corso · di default spento, sempre accessibile", "Blocco accesso remoto schermo",
            status.remote_screen_blocked ? "bloccato — il telefono non può collegarsi" : "sbloccato — accesso libero dal telefono",
            status.remote_screen_blocked ? "warn" : "on",
            `<button class="hud-button ${status.remote_screen_blocked ? "" : "danger"}"
                     onclick="runAction('set_remote_screen_blocked', {enabled: ${!status.remote_screen_blocked}}).then(loadStatus)">
                ${status.remote_screen_blocked ? "SBLOCCA" : "BLOCCA ORA"}
             </button>`
        ),
        statCard(
            "virtual camera (telefono→pc) + camera uvc (pc→telefono) · avviate dal telefono", "Webcam",
            `Virtual Camera: ${status.virtualcam_error ? status.virtualcam_error : (status.virtualcam_active ? "attiva" : "non attiva")} — `
                + `Camera UVC: ${status.uvccam_active ? "in streaming (cam " + status.uvccam_device + ")" : "non attiva"}`,
            status.virtualcam_error ? "warn" : ((status.virtualcam_active || status.uvccam_active) ? "on" : "off"),
            (status.virtualcam_active ? `<button class="hud-button danger" onclick="runAction('stop_virtualcam').then(loadStatus)">FERMA VIRTUAL CAM</button>` : "")
                + (status.uvccam_active ? `<button class="hud-button danger" onclick="runAction('stop_uvccam').then(loadStatus)">FERMA CAMERA UVC</button>` : "")
        ),
        statCard(
            "nega nuove connessioni e interrompe quelle in corso · di default spento, sempre accessibile", "Blocco accesso remoto webcam",
            status.remote_webcam_blocked ? "bloccato — il telefono non può collegarsi" : "sbloccato — accesso libero dal telefono",
            status.remote_webcam_blocked ? "warn" : "on",
            `<button class="hud-button ${status.remote_webcam_blocked ? "" : "danger"}"
                     onclick="runAction('set_remote_webcam_blocked', {enabled: ${!status.remote_webcam_blocked}}).then(loadStatus)">
                ${status.remote_webcam_blocked ? "SBLOCCA" : "BLOCCA ORA"}
             </button>`
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
        if (currentView === "rete" && currentReteSubview === "filezilla") fzRefreshShares();
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
