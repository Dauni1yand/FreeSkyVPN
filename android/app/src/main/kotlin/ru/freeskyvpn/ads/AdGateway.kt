package ru.freeskyvpn.ads

import android.app.Activity

/**
 * The rewarded video that pays for the servers.
 *
 * The service has no subscription and no free tier: one completed ad buys
 * one hour of VPN. That makes this interface the revenue side of the whole
 * product, and it has to be honest about the case that decides whether the
 * product works at all — the ad that will not load.
 *
 * Fill rates are not 100%, and an ad network can be down or unreachable.
 * If the app treats "no ad" as "no service", then their outage is our
 * outage, total rather than partial. So [Outcome.Unavailable] is a normal
 * result, and the caller answers it by asking the head for a short grace
 * period on the lower-priority class — worse than the real thing, so it
 * cannot become the way to skip the ad, but not nothing.
 *
 * A note on which network. Google paused ad serving to users in Russia in
 * March 2022, so AdMob does not monetise this audience at all; Yandex and
 * VK are what remain. That is a business fact rather than a code one, but
 * it decides which SDK lands behind this interface.
 */
interface AdGateway {

    sealed interface Outcome {
        /** The user watched it through. The reward is owed. */
        data object Rewarded : Outcome

        /** They closed it early. No reward, and no complaint either. */
        data object Skipped : Outcome

        /**
         * No ad could be shown — no fill, no network, SDK not initialised.
         * The caller falls back to grace access rather than leaving the
         * user unable to connect at all.
         */
        data class Unavailable(val reason: String) : Outcome
    }

    /**
     * Which format to show.
     *
     * They are not interchangeable. A rewarded ad has a completion signal —
     * and, once server-side verification is configured, a callback from the
     * network — so the head can eventually be sure it was watched. An
     * interstitial is skippable and has neither, which is why the package
     * it pays for buys the least time.
     */
    enum class Kind { Rewarded, Interstitial }

    /** Whether an ad of this kind is loaded. False means [show] will likely fail. */
    fun isReady(kind: Kind): Boolean

    /**
     * Show one ad and wait for it to finish.
     *
     * Must never throw: every failure path is an [Outcome.Unavailable], so
     * a broken SDK cannot take the connect button down with it.
     *
     * An [Kind.Interstitial] that the user skips still counts as
     * [Outcome.Rewarded]: skipping is what "skippable" means, the
     * impression was served and paid for, and refusing the reward would be
     * charging for something we advertised as optional.
     */
    suspend fun show(activity: Activity, kind: Kind): Outcome

    /** Start loading both formats, so the next [show] does not make the user wait. */
    fun preload()
}

/**
 * The implementation in use until an ad network is wired up.
 *
 * Reports nothing available, which means every connection attempt takes the
 * grace path. That is deliberately the right behaviour for a build with no
 * ad inventory: the app is fully usable for testing, the head still records
 * every grant as `grace`, and the admin panel shows a fleet running
 * entirely on the fallback — which is exactly what is happening.
 */
object NoAds : AdGateway {
    override fun isReady(kind: AdGateway.Kind): Boolean = false

    override suspend fun show(activity: Activity, kind: AdGateway.Kind): AdGateway.Outcome =
        AdGateway.Outcome.Unavailable("рекламная сеть не подключена")

    override fun preload() = Unit
}
