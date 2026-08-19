package ru.freeskyvpn

import android.app.Application
import ru.freeskyvpn.ads.AdGateway
import ru.freeskyvpn.ads.DebugAds
import ru.freeskyvpn.ads.NoAds

/**
 * Application singleton, kept deliberately thin.
 *
 * The only thing here is the ad gateway, because it is the one dependency
 * the whole product is gated on: no video, no time, no tunnel. Swapping in
 * a real network is a one-line change in one place rather than a search
 * through the UI.
 *
 * Debug builds get a fake that always succeeds. That is not convenience —
 * with [NoAds] every connection falls through to the head's grace path,
 * which is rate limited to once every few hours because it exists to
 * survive an ad outage rather than to replace ads. A tester would get
 * fifteen minutes and then nothing until evening.
 */
class FreeSkyApp : Application() {

    val ads: AdGateway = if (BuildConfig.DEBUG) DebugAds() else NoAds

    override fun onCreate() {
        super.onCreate()
        // Warm the first ad now: the user's first tap on connect should not
        // be the moment an SDK starts initialising.
        ads.preload()
    }
}
