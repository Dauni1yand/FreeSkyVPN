package ru.freeskyvpn.ads

import android.app.Activity

/**
 * Where rewarded video will go.
 *
 * Deliberately a no-op for now: whether advertising pays for the servers is
 * an arithmetic question that has not been answered yet, and wiring an SDK
 * in before the answer would mean shipping a tracking library, a consent
 * flow and a Play data-safety declaration for revenue nobody has sized.
 *
 * The interface exists rather than the call sites being empty, so that
 * adding the SDK later is one implementation and a binding — not a hunt
 * through the UI for the right places to interrupt the user.
 *
 * Whatever lands here has one hard constraint: it must never gate the
 * connect button. A free tier that cannot connect until an ad loads is a
 * free tier that does not work on the connections these users actually have.
 */
interface AdGateway {

    /** Whether a rewarded ad could be shown right now. */
    val isRewardAvailable: Boolean

    /**
     * Show a rewarded ad.
     *
     * @return true if the user earned the reward. A false must leave the
     *         user no worse off than if they had never been offered it.
     */
    suspend fun showRewarded(activity: Activity): Boolean

    /** Called on app start so an implementation can warm up. */
    fun preload()
}

/**
 * The implementation in use until the economics are settled.
 *
 * Reports nothing available, which makes every ad entry point in the UI
 * hide itself. That is the correct behaviour for a build with no ad
 * inventory — not an error state, and not a placeholder the user can see.
 */
object NoAds : AdGateway {
    override val isRewardAvailable: Boolean get() = false
    override suspend fun showRewarded(activity: Activity): Boolean = false
    override fun preload() = Unit
}
