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

@Serializable
data class Account(
    @SerialName("user_id") val userId: String,
    @SerialName("telegram_linked") val telegramLinked: Boolean = false,
    @SerialName("subscription_active") val subscriptionActive: Boolean = false,
    @SerialName("subscription_type") val subscriptionType: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("trial_available") val trialAvailable: Boolean = false,
    /** False while the server has no payment provider; the app hides the buy button. */
    @SerialName("payments_available") val paymentsAvailable: Boolean = false,
)

@Serializable
data class LinkCode(
    val code: String,
    @SerialName("expires_at") val expiresAt: String,
    @SerialName("bot_username") val botUsername: String? = null,
)
