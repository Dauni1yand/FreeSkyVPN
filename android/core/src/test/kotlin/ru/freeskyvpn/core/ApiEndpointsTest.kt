package ru.freeskyvpn.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ApiEndpointsTest {

    private val configured = listOf("https://api.example.ru", "https://backup.example.com")

    // --- ordering ------------------------------------------------------------

    @Test
    fun `configured order is kept when nothing is known yet`() {
        assertEquals(configured, ApiEndpoints.resolve(configured))
    }

    @Test
    fun `whatever answered last time is tried first`() {
        // Otherwise a client that already found a live address pays a
        // connect timeout on the dead primary at every launch.
        assertEquals(
            listOf("https://backup.example.com", "https://api.example.ru"),
            ApiEndpoints.resolve(configured, lastGood = "https://backup.example.com"),
        )
    }

    @Test
    fun `the last good address is not tried twice`() {
        val resolved = ApiEndpoints.resolve(configured, lastGood = "https://api.example.ru")
        assertEquals(configured.size, resolved.size)
        assertEquals("https://api.example.ru", resolved.first())
    }

    @Test
    fun `a last good address no longer in the build is still tried`() {
        // The build shipped a new list; the old address may well still work
        // and is the one this device knows about.
        val resolved = ApiEndpoints.resolve(configured, lastGood = "https://old.example.net")
        assertEquals("https://old.example.net", resolved.first())
        assertEquals(3, resolved.size)
    }

    @Test
    fun `duplicates in the configured list collapse`() {
        val resolved = ApiEndpoints.resolve(
            listOf("https://a.example", "https://a.example/", " https://a.example ")
        )
        assertEquals(listOf("https://a.example"), resolved)
    }

    // --- the debug override --------------------------------------------------

    @Test
    fun `an override wins outright`() {
        assertEquals(
            listOf("https://staging.example.ru"),
            ApiEndpoints.resolve(configured, override = "https://staging.example.ru"),
        )
    }

    @Test
    fun `an override does not silently fall back to production`() {
        // A typo that quietly reached the real server would look like a
        // working build and produce results from the wrong machine.
        val resolved = ApiEndpoints.resolve(configured, override = "https://typo.example")
        assertEquals(1, resolved.size)
    }

    @Test
    fun `a blank override is ignored`() {
        assertEquals(configured, ApiEndpoints.resolve(configured, override = "   "))
    }

    // --- what counts as usable ----------------------------------------------

    @Test
    fun `a bare host becomes https`() {
        assertEquals("https://api.example.ru", ApiEndpoints.normalise("api.example.ru"))
    }

    @Test
    fun `cleartext to the internet is refused`() {
        // This connection carries the per-user bearer token, and a token on
        // a plain connection is a token anyone on the network has.
        assertNull(ApiEndpoints.normalise("http://api.example.ru"))
    }

    @Test
    fun `cleartext to a private address is allowed for testing`() {
        assertEquals("http://192.168.1.5:8000", ApiEndpoints.normalise("http://192.168.1.5:8000"))
        assertEquals("http://10.0.2.2:8000", ApiEndpoints.normalise("http://10.0.2.2:8000"))
        assertEquals("http://localhost:8000", ApiEndpoints.normalise("http://localhost:8000"))
    }

    @Test
    fun `a public address dressed as private is still refused`() {
        assertNull(ApiEndpoints.normalise("http://172.32.0.1:8000"))
        assertNull(ApiEndpoints.normalise("http://11.0.0.1:8000"))
    }

    @Test
    fun `trailing slashes and spaces are tolerated`() {
        assertEquals("https://api.example.ru", ApiEndpoints.normalise("  https://api.example.ru/  "))
    }

    @Test
    fun `nonsense is refused rather than half accepted`() {
        assertNull(ApiEndpoints.normalise(null))
        assertNull(ApiEndpoints.normalise(""))
        assertNull(ApiEndpoints.normalise("ftp://api.example.ru"))
    }

    // --- parsing the build's list -------------------------------------------

    @Test
    fun `a comma separated list is parsed in order`() {
        assertEquals(
            listOf("https://a.example", "https://b.example"),
            ApiEndpoints.parse("a.example, https://b.example/"),
        )
    }

    @Test
    fun `an empty configuration parses to nothing rather than to garbage`() {
        assertTrue(ApiEndpoints.parse("").isEmpty())
        assertTrue(ApiEndpoints.parse("  ,  ").isEmpty())
    }

    // --- hostnames for the routing rules ------------------------------------

    @Test
    fun `hostnames are extracted without scheme or port`() {
        assertEquals("api.example.ru", ApiEndpoints.hostOf("https://api.example.ru/"))
        assertEquals("192.168.1.5", ApiEndpoints.hostOf("http://192.168.1.5:8000"))
        assertEquals("api.example.ru", ApiEndpoints.hostOf("api.example.ru"))
    }

    @Test
    fun `every configured host is offered for proxying`() {
        assertEquals(
            listOf("api.example.ru", "backup.example.com"),
            ApiEndpoints.proxiedHosts(configured),
        )
    }

    @Test
    fun `duplicate hosts collapse`() {
        val hosts = ApiEndpoints.proxiedHosts(
            listOf("https://api.example.ru", "https://api.example.ru/v2")
        )
        assertEquals(listOf("api.example.ru"), hosts)
    }

    @Test
    fun `unusable entries do not become empty rules`() {
        assertTrue(ApiEndpoints.proxiedHosts(listOf("", "ftp://x")).isEmpty())
    }
}
