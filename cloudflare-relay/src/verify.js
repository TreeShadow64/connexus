// Verifica i token ID di Firebase Auth mandati dal telefono/PC: e' cosi'
// che il Worker sa "chi sta chiamando" senza dover fidarsi ciecamente di un
// uid dichiarato a mano (che chiunque potrebbe falsificare).
import { importX509, jwtVerify, decodeProtectedHeader } from "jose";

const CERTS_URL =
    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com";

let certsCache = null;
let certsCacheExpiry = 0;

async function getGoogleCerts() {
    const now = Date.now();
    if (certsCache && now < certsCacheExpiry) return certsCache;
    const res = await fetch(CERTS_URL);
    if (!res.ok) throw new Error("impossibile scaricare i certificati Google");
    certsCache = await res.json();
    // I certificati ruotano periodicamente: 1 ora di cache basta e avanza.
    certsCacheExpiry = now + 60 * 60 * 1000;
    return certsCache;
}

/** Ritorna l'uid del chiamante se il token e' valido, altrimenti lancia. */
export async function verifyIdToken(idToken, projectId) {
    if (!idToken) throw new Error("idToken mancante");
    const { kid } = decodeProtectedHeader(idToken);
    if (!kid) throw new Error("token senza kid");

    const certs = await getGoogleCerts();
    const pem = certs[kid];
    if (!pem) throw new Error("chiave di firma sconosciuta (certificati ruotati?)");

    const key = await importX509(pem, "RS256");
    const { payload } = await jwtVerify(idToken, key, {
        issuer: `https://securetoken.google.com/${projectId}`,
        audience: projectId,
    });

    if (!payload.sub) throw new Error("token senza uid");
    return payload.sub;
}
