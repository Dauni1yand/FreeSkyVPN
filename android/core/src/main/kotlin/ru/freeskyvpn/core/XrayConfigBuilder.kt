package ru.freeskyvpn.core

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * Turns a [VlessLink] plus a [RoutingPolicy] into an Xray client config.
 *
 * Xray has no tun inbound, so the shape here is the standard one: the VPN
 * service hands the tun file descriptor to tun2socks, which forwards to the
 * SOCKS inbound below, and Xray does the routing from there.
 *
 * Two details carry most of the weight:
 *
 * **Rule order.** Xray takes the first matching rule, so direct rules have
 * to precede the catch-all that sends everything to the proxy. Reversed,
 * every rule below the catch-all would be dead and the split tunnel would
 * silently do nothing — which looks exactly like working software until
 * someone opens a banking app.
 *
 * **DNS.** Domain rules only fire on names, and a name only survives to the
 * routing stage if DNS is answered inside Xray. Resolving a Russian host
 * through a foreign resolver also gets you a foreign CDN edge, so Russian
 * domains are answered by a Russian resolver over the direct path and
 * everything else by a foreign one over the proxy.
 */
object XrayConfigBuilder {

    /** Loopback SOCKS port tun2socks forwards the tun device into. */
    const val SOCKS_PORT = 10808
    /** Xray's own DNS listener, pointed at by the tun device's DNS server. */
    const val DNS_PORT = 10853

    private const val TAG_PROXY = "proxy"
    private const val TAG_DIRECT = "direct"
    private const val TAG_BLOCK = "block"
    private const val TAG_DNS_IN = "dns-in"
    private const val TAG_DNS_OUT = "dns-out"

    // Answers Russian names. Yandex's resolver is itself inside Russia, so
    // it returns the nearby edge of a Russian CDN rather than whichever one
    // is closest to an exit node abroad.
    private const val DNS_RU = "77.88.8.8"
    private const val DNS_ABROAD = "1.1.1.1"

    /**
     * @param headHosts hostnames the app reaches the head at. Routed through
     *        the tunnel rather than around it — see [routing].
     */
    fun build(
        link: VlessLink,
        policy: RoutingPolicy,
        headHosts: List<String> = emptyList(),
        logLevel: String = "warning",
    ): JsonElement = buildJsonObject {
        putJsonObject("log") { put("loglevel", logLevel) }
        put("dns", dns(policy, headHosts))
        put("inbounds", inbounds())
        put("outbounds", outbounds(link))
        put("routing", routing(policy, headHosts))
    }

    fun buildJson(
        link: VlessLink,
        policy: RoutingPolicy,
        headHosts: List<String> = emptyList(),
        logLevel: String = "warning",
    ): String = build(link, policy, headHosts, logLevel).toString()

    private fun dns(policy: RoutingPolicy, headHosts: List<String>) = buildJsonObject {
        putJsonArray("servers") {
            // Russian names first: a matching server short-circuits, so this
            // one never has to be reached through the tunnel.
            //
            // The head's own names are excluded even when they end in .ru.
            // Resolving them domestically is exactly what a DNS-level block
            // interferes with, and the whole point of proxying them is to
            // ask somebody else.
            val headDomains = headHosts.map { "domain:${it.lowercase()}" }.toSet()
            add(buildJsonObject {
                put("address", DNS_RU)
                putJsonArray("domains") {
                    policy.directDomainRules().filterNot { it in headDomains }.forEach { add(it) }
                }
                // Without this, a name this server declines to answer falls
                // through to the foreign resolver and comes back with a
                // foreign edge — the exact outcome the split is avoiding.
                put("skipFallback", true)
            })
            add(DNS_ABROAD)
        }
        // Cuts a round trip on every lookup that is already an address.
        put("queryStrategy", "UseIP")
    }

    private fun inbounds() = buildJsonArray {
        add(buildJsonObject {
            put("tag", "socks-in")
            put("protocol", "socks")
            put("listen", "127.0.0.1")
            put("port", SOCKS_PORT)
            putJsonObject("settings") {
                put("auth", "noauth")
                // The tun carries UDP too; without this, QUIC and DNS over
                // the tunnel simply vanish.
                put("udp", true)
            }
            putJsonObject("sniffing") {
                put("enabled", true)
                // Recovers the hostname from the TLS handshake. Without it,
                // traffic that skipped Xray's DNS arrives as a bare IP and
                // every domain rule below misses it.
                putJsonArray("destOverride") { add("http"); add("tls"); add("quic") }
                put("routeOnly", false)
            }
        })
        add(buildJsonObject {
            put("tag", TAG_DNS_IN)
            put("protocol", "dokodemo-door")
            put("listen", "127.0.0.1")
            put("port", DNS_PORT)
            putJsonObject("settings") {
                put("address", DNS_ABROAD)
                put("port", 53)
                put("network", "tcp,udp")
            }
        })
    }

    private fun outbounds(link: VlessLink) = buildJsonArray {
        add(proxyOutbound(link))
        add(buildJsonObject {
            put("tag", TAG_DIRECT)
            put("protocol", "freedom")
            putJsonObject("settings") { put("domainStrategy", "UseIP") }
        })
        add(buildJsonObject {
            put("tag", TAG_BLOCK)
            put("protocol", "blackhole")
        })
        add(buildJsonObject {
            put("tag", TAG_DNS_OUT)
            put("protocol", "dns")
        })
    }

    private fun proxyOutbound(link: VlessLink) = buildJsonObject {
        put("tag", TAG_PROXY)
        put("protocol", "vless")
        putJsonObject("settings") {
            putJsonArray("vnext") {
                add(buildJsonObject {
                    put("address", link.host)
                    put("port", link.port)
                    putJsonArray("users") {
                        add(buildJsonObject {
                            put("id", link.uuid)
                            put("encryption", "none")
                            // Omitted rather than sent empty: Xray treats an
                            // empty flow as a different setting from an
                            // absent one on some transports.
                            link.flow?.let { put("flow", it) }
                        })
                    }
                })
            }
        }
        putJsonObject("streamSettings") {
            put("network", link.network)
            put("security", "reality")
            putJsonObject("realitySettings") {
                put("serverName", link.sni)
                put("fingerprint", link.fingerprint)
                put("publicKey", link.publicKey)
                put("shortId", link.shortId)
                put("spiderX", "/")
            }
            when (link.network) {
                "grpc" -> putJsonObject("grpcSettings") {
                    put("serviceName", link.serviceName.orEmpty())
                }
                "xhttp" -> putJsonObject("xhttpSettings") {
                    put("path", link.path ?: "/")
                }
            }
        }
    }

    private fun routing(policy: RoutingPolicy, headHosts: List<String>) = buildJsonObject {
        // Only resolve a domain to decide routing when no domain rule matched
        // it. "IPOnDemand" would resolve first and lose the name, which is
        // what the direct rules are keyed on.
        put("domainStrategy", "IPIfNonMatch")
        putJsonArray("rules") {
            // DNS traffic has to reach the dns outbound before anything else
            // can classify it by destination.
            add(buildJsonObject {
                put("type", "field")
                putJsonArray("inboundTag") { add(TAG_DNS_IN) }
                put("outboundTag", TAG_DNS_OUT)
            })

            // The head, before the direct rules — and that order is the
            // whole point. A control-plane domain ending in .ru would
            // otherwise match `domain:ru` and be sent around the tunnel,
            // leaving a user whose ISP blocks that name unable to reach it
            // even with the VPN up. Sending it through means the tunnel
            // they already have repairs the problem by itself.
            if (headHosts.isNotEmpty()) {
                add(buildJsonObject {
                    put("type", "field")
                    putJsonArray("domain") {
                        headHosts.forEach { add("domain:${it.lowercase()}") }
                    }
                    put("outboundTag", TAG_PROXY)
                })
            }

            val domains = policy.directDomainRules()
            if (domains.isNotEmpty()) {
                add(buildJsonObject {
                    put("type", "field")
                    putJsonArray("domain") { domains.forEach { add(it) } }
                    put("outboundTag", TAG_DIRECT)
                })
            }

            val ips = policy.directIpRules()
            if (ips.isNotEmpty()) {
                add(buildJsonObject {
                    put("type", "field")
                    putJsonArray("ip") { ips.forEach { add(it) } }
                    put("outboundTag", TAG_DIRECT)
                })
            }

            // Everything else. Last, and only last: Xray stops at the first
            // match, so anything after this rule would never run.
            add(buildJsonObject {
                put("type", "field")
                put("port", "0-65535")
                put("outboundTag", TAG_PROXY)
            })
        }
    }
}
