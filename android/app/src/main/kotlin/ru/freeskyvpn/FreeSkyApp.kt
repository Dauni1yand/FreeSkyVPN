package ru.freeskyvpn

import android.app.Application
import ru.freeskyvpn.ads.AdGateway
import ru.freeskyvpn.ads.NoAds

/**
 * Application singleton, kept deliberately thin.
 *
 * The only thing here is the ad gateway, and only so that swapping [NoAds]
 * for a real implementation is a one-line change in one place rather than a
 * search through the UI.
 */
class FreeSkyApp : Application() {

    val ads: AdGateway = NoAds

    override fun onCreate() {
        super.onCreate()
        ads.preload()
    }
}
