package ru.freeskyvpn.core

import java.net.URI
import java.net.URLDecoder

/**
 * One `vless://` link, taken apart.
 *
 * The head builds these in `head/app/services/vless_link.py` and the node
 * serves the matching inbound; if this parser and that builder ever disagree
 * about a field, the handshake fails with nothing in the logs worth reading.
 * So this is deliberately strict: an unparseable link is an error, never a
 * half-populated object that fails later at connect time when the cause is
 * much harder to see.
 *
 * Format, as produced by the head:
 *
 *     vless://<uuid>@<host>:<port>?type=..&security=reality&sni=..&fp=..
 *             &pbk=..&sid=..[&flow=..][&serviceName=..][&path=..]#<label>
 */
data class VlessLink(
    val uuid: String,
    val host: String,
    val port: Int,
    /** Xray `streamSettings.network`: raw/tcp, grpc or xhttp. */
    val network: String,
    val sni: String,
    val publicKey: String,
    val shortId: String,
    val fingerprint: String,
    /** `xtls-rprx-vision`, or null when the transport carries no flow. */
    val flow: String?,
    val serviceName: String?,
    val path: String?,
    val label: String,
) {
    companion object {
        private const val SCHEME = "vless"

        fun parse(raw: String): VlessLink {
            val trimmed = raw.trim()
            require(trimmed.startsWith("$SCHEME://")) { "not a vless:// link" }

            // URI handles the userinfo/host/port/query split; doing it by
            // hand is where IPv6 hosts and percent-encoded labels go wrong.
            val uri = try {
                URI(trimmed)
            } catch (e: Exception) {
                throw IllegalArgumentException("malformed vless:// link: ${e.message}", e)
            }

            val uuid = uri.userInfo?.takeIf { it.isNotBlank() }
                ?: throw IllegalArgumentException("link carries no client uuid")
            val host = uri.host?.trim('[', ']')?.takeIf { it.isNotBlank() }
                ?: throw IllegalArgumentException("link carries no host")
            val port = uri.port.takeIf { it in 1..65535 }
                ?: throw IllegalArgumentException("link carries no usable port")

            val q = parseQuery(uri.rawQuery)

            // Reality is the only security this service issues. Accepting
            // anything else would mean silently connecting without the
            // camouflage the whole design rests on.
            val security = q["security"] ?: "none"
            require(security == "reality") { "unsupported security '$security'; expected reality" }

            return VlessLink(
                uuid = uuid,
                host = host,
                port = port,
                network = normaliseNetwork(q["type"] ?: "tcp"),
                sni = q["sni"] ?: throw IllegalArgumentException("reality link without sni"),
                publicKey = q["pbk"] ?: throw IllegalArgumentException("reality link without pbk"),
                shortId = q["sid"].orEmpty(),
                fingerprint = q["fp"] ?: "chrome",
                flow = q["flow"]?.takeIf { it.isNotBlank() },
                serviceName = q["serviceName"]?.takeIf { it.isNotBlank() },
                path = q["path"]?.takeIf { it.isNotBlank() },
                label = uri.fragment?.let(::decode).orEmpty().ifBlank { "FreeSkyVPN" },
            )
        }

        /**
         * Xray renamed the plain TCP transport to `raw`, and both spellings
         * are in circulation — the head emits whichever its transport table
         * says. Normalising here means the config builder has one case to
         * handle instead of two.
         */
        private fun normaliseNetwork(value: String): String =
            when (value.lowercase()) {
                "tcp", "raw" -> "raw"
                else -> value.lowercase()
            }

        private fun parseQuery(rawQuery: String?): Map<String, String> {
            if (rawQuery.isNullOrBlank()) return emptyMap()
            return rawQuery.split('&')
                .mapNotNull { pair ->
                    val idx = pair.indexOf('=')
                    if (idx <= 0) null
                    else decode(pair.substring(0, idx)) to decode(pair.substring(idx + 1))
                }
                .toMap()
        }

        private fun decode(value: String): String =
            try {
                URLDecoder.decode(value, Charsets.UTF_8.name())
            } catch (_: Exception) {
                value
            }
    }
}
