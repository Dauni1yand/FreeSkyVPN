package ru.freeskyvpn.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull

/**
 * The links under test are the exact shape `head/app/services/vless_link.py`
 * produces. If these two ever disagree the connection fails during the
 * handshake with nothing useful in any log, so the format is pinned here.
 */
class VlessLinkTest {

    private val realLink =
        "vless://4f1a2b3c-1111-2222-3333-444455556666@203.0.113.10:443" +
            "?type=raw&security=reality&sni=www.samsung.com&fp=chrome" +
            "&pbk=aGVsbG8td29ybGQtcHVibGljLWtleQ&sid=ab12ab12ab12ab12" +
            "&flow=xtls-rprx-vision#FreeSkyVPN"

    @Test
    fun `parses every field the head puts in`() {
        val link = VlessLink.parse(realLink)

        assertEquals("4f1a2b3c-1111-2222-3333-444455556666", link.uuid)
        assertEquals("203.0.113.10", link.host)
        assertEquals(443, link.port)
        assertEquals("raw", link.network)
        assertEquals("www.samsung.com", link.sni)
        assertEquals("aGVsbG8td29ybGQtcHVibGljLWtleQ", link.publicKey)
        assertEquals("ab12ab12ab12ab12", link.shortId)
        assertEquals("chrome", link.fingerprint)
        assertEquals("xtls-rprx-vision", link.flow)
        assertEquals("FreeSkyVPN", link.label)
    }

    @Test
    fun `tcp and raw are the same transport`() {
        // Xray renamed it; both spellings are in circulation, and the config
        // builder should not have to know that.
        assertEquals("raw", VlessLink.parse(realLink.replace("type=raw", "type=tcp")).network)
    }

    /** Rewrites the query while leaving the `#label` fragment where it is. */
    private fun withQuery(vararg replacements: Pair<String, String>): String {
        var query = realLink.substringAfter('?').substringBefore('#')
        replacements.forEach { (from, to) -> query = query.replace(from, to) }
        return realLink.substringBefore('?') + "?" + query + "#" + realLink.substringAfter('#')
    }

    @Test
    fun `grpc links carry their service name`() {
        val link = VlessLink.parse(
            withQuery(
                "type=raw" to "type=grpc",
                "&flow=xtls-rprx-vision" to "&serviceName=abc123",
            )
        )
        assertEquals("grpc", link.network)
        assertEquals("abc123", link.serviceName)
        assertNull(link.flow)
    }

    @Test
    fun `xhttp links carry their path`() {
        val link = VlessLink.parse(
            withQuery(
                "type=raw" to "type=xhttp",
                "&flow=xtls-rprx-vision" to "&path=%2Fabc123",
            )
        )
        assertEquals("xhttp", link.network)
        assertEquals("/abc123", link.path)
    }

    @Test
    fun `a query parameter is not read out of the label`() {
        // Regression on the test above, which originally appended after the
        // fragment and silently asserted nothing.
        val link = VlessLink.parse("$realLink&serviceName=notreallyhere")
        assertNull(link.serviceName)
    }

    @Test
    fun `a percent-encoded label is decoded`() {
        val link = VlessLink.parse(realLink.replace("#FreeSkyVPN", "#%D0%9D%D0%B8%D0%B4%D0%B5%D1%80%D0%BB%D0%B0%D0%BD%D0%B4%D1%8B"))
        assertEquals("Нидерланды", link.label)
    }

    @Test
    fun `a missing label falls back to the product name`() {
        assertEquals("FreeSkyVPN", VlessLink.parse(realLink.substringBefore('#')).label)
    }

    @Test
    fun `an ipv6 host loses its brackets`() {
        val link = VlessLink.parse(
            "vless://abc@[2001:db8::1]:443?security=reality&sni=a.example&pbk=k&type=raw"
        )
        assertEquals("2001:db8::1", link.host)
    }

    @Test
    fun `a link without reality is refused`() {
        // Connecting without the camouflage the design rests on would be
        // worse than not connecting.
        val e = assertFailsWith<IllegalArgumentException> {
            VlessLink.parse(realLink.replace("security=reality", "security=tls"))
        }
        assertEquals(true, e.message!!.contains("reality"))
    }

    @Test
    fun `a link without an sni is refused`() {
        assertFailsWith<IllegalArgumentException> {
            VlessLink.parse(realLink.replace("&sni=www.samsung.com", ""))
        }
    }

    @Test
    fun `a link without a public key is refused`() {
        assertFailsWith<IllegalArgumentException> {
            VlessLink.parse(realLink.replace("&pbk=aGVsbG8td29ybGQtcHVibGljLWtleQ", ""))
        }
    }

    @Test
    fun `a link without a uuid is refused`() {
        assertFailsWith<IllegalArgumentException> {
            VlessLink.parse("vless://@203.0.113.10:443?security=reality&sni=a&pbk=b")
        }
    }

    @Test
    fun `a link without a port is refused`() {
        assertFailsWith<IllegalArgumentException> {
            VlessLink.parse("vless://abc@203.0.113.10?security=reality&sni=a&pbk=b")
        }
    }

    @Test
    fun `some other scheme is refused`() {
        assertFailsWith<IllegalArgumentException> { VlessLink.parse("https://example.com") }
    }

    @Test
    fun `surrounding whitespace is tolerated`() {
        // Users paste these out of chat messages.
        assertEquals(443, VlessLink.parse("  $realLink\n").port)
    }
}
