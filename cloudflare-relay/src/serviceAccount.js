// Le uniche due operazioni "con privilegi" di tutto il sistema: mandare una
// notifica push per conto di un utente gia' verificato, e far firmare al PC
// un token valido per accedere a Firestore come quello stesso utente. La
// chiave privata usata qui non lascia mai questa infrastruttura.
import { SignJWT, importPKCS8 } from "jose";

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const SCOPES = "https://www.googleapis.com/auth/firebase.messaging https://www.googleapis.com/auth/datastore";

let cachedKey = null;
let cachedKeyPem = null;

async function getPrivateKey(pem) {
    if (cachedKey && cachedKeyPem === pem) return cachedKey;
    cachedKey = await importPKCS8(pem, "RS256");
    cachedKeyPem = pem;
    return cachedKey;
}

/** Token OAuth2 di Google (non e' un token Firebase): serve per chiamare le
 * API REST di Firestore e di FCM come account di servizio. */
export async function getGoogleAccessToken(env) {
    const key = await getPrivateKey(env.SERVICE_ACCOUNT_PRIVATE_KEY);
    const now = Math.floor(Date.now() / 1000);
    const assertion = await new SignJWT({ scope: SCOPES })
        .setProtectedHeader({ alg: "RS256" })
        .setIssuer(env.SERVICE_ACCOUNT_EMAIL)
        .setSubject(env.SERVICE_ACCOUNT_EMAIL)
        .setAudience(TOKEN_URL)
        .setIssuedAt(now)
        .setExpirationTime(now + 3600)
        .sign(key);

    const res = await fetch(TOKEN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
            grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
            assertion,
        }),
    });
    if (!res.ok) throw new Error(`token Google fallito: ${await res.text()}`);
    const data = await res.json();
    return data.access_token;
}

/** Token custom di Firebase Auth: e' quello che permette al PC di "accedere
 * come" lo stesso utente gia' loggato sul telefono, senza mai vedere una
 * password ne' toccare la chiave di servizio. Va scambiato lato PC con un
 * vero idToken tramite l'endpoint signInWithCustomToken. */
export async function mintCustomToken(env, uid) {
    const key = await getPrivateKey(env.SERVICE_ACCOUNT_PRIVATE_KEY);
    const now = Math.floor(Date.now() / 1000);
    return await new SignJWT({ uid })
        .setProtectedHeader({ alg: "RS256" })
        .setIssuer(env.SERVICE_ACCOUNT_EMAIL)
        .setSubject(env.SERVICE_ACCOUNT_EMAIL)
        .setAudience("https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit")
        .setIssuedAt(now)
        .setExpirationTime(now + 3600)
        .sign(key);
}
