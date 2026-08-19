package ru.freeskyvpn.data

import android.content.Context
import android.os.Build
import android.util.Log
import ru.freeskyvpn.core.Account
import ru.freeskyvpn.core.AdProgress
import ru.freeskyvpn.core.AdTicket
import ru.freeskyvpn.core.LinkCode

/**
 * Everything the UI is allowed to ask for.
 *
 * Sits between the screens and [HeadApi] so that two rules hold in one
 * place rather than being remembered at each call site: registration
 * happens exactly once and lazily, and a config that was fetched
 * successfully is cached — so "connect" works on a bad connection, which is
 * precisely when it is needed.
 */
class Repository(context: Context) {

    val storage = Storage(context.applicationContext)
    private val api = HeadApi(storage)

    /**
     * Ensures this install has an account, creating one on first use.
     *
     * Anonymous and silent: the product is one button, and a registration
     * form before the first connection buys the user nothing. The account
     * becomes recoverable when they link it to Telegram, not before — which
     * is why the account screen says so rather than hiding it.
     */
    suspend fun ensureRegistered() {
        if (storage.isRegistered) return
        val registration = api.registerDevice("${Build.MANUFACTURER} ${Build.MODEL} / Android ${Build.VERSION.RELEASE}")
        storage.token = registration.token
        storage.userId = registration.userId
    }

    /**
     * Asks the head for a config and caches it.
     *
     * The cached copy is what makes a reconnect instant and what keeps the
     * app usable when the head is briefly unreachable. It is only replaced
     * on success — a failed refresh must never leave the app with nothing.
     */
    suspend fun fetchConfig(): String {
        ensureRegistered()
        val config = api.connect()
        storage.lastVlessUrl = config.vlessUrl
        storage.lastNodeCountry = config.nodeCountry
        return config.vlessUrl
    }

    /**
     * The "не работает" button.
     *
     * The head decides what actually happened — whether this one user was
     * unlucky or the inbound is blocked for everyone on it — and returns a
     * replacement either way. The app's only job is to swap the config and
     * reconnect.
     */
    suspend fun reportFailure(): String {
        ensureRegistered()
        val outcome = api.reportFailure()
        storage.lastVlessUrl = outcome.vlessUrl
        storage.lastNodeCountry = outcome.nodeCountry
        return outcome.vlessUrl
    }

    suspend fun account(): Account {
        ensureRegistered()
        return api.account()
    }

    /** Start a run through a package's ads; the reply says what it wants. */
    suspend fun prepareAd(packageCode: String): AdTicket {
        ensureRegistered()
        return api.prepareAd(packageCode)
    }

    /** Credit one completed view. Time is granted per view, not per package. */
    suspend fun completeAd(nonce: String): AdProgress {
        ensureRegistered()
        return api.completeAd(nonce)
    }

    /**
     * Take the fallback when no ad could be delivered.
     *
     * Not an error path to hide: without it, an outage at the ad network is
     * an outage of the VPN, and a VPN that will not connect is not a
     * degraded VPN.
     */
    suspend fun accessWithoutAd(): Account {
        ensureRegistered()
        return api.adUnavailable()
    }

    suspend fun startLink(): LinkCode {
        ensureRegistered()
        return api.startLink()
    }

    /**
     * Refreshes the split-tunnel policy.
     *
     * Best effort by design: a failure here leaves the previous policy in
     * place, and on a first launch that is the one compiled into the app.
     * Never a reason to stop the user connecting.
     */
    suspend fun refreshRoutingPolicy() {
        runCatching {
            ensureRegistered()
            val policy = api.routingPolicy()
            if (policy.version >= storage.routingPolicy.version) {
                storage.routingPolicy = policy
            }
        }.onFailure { Log.i(TAG, "keeping the previous routing policy: ${it.message}") }
    }

    private companion object { const val TAG = "Repository" }
}
