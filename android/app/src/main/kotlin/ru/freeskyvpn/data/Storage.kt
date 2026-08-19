package ru.freeskyvpn.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.serialization.json.Json
import ru.freeskyvpn.core.RoutingPolicy

/**
 * Everything the app remembers between launches.
 *
 * The bearer token lives in [EncryptedSharedPreferences] because it is the
 * account: the head hands it over exactly once at registration and can never
 * reproduce it, so losing it means losing the account, and leaking it means
 * handing the account over. Everything else here is cache and preference and
 * lives in ordinary preferences — encrypting a list of package names would
 * buy nothing and cost a keystore round trip on every read.
 */
class Storage(context: Context) {

    private val json = Json { ignoreUnknownKeys = true }

    private val secure: SharedPreferences = run {
        val key = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "freesky_secure",
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    private val plain: SharedPreferences =
        context.getSharedPreferences("freesky", Context.MODE_PRIVATE)

    // --- account ---------------------------------------------------------

    var token: String?
        get() = secure.getString(KEY_TOKEN, null)
        set(value) = secure.edit().putString(KEY_TOKEN, value).apply()

    var userId: String?
        get() = secure.getString(KEY_USER_ID, null)
        set(value) = secure.edit().putString(KEY_USER_ID, value).apply()

    val isRegistered: Boolean get() = !token.isNullOrBlank()

    // --- last known config ----------------------------------------------

    /**
     * The last config the head handed out.
     *
     * Cached so the app can reconnect without a round trip — which matters
     * precisely when it matters most: the head is in Russia, the user's
     * connection is unreliable, and "connect" should not depend on an API
     * call succeeding first.
     */
    var lastVlessUrl: String?
        get() = plain.getString(KEY_VLESS, null)
        set(value) = plain.edit().putString(KEY_VLESS, value).apply()

    var lastNodeCountry: String?
        get() = plain.getString(KEY_COUNTRY, null)
        set(value) = plain.edit().putString(KEY_COUNTRY, value).apply()

    // --- where the head lives --------------------------------------------

    /**
     * The address that most recently answered.
     *
     * Remembered so a client that already found a live host does not
     * re-probe a dead primary at every launch and pay a connect timeout
     * for it. Not encrypted: it is one of the addresses printed in the
     * build, not a secret.
     */
    var lastGoodApiUrl: String?
        get() = plain.getString(KEY_LAST_GOOD_API, null)
        set(value) = plain.edit().putString(KEY_LAST_GOOD_API, value).apply()

    /**
     * A debug-only address typed by a tester.
     *
     * Read only when BuildConfig.DEBUG, so a release build cannot be
     * pointed anywhere by anything.
     */
    var apiOverride: String?
        get() = plain.getString(KEY_API_OVERRIDE, null)
        set(value) = plain.edit().putString(KEY_API_OVERRIDE, value).apply()

    // --- split tunnel ----------------------------------------------------

    var routingPolicy: RoutingPolicy
        get() = plain.getString(KEY_POLICY, null)
            ?.let { runCatching { json.decodeFromString<RoutingPolicy>(it) }.getOrNull() }
            ?: RoutingPolicy.DEFAULT
        set(value) = plain.edit().putString(KEY_POLICY, json.encodeToString(value)).apply()

    /** Whether the Russian-services bypass is on at all. On by default. */
    var splitTunnelEnabled: Boolean
        get() = plain.getBoolean(KEY_SPLIT_ON, true)
        set(value) = plain.edit().putBoolean(KEY_SPLIT_ON, value).apply()

    /** Apps the user added to the bypass beyond what the policy proposes. */
    var userExcluded: Set<String>
        get() = plain.getStringSet(KEY_USER_EXCLUDED, emptySet()).orEmpty()
        set(value) = plain.edit().putStringSet(KEY_USER_EXCLUDED, value).apply()

    /** Apps the user pulled back into the tunnel despite the policy. */
    var userIncluded: Set<String>
        get() = plain.getStringSet(KEY_USER_INCLUDED, emptySet()).orEmpty()
        set(value) = plain.edit().putStringSet(KEY_USER_INCLUDED, value).apply()

    private companion object {
        const val KEY_TOKEN = "token"
        const val KEY_USER_ID = "user_id"
        const val KEY_VLESS = "vless_url"
        const val KEY_COUNTRY = "node_country"
        const val KEY_LAST_GOOD_API = "last_good_api"
        const val KEY_API_OVERRIDE = "api_override"
        const val KEY_POLICY = "routing_policy"
        const val KEY_SPLIT_ON = "split_tunnel_enabled"
        const val KEY_USER_EXCLUDED = "user_excluded"
        const val KEY_USER_INCLUDED = "user_included"
    }
}
