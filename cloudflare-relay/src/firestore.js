// Letture minime da Firestore via REST, autenticate con l'access token
// dell'account di servizio (il Worker legge solo cio' che gli serve per
// completare un'azione gia' autorizzata dal chiamante verificato).
const BASE = "https://firestore.googleapis.com/v1";

function fieldValue(field) {
    if (!field) return null;
    if ("stringValue" in field) return field.stringValue;
    if ("booleanValue" in field) return field.booleanValue;
    if ("doubleValue" in field) return field.doubleValue;
    if ("integerValue" in field) return Number(field.integerValue);
    return null;
}

/** Legge un documento dispositivo e ritorna solo i campi che servono qui. */
export async function getDeviceFcmToken(env, accessToken, uid, deviceId) {
    const url = `${BASE}/projects/${env.FIREBASE_PROJECT_ID}/databases/(default)/documents/users/${uid}/devices/${deviceId}`;
    const res = await fetch(url, {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`lettura Firestore fallita: ${await res.text()}`);
    const doc = await res.json();
    const fields = doc.fields || {};
    return fieldValue(fields.fcmToken);
}
