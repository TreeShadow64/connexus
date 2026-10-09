package com.hubpc.client

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertNotNull
import org.junit.Test

class PairingQrTest {

    private val token = "0123456789abcdef0123456789abcdef"

    @Test
    fun codiceDelPcValido() {
        val p = PairingQr.parse("connexus://pair?ip=192.168.1.8&token=$token&name=BarbaPc")
        assertNotNull(p)
        assertEquals("192.168.1.8", p!!.ip)
        assertEquals(token, p.token)
        assertEquals("BarbaPc", p.name)
    }

    @Test
    fun nomeConSpaziEAccentiCodificati() {
        val p = PairingQr.parse("connexus://pair?ip=192.168.1.8&token=$token&name=PC%20di%20Dar%C3%ADo")
        assertEquals("PC di Darío", p!!.name)
    }

    @Test
    fun nomeConCaratteriPericolosiVieneRipulito() {
        val p = PairingQr.parse("connexus://pair?ip=192.168.1.8&token=$token&name=%3Cscript%3E%22x%22")
        assertEquals("_script__x_", p!!.name)
    }

    @Test
    fun nomeMancanteDiventaPc() {
        assertEquals("PC", PairingQr.parse("connexus://pair?ip=10.0.0.5&token=$token")!!.name)
    }

    @Test
    fun altriCodiciVengonoRifiutati() {
        assertNull(PairingQr.parse("https://esempio.it/pair?ip=192.168.1.8&token=$token"))
        assertNull(PairingQr.parse("connexus://altro?ip=192.168.1.8&token=$token"))
        assertNull(PairingQr.parse("testo qualunque"))
        assertNull(PairingQr.parse(""))
        assertNull(PairingQr.parse(null))
    }

    @Test
    fun ipNonValidoVieneRifiutato() {
        assertNull(PairingQr.parse("connexus://pair?ip=192.168.1.8/../x&token=$token"))
        assertNull(PairingQr.parse("connexus://pair?ip=&token=$token"))
        assertNull(PairingQr.parse("connexus://pair?ip=a b&token=$token"))
    }

    @Test
    fun tokenNonValidoVieneRifiutato() {
        assertNull(PairingQr.parse("connexus://pair?ip=192.168.1.8&token=corto"))
        assertNull(PairingQr.parse("connexus://pair?ip=192.168.1.8&token=${token}%3B%20rm"))
        assertNull(PairingQr.parse("connexus://pair?ip=192.168.1.8"))
    }
}
