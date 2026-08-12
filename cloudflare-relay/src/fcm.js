// Invio della notifica push vera e propria (FCM HTTP v1), per conto di un
// chiamante gia' verificato.
export async function sendPush(env, accessToken, fcmToken, cmd) {
    const url = `https://fcm.googleapis.com/v1/projects/${env.FIREBASE_PROJECT_ID}/messages:send`;
    const res = await fetch(url, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${accessToken}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            message: {
                token: fcmToken,
                data: { cmd },
                android: { priority: "high" },
            },
        }),
    });
    if (!res.ok) throw new Error(`invio FCM fallito: ${await res.text()}`);
    return await res.json();
}
