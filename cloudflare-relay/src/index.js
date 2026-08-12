// Connexus relay — l'UNICO posto in tutto il sistema che tiene la chiave di
// servizio Firebase. Fa solo due cose, entrambe dietro verifica di un
// idToken vero:
//   1. /send-push    manda una notifica push a un telefono dell'account
//      del chiamante (serve perche' i client Firebase non possono
//      mandarsi notifiche a vicenda da soli).
//   2. /mint-pc-token da' al PC dell'utente un token per accedere a
//      Firestore CON LE STESSE regole di sicurezza di un client normale
//      (niente accesso admin sul PC di nessuno).
import { verifyIdToken } from "./verify.js";
import { getGoogleAccessToken, mintCustomToken } from "./serviceAccount.js";
import { getDeviceFcmToken } from "./firestore.js";
import { sendPush } from "./fcm.js";

const ALLOWED_COMMANDS = new Set(["locate", "alarm_start", "alarm_stop"]);

function json(obj, status = 200) {
    return new Response(JSON.stringify(obj), {
        status,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
    });
}

async function handleSendPush(request, env) {
    const body = await request.json().catch(() => ({}));
    const { idToken, deviceId, cmd } = body;

    if (!ALLOWED_COMMANDS.has(cmd)) {
        return json({ ok: false, error: "comando non valido" }, 400);
    }
    if (!deviceId || typeof deviceId !== "string") {
        return json({ ok: false, error: "deviceId mancante" }, 400);
    }

    let uid;
    try {
        uid = await verifyIdToken(idToken, env.FIREBASE_PROJECT_ID);
    } catch (e) {
        return json({ ok: false, error: `autenticazione fallita: ${e.message}` }, 401);
    }

    const accessToken = await getGoogleAccessToken(env);
    const fcmToken = await getDeviceFcmToken(env, accessToken, uid, deviceId);
    if (!fcmToken) {
        return json({ ok: false, error: "dispositivo senza token push (non e' un telefono, o non ha mai aperto l'app)" }, 404);
    }

    await sendPush(env, accessToken, fcmToken, cmd);
    return json({ ok: true });
}

async function handleMintPcToken(request, env) {
    const body = await request.json().catch(() => ({}));
    const { idToken } = body;

    let uid;
    try {
        uid = await verifyIdToken(idToken, env.FIREBASE_PROJECT_ID);
    } catch (e) {
        return json({ ok: false, error: `autenticazione fallita: ${e.message}` }, 401);
    }

    const customToken = await mintCustomToken(env, uid);
    return json({ ok: true, customToken });
}

export default {
    async fetch(request, env) {
        const url = new URL(request.url);

        if (request.method === "OPTIONS") {
            return new Response(null, {
                headers: {
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type",
                },
            });
        }

        try {
            if (url.pathname === "/send-push" && request.method === "POST") {
                return await handleSendPush(request, env);
            }
            if (url.pathname === "/mint-pc-token" && request.method === "POST") {
                return await handleMintPcToken(request, env);
            }
            if (url.pathname === "/" && request.method === "GET") {
                return json({ ok: true, service: "connexus-relay" });
            }
        } catch (e) {
            return json({ ok: false, error: `errore interno: ${e.message}` }, 500);
        }

        return json({ ok: false, error: "non trovato" }, 404);
    },
};
