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
    /** What the next completed ad will buy. */
    @SerialName("ad_reward_minutes") val adRewardMinutes: Int = 60,
)

/** The single-use token returned once a rewarded ad finishes. */
@Serializable
data class AdTicket(
    val nonce: String,
    @SerialName("reward_minutes") val rewardMinutes: Int = 60,
)

@Serializable
data class LinkCode(
    val code: String,
    @SerialName("expires_at") val expiresAt: String,
    @SerialName("bot_username") val botUsername: String? = null,
)
