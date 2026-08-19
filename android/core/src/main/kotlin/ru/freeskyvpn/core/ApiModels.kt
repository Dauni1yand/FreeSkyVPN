package ru.freeskyvpn.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire models for the head's `/api/v1/me` endpoints.
 *
 * `ignoreUnknownKeys` is set on the Json instance rather than here, so the
 * head can add a field without every older install failing to parse the
 * response — a client that breaks on new server fields is a client that can
 * never be updated independently of the server.
 */

@Serializable
data class DeviceRegistration(
    @SerialName("user_id") val userId: String,
    val token: String,
    @SerialName("session_id") val sessionId: String,
)

@Serializable
data class ConnectionConfig(
    @SerialName("vless_url") val vlessUrl: String,
    @SerialName("node_country") val nodeCountry: String,
    @SerialName("inbound_id") val inboundId: String,
)

@Serializable
data class FailureOutcome(
    @SerialName("vless_url") val vlessUrl: String,
    @SerialName("node_country") val nodeCountry: String,
    @SerialName("inbound_id") val inboundId: String,
    /** True when the server decided the old inbound was blocked for everyone. */
    @SerialName("inbound_declared_dead") val inboundDeclaredDead: Boolean = false,
    @SerialName("node_declared_burned") val nodeDeclaredBurned: Boolean = false,
    @SerialName("users_migrated") val usersMigrated: Int = 0,
)

/**
 * The account, which under this model is mostly "how much time is left".
 *
 * The service is funded entirely by advertising: one completed rewarded
 * video buys one hour. There is no subscription and no free tier, so
 * [accessSecondsRemaining] is what the connect button is gated on.
 */
@Serializable
data class Account(
    @SerialName("user_id") val userId: String,
    @SerialName("telegram_linked") val telegramLinked: Boolean = false,
    @SerialName("access_active") val accessActive: Boolean = false,
    @SerialName("access_expires_at") val accessExpiresAt: String? = null,
    @SerialName("access_seconds_remaining") val accessSecondsRemaining: Int = 0,
    /**
     * True when the current stretch was handed out because no ad could be
     * delivered. Worth surfacing: that traffic sits in the lower-priority
     * class, so the user has a real reason for it feeling slower.
     */
    @SerialName("access_is_grace") val accessIsGrace: Boolean = false,
    /**
     * What the user can buy at the connect button.
     *
     * Served by the head rather than compiled in, so the offer can change
     * without a store release — and so the client can never be the thing
     * that decides what an ad is worth.
     */
    val packages: List<AccessPackage> = emptyList(),
)

/** One option in the duration picker. */
@Serializable
data class AccessPackage(
    val code: String,
    val label: String,
    /**
     * `rewarded` must be watched through and has a completion signal;
     * `interstitial` is skippable and has none, anywhere — which is why it
     * buys the least time.
     */
    @SerialName("ad_kind") val adKind: String,
    val views: Int,
    @SerialName("total_minutes") val totalMinutes: Int,
) {
    val isSkippable: Boolean get() = adKind == "interstitial"
}

/** The token covering one run through a package's ads. */
@Serializable
data class AdTicket(
    val nonce: String,
    @SerialName("package") val packageCode: String = "",
    @SerialName("ad_kind") val adKind: String = "rewarded",
    @SerialName("views_required") val viewsRequired: Int = 1,
    @SerialName("minutes_per_view") val minutesPerView: Int = 60,
)

/**
 * What one completed view bought.
 *
 * Time is credited per view rather than when the package finishes, so a
 * user who watches the first of two ads and closes the app keeps the hour
 * they earned. [complete] is false while another video is still owed.
 */
@Serializable
data class AdProgress(
    @SerialName("views_done") val viewsDone: Int,
    @SerialName("views_required") val viewsRequired: Int,
    @SerialName("minutes_granted") val minutesGranted: Int,
    val complete: Boolean,
    val account: Account,
)

@Serializable
data class LinkCode(
    val code: String,
    @SerialName("expires_at") val expiresAt: String,
    @SerialName("bot_username") val botUsername: String? = null,
)
