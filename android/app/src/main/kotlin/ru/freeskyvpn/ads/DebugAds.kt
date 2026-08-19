package ru.freeskyvpn.ads

import android.app.Activity
import android.util.Log
import kotlinx.coroutines.delay

/**
 * A stand-in ad network for debug builds.
 *
 * Without it the app is barely testable. [NoAds] reports nothing available,
 * so every connection falls through to the head's grace path — which is
 * rate limited to once every few hours on purpose, because it exists to
 * survive an ad outage rather than to replace ads. A tester would get
 * fifteen minutes and then be locked out for the rest of the afternoon.
 *
 * This reports success instead, so the whole flow can be exercised for
 * real: the picker, a package that wants two videos, the per-view credit,
 * the countdown, the automatic disconnect at zero, and reconnecting
 * without an ad while time remains.
 *
 * It is wired up only when `BuildConfig.DEBUG` is set, so a release build
 * cannot ship a gateway that hands out access for nothing.
 */
class DebugAds(private val playbackMillis: Long = 1_500) : AdGateway {

    override fun isReady(kind: AdGateway.Kind): Boolean = true

    override suspend fun show(activity: Activity, kind: AdGateway.Kind): AdGateway.Outcome {
        Log.i(TAG, "pretending to show a $kind ad")
        // A real ad takes time, and code that assumes it is instant tends to
        // work only against a fake one. The overlay and the "2 из 2" counter
        // are only visible at all because this takes a moment.
        delay(playbackMillis)
        return AdGateway.Outcome.Rewarded
    }

    override fun preload() = Unit

    private companion object {
        const val TAG = "DebugAds"
    }
}

/**
 * A gateway that always fails, for exercising the fallback deliberately.
 *
 * Swap it in when the thing being tested is what happens when the ad
 * network is down — the grace grant, its rate limit, and the notice that
 * explains why the connection feels slower. That path is the one most
 * likely to be wrong in production and the least likely to be reached by
 * accident.
 */
object BrokenAds : AdGateway {
    override fun isReady(kind: AdGateway.Kind): Boolean = false

    override suspend fun show(activity: Activity, kind: AdGateway.Kind): AdGateway.Outcome =
        AdGateway.Outcome.Unavailable("отладка: сеть недоступна")

    override fun preload() = Unit
}
