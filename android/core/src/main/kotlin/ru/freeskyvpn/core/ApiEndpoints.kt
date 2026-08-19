package ru.freeskyvpn.core

/**
 * Which addresses to try for the head, and in what order.
 *
 * The app is useless without the head: no config, no ad tokens, no account.
 * So a single compiled-in address is a single point of failure, and for a
 * VPN aimed at Russia it is a *likely* one. Losing the server's IP is
 * survivable — the A record moves and nothing on the device notices. Losing
 * the *domain* is not: every installed copy stops working until an update
 * ships, which for a Play release is days at best.
 *
 * So the build carries a list rather than one name, and the client walks it
 * until something answers. Backup entries can be domains bought later and
 * pointed at the same server; they cost nothing until the day they matter.
 *
 * Ordering is the whole job here, and there are two rules:
 *
 *  * whatever worked last time goes first, so a client that already found a
 *    live address does not re-probe a dead primary on every launch and pay
 *    a connect timeout for it;
 *  * a debug override beats everything, so a tester can point the app at a
 *    laptop without rebuilding.
 */
object ApiEndpoints {

    /**
     * The order to try, most likely to work first.
     *
     * @param configured what the build was given, in preference order
     * @param override a debug-only address typed by a tester
     * @param lastGood the address that answered most recently
     */
    fun resolve(
        configured: List<String>,
        override: String? = null,
        lastGood: String? = null,
    ): List<String> {
        val normalisedOverride = normalise(override)
        if (normalisedOverride != null) {
            // Deliberately exclusive: someone who typed an address wants
            // that address, and silently falling back to production would
            // make a typo look like a working build.
            return listOf(normalisedOverride)
        }

        val ordered = LinkedHashSet<String>()
        normalise(lastGood)?.let(ordered::add)
        configured.mapNotNull(::normalise).forEach(ordered::add)
        return ordered.toList()
    }

    /**
     * Trims and validates one address, or null if it is unusable.
     *
     * Cleartext is refused outright. Android blocks it by default anyway,
     * but the reason is worth being explicit about: this connection carries
     * the per-user bearer token, and a token on a plain connection is a
     * token anyone on the same network has.
     */
    fun normalise(raw: String?): String? {
        val trimmed = raw?.trim()?.trimEnd('/') ?: return null
        if (trimmed.isEmpty()) return null
        return when {
            trimmed.startsWith("https://") -> trimmed
            // Only ever reachable from a debug build, and only to a private
            // address — see the network security config. Kept here so the
            // resolution logic stays the single place that decides what a
            // usable address is.
            trimmed.startsWith("http://") && isPrivateHost(trimmed) -> trimmed
            // A bare host is the common way to mistype one of these.
            !trimmed.contains("://") -> "https://$trimmed"
            else -> null
        }
    }

    /** Parses the comma-separated list a build is configured with. */
    fun parse(configured: String): List<String> =
        configured.split(',').mapNotNull(::normalise)

    private fun isPrivateHost(url: String): Boolean {
        val host = url.removePrefix("http://").substringBefore('/').substringBefore(':')
        if (host == "localhost" || host == "127.0.0.1" || host == "10.0.2.2") return true

        val octets = host.split('.').mapNotNull { it.toIntOrNull() }
        if (octets.size != 4) return false
        return when {
            octets[0] == 10 -> true
            octets[0] == 192 && octets[1] == 168 -> true
            octets[0] == 172 && octets[1] in 16..31 -> true
            else -> false
        }
    }
}
