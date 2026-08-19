package ru.freeskyvpn.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.assertNull

/**
 * The split tunnel is the feature most likely to break silently: a wrong
 * rule order produces a config Xray accepts and runs, which routes
 * everything through the proxy and looks entirely healthy until someone
 * opens a banking app. So the assertions here are about *order* and
 * *reachability* of rules, not only about their presence.
 */
class XrayConfigBuilderTest {

    private val link = VlessLink.parse(
        "vless://uuid-1@203.0.113.10:443?type=raw&security=reality" +
            "&sni=www.samsung.com&fp=chrome&pbk=pubkey&sid=abcd" +
            "&flow=xtls-rprx-vision#FreeSkyVPN"
    )

    private fun config(policy: RoutingPolicy = RoutingPolicy.DEFAULT): JsonObject =
        XrayConfigBuilder.build(link, policy).jsonObject

    private fun rules(policy: RoutingPolicy = RoutingPolicy.DEFAULT) =
        config(policy)["routing"]!!.jsonObject["rules"]!!.jsonArray.map { it.jsonObject }

    private fun JsonObject.outbound() = this["outboundTag"]!!.jsonPrimitive.content

    // --- rule ordering ---------------------------------------------------

    @Test
    fun `the catch-all proxy rule is last`() {
        // Xray stops at the first match. Anything after this rule is dead
        // code, and the split tunnel would quietly do nothing.
        val all = rules()
        assertEquals("proxy", all.last().outbound())
        assertEquals(1, all.count { it["port"]?.jsonPrimitive?.content == "0-65535" })
    }

    @Test
    fun `direct rules come before the catch-all`() {
        val all = rules()
        val lastDirect = all.indexOfLast { it.outbound() == "direct" }
        val catchAll = all.indexOfLast { it.outbound() == "proxy" }
        assertTrue(lastDirect in 0 until catchAll, "direct rules must precede the proxy catch-all")
    }

    @Test
    fun `dns is routed before anything can classify it by destination`() {
        assertEquals("dns-out", rules().first().outbound())
    }

    // --- what actually goes direct ---------------------------------------

    @Test
    fun `the russian tlds go direct`() {
        val domains = rules().first { it.outbound() == "direct" && it.containsKey("domain") }["domain"]!!
            .jsonArray.map { it.jsonPrimitive.content }

        assertTrue("domain:ru" in domains)
        assertTrue("domain:su" in domains)
        assertTrue("domain:рф" in domains)
    }

    @Test
    fun `russian services on foreign tlds go direct`() {
        val domains = rules().first { it.outbound() == "direct" && it.containsKey("domain") }["domain"]!!
            .jsonArray.map { it.jsonPrimitive.content }

        assertTrue("domain:vk.com" in domains)
        assertTrue("domain:2gis.com" in domains)
        assertTrue("domain:okko.tv" in domains)
    }

    @Test
    fun `the local network stays reachable while the tunnel is up`() {
        // Forgetting geoip:private is the classic way to make a VPN feel
        // broken at home: the printer and the router vanish.
        val ips = rules().first { it.outbound() == "direct" && it.containsKey("ip") }["ip"]!!
            .jsonArray.map { it.jsonPrimitive.content }

        assertTrue("geoip:private" in ips)
        assertTrue("geoip:ru" in ips)
    }

    @Test
    fun `an empty policy still produces a working proxy-everything config`() {
        // A first launch with no policy yet must connect, not fail.
        val all = rules(RoutingPolicy(version = 0))
        assertEquals("proxy", all.last().outbound())
        assertEquals(0, all.count { it.outbound() == "direct" })
    }

    // --- dns -------------------------------------------------------------

    @Test
    fun `russian names are answered by a russian resolver`() {
        val servers = config()["dns"]!!.jsonObject["servers"]!!.jsonArray
        val ruServer = servers[0].jsonObject

        assertEquals("77.88.8.8", ruServer["address"]!!.jsonPrimitive.content)
        val domains = ruServer["domains"]!!.jsonArray.map { it.jsonPrimitive.content }
        assertTrue("domain:ru" in domains)
    }

    @Test
    fun `the russian resolver does not fall through to the foreign one`() {
        // Falling through would answer a Russian name from abroad and hand
        // back a foreign CDN edge — the exact outcome the split avoids.
        val ruServer = config()["dns"]!!.jsonObject["servers"]!!.jsonArray[0].jsonObject
        assertEquals(true, ruServer["skipFallback"]!!.jsonPrimitive.content.toBoolean())
    }

    // --- the outbound ----------------------------------------------------

    @Test
    fun `the proxy outbound reproduces the link`() {
        val proxy = config()["outbounds"]!!.jsonArray
            .map { it.jsonObject }
            .first { it["tag"]!!.jsonPrimitive.content == "proxy" }

        val vnext = proxy["settings"]!!.jsonObject["vnext"]!!.jsonArray[0].jsonObject
        assertEquals("203.0.113.10", vnext["address"]!!.jsonPrimitive.content)
        assertEquals(443, vnext["port"]!!.jsonPrimitive.content.toInt())

        val user = vnext["users"]!!.jsonArray[0].jsonObject
        assertEquals("uuid-1", user["id"]!!.jsonPrimitive.content)
        assertEquals("xtls-rprx-vision", user["flow"]!!.jsonPrimitive.content)

        val reality = proxy["streamSettings"]!!.jsonObject["realitySettings"]!!.jsonObject
        assertEquals("www.samsung.com", reality["serverName"]!!.jsonPrimitive.content)
        assertEquals("pubkey", reality["publicKey"]!!.jsonPrimitive.content)
        assertEquals("abcd", reality["shortId"]!!.jsonPrimitive.content)
    }

    @Test
    fun `a transport without a flow omits the field entirely`() {
        // Xray treats an absent flow differently from an empty one on some
        // transports, so this must not become flow="".
        val grpcLink = VlessLink.parse(
            "vless://uuid-1@203.0.113.10:443?type=grpc&security=reality" +
                "&sni=a.example&pbk=k&sid=ab&serviceName=svc"
        )
        val proxy = XrayConfigBuilder.build(grpcLink, RoutingPolicy.DEFAULT).jsonObject["outbounds"]!!
            .jsonArray.map { it.jsonObject }.first { it["tag"]!!.jsonPrimitive.content == "proxy" }
        val user = proxy["settings"]!!.jsonObject["vnext"]!!.jsonArray[0].jsonObject["users"]!!
            .jsonArray[0].jsonObject

        assertNull(user["flow"])
    }

    @Test
    fun `grpc carries its service name into stream settings`() {
        val grpcLink = VlessLink.parse(
            "vless://u@h.example:443?type=grpc&security=reality&sni=a.example&pbk=k&serviceName=svc"
        )
        val stream = XrayConfigBuilder.build(grpcLink, RoutingPolicy.DEFAULT).jsonObject["outbounds"]!!
            .jsonArray.map { it.jsonObject }.first { it["tag"]!!.jsonPrimitive.content == "proxy" }["streamSettings"]!!.jsonObject

        assertEquals("svc", stream["grpcSettings"]!!.jsonObject["serviceName"]!!.jsonPrimitive.content)
    }

    // --- inbounds --------------------------------------------------------

    @Test
    fun `the socks inbound accepts udp`() {
        // tun2socks forwards UDP too; without this, QUIC and DNS over the
        // tunnel simply disappear.
        val socks = config()["inbounds"]!!.jsonArray.map { it.jsonObject }
            .first { it["tag"]!!.jsonPrimitive.content == "socks-in" }

        assertEquals(true, socks["settings"]!!.jsonObject["udp"]!!.jsonPrimitive.content.toBoolean())
        assertEquals(XrayConfigBuilder.SOCKS_PORT, socks["port"]!!.jsonPrimitive.content.toInt())
    }

    @Test
    fun `sniffing is on so ip-addressed traffic can still match domain rules`() {
        val socks = config()["inbounds"]!!.jsonArray.map { it.jsonObject }
            .first { it["tag"]!!.jsonPrimitive.content == "socks-in" }
        val sniffing = socks["sniffing"]!!.jsonObject

        assertEquals(true, sniffing["enabled"]!!.jsonPrimitive.content.toBoolean())
        val overrides = sniffing["destOverride"]!!.jsonArray.map { it.jsonPrimitive.content }
        assertTrue("tls" in overrides)
    }

    @Test
    fun `routing resolves names only when no domain rule matched`() {
        // IPOnDemand would resolve first and discard the name the direct
        // rules are keyed on.
        assertEquals(
            "IPIfNonMatch",
            config()["routing"]!!.jsonObject["domainStrategy"]!!.jsonPrimitive.content,
        )
    }

    @Test
    fun `the config is valid json`() {
        val text = XrayConfigBuilder.buildJson(link, RoutingPolicy.DEFAULT)
        assertTrue(text.startsWith("{") && text.endsWith("}"))
        assertTrue(text.length > 500)
    }

    // --- the head's own address ---------------------------------------------

    private val headHosts = listOf("api.example.ru", "backup.example.com")

    private fun rulesWithHead() = XrayConfigBuilder
        .build(link, RoutingPolicy.DEFAULT, headHosts)
        .jsonObject["routing"]!!.jsonObject["rules"]!!.jsonArray.map { it.jsonObject }

    @Test
    fun `the head goes through the tunnel, not around it`() {
        val rule = rulesWithHead().first { it.outbound() == "proxy" && it.containsKey("domain") }
        val domains = rule["domain"]!!.jsonArray.map { it.jsonPrimitive.content }

        assertTrue("domain:api.example.ru" in domains)
        assertTrue("domain:backup.example.com" in domains)
    }

    @Test
    fun `a ru head domain is proxied despite the direct ru rule`() {
        // The one that matters. Without this ordering, api.example.ru matches
        // `domain:ru`, goes direct, and a user whose ISP blocks that name
        // cannot reach the head even with the VPN up — so the tunnel they
        // already have cannot repair the problem.
        val all = rulesWithHead()
        val headRule = all.indexOfFirst { it.outbound() == "proxy" && it.containsKey("domain") }
        val directRule = all.indexOfFirst { it.outbound() == "direct" && it.containsKey("domain") }

        assertTrue(headRule in 0 until directRule, "the head rule must precede the direct rules")
    }

    @Test
    fun `the head is not resolved by the domestic dns server`() {
        // Resolving it domestically is exactly what a DNS-level block
        // interferes with; the point of proxying it is to ask someone else.
        val ruServer = XrayConfigBuilder
            .build(link, RoutingPolicy(version = 1, directTlds = listOf("ru"), directGeoip = listOf("ru")), headHosts)
            .jsonObject["dns"]!!.jsonObject["servers"]!!.jsonArray[0].jsonObject

        val domains = ruServer["domains"]!!.jsonArray.map { it.jsonPrimitive.content }
        assertTrue("domain:api.example.ru" !in domains)
    }

    @Test
    fun `no head hosts means no extra rule`() {
        val all = rules()
        assertEquals(0, all.count { it.outbound() == "proxy" && it.containsKey("domain") })
    }

    @Test
    fun `the catch-all is still last with head hosts present`() {
        assertEquals("proxy", rulesWithHead().last().outbound())
        assertEquals(1, rulesWithHead().count { it["port"]?.jsonPrimitive?.content == "0-65535" })
    }
}
