package ru.freeskyvpn.core

import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class RoutingPolicyTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `the built-in default is a usable policy on its own`() {
        // It is what the app routes with before its first successful fetch
        // and whenever the head is unreachable, so it cannot be a stub.
        val p = RoutingPolicy.DEFAULT
        assertTrue("ru" in p.directTlds)
        assertTrue(p.directDomains.isNotEmpty())
        assertTrue(p.directPackages.isNotEmpty())
        assertTrue("private" in p.directGeoip)
    }

    @Test
    fun `a policy from the head parses`() {
        val body = """
            {"version": 7,
             "direct_tlds": ["ru"],
             "direct_domains": ["vk.com"],
             "direct_packages": ["ru.sberbankmobile"],
             "direct_geoip": ["ru", "private"]}
        """.trimIndent()

        val p = json.decodeFromString<RoutingPolicy>(body)

        assertEquals(7, p.version)
        assertEquals(listOf("vk.com"), p.directDomains)
    }

    @Test
    fun `an unknown field from a newer head does not break an older app`() {
        // A client that breaks on new server fields can never be updated
        // independently of the server.
        val body = """{"version": 8, "direct_tlds": ["ru"], "something_new": [1,2,3]}"""
        assertEquals(8, json.decodeFromString<RoutingPolicy>(body).version)
    }

    @Test
    fun `domain rules are lowercased and prefixed`() {
        val p = RoutingPolicy(version = 1, directTlds = listOf("RU"), directDomains = listOf(" VK.com "))
        assertEquals(listOf("domain:ru", "domain:vk.com"), p.directDomainRules())
    }

    @Test
    fun `geoip rules are prefixed`() {
        val p = RoutingPolicy(version = 1, directGeoip = listOf("RU", "private"))
        assertEquals(listOf("geoip:ru", "geoip:private"), p.directIpRules())
    }
}
