package com.hubpc.client

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.SecureRandom
import java.util.Date
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.security.auth.x500.X500Principal

/** Certificato TLS auto-firmato per AUTH TLS (FTPS esplicito) sul server FTP
 * del telefono — controparte di ftp_tls.py sul PC, stesso principio: nessuna
 * autorita' di certificazione, serve solo a cifrare contro chi origlia sulla
 * stessa LAN, non ad autenticare un'identita' verificabile da terzi.
 *
 * La chiave vive in AndroidKeyStore invece che su file: e' l'unico modo di
 * avere un certificato auto-firmato senza aggiungere una libreria come
 * Bouncy Castle solo per questo, e AndroidKeyStore genera gia' da solo un
 * certificato X.509 minimale insieme alla coppia di chiavi quando gli si
 * chiede un "certificate subject". */
object FtpTls {
    private const val ALIAS = "connexus_ftp_tls"

    private fun ensureKeyExists() {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (keyStore.containsAlias(ALIAS)) return

        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_RSA, "AndroidKeyStore")
        val notBefore = Date(System.currentTimeMillis() - 24L * 60 * 60 * 1000)
        val notAfter = Date(notBefore.time + 10L * 365 * 24 * 60 * 60 * 1000)
        val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setSignaturePaddings(KeyProperties.SIGNATURE_PADDING_RSA_PKCS1)
            .setKeySize(2048)
            .setCertificateSubject(X500Principal("CN=Connexus Phone FTP"))
            .setCertificateSerialNumber(BigInteger.ONE)
            .setCertificateNotBefore(notBefore)
            .setCertificateNotAfter(notAfter)
            .build()
        generator.initialize(spec)
        generator.generateKeyPair()
    }

    /** SSLContext pronto per AUTH TLS, o null se la generazione/chiave fallisce:
     * la condivisione deve continuare a funzionare in chiaro anche senza. */
    fun getServerContext(): SSLContext? {
        return try {
            ensureKeyExists()
            val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
            kmf.init(keyStore, null)
            val context = SSLContext.getInstance("TLS")
            context.init(kmf.keyManagers, null, SecureRandom())
            context
        } catch (e: Exception) {
            null
        }
    }
}
