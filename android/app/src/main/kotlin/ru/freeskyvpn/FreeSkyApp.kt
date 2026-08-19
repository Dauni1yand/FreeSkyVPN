package ru.freeskyvpn

import android.app.Application
import ru.freeskyvpn.ads.AdGateway
import ru.freeskyvpn.ads.NoAds

/**
 * Application singleton, kept deliberately thin.
 *
 * The only thing here is the ad gateway, because it is the one dependency
 * the whole product is gated on: no rewarded video, no hour, no tunnel.
 * Swapping [NoAds] for a real network is a one-line change in one place
 * rather than a search through the UI.
 */
class FreeSkyApp : Application() {

    val ads: AdGateway = NoAds

    override fun onCreate() {
        super.onCreate()
        // Warm the first ad now: the user's first tap on connect should not
        // be the moment an SDK starts initialising.
        ads.preload()
    }
}
