package ru.freeskyvpn.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * What stays outside the tunnel.
 *
 * The requirement is that Russian services keep working without the VPN, and
 * that is not only about speed: banks, Gosuslugi and several media services
 * refuse or degrade connections from foreign addresses, so routing them
 * through a node abroad does not make them slow, it makes them broken.
 *
 * Fetched from the head so a misbehaving service can be moved to the direct
 * list without a store review. [DEFAULT] is what the app uses before its
 * first successful fetch and whenever the head cannot be reached — it must
 * therefore be a complete, usable policy on its own, not a placeholder.
 */
@Serializable
data class RoutingPolicy(
    val version: Int,
    /**
     * Top-level domains that are Russian by definition. One rule covers
     * hundreds of thousands of hosts, which is why this is not a curated
     * list of individual domains that someone would have to keep current.
     */
    @SerialName("direct_tlds") val directTlds: List<String> = emptyList(),
    /** Russian services living on foreign TLDs — nothing in the name says so. */
    @SerialName("direct_domains") val directDomains: List<String> = emptyList(),
    /** Whole apps kept off the tunnel; see [SplitTunnel]. */
    @SerialName("direct_packages") val directPackages: List<String> = emptyList(),
    /** Xray geoip codes. `private` keeps the local network reachable. */
    @SerialName("direct_geoip") val directGeoip: List<String> = emptyList(),
) {
    companion object {
        /**
         * Mirrors `head/app/services/routing_policy.py`. Kept in step by
         * hand, which is safe only because the head's copy always wins the
         * moment it is reachable — this exists so a first launch on a
         * flaky connection still routes correctly, not as a second source
         * of truth.
         */
        val DEFAULT = RoutingPolicy(
            version = 0,
            directTlds = listOf("ru", "su", "рф", "moscow", "tatar"),
            directDomains = listOf(
                "vk.com", "vk.me", "vk-cdn.net", "vkuser.net", "vkuservideo.net",
                "userapi.com", "mycdn.me",
                "yandex.com", "yandex.net", "yastatic.net",
                "wildberries.com", "wb.ru", "ozon.com", "lenta.com", "sportmaster.com",
                "okko.tv", "premier.one", "start.ru", "kion.ru",
                "2gis.com", "pobeda.aero", "utair.com",
                "tbank.ru", "gazprombank.com", "psbank.com", "mirconnect.ru",
                "megafon.com", "beeline.com",
            ),
            directPackages = listOf(
                "ru.sberbankmobile", "ru.vtb24.mobilebanking.android",
                "com.idamob.tinkoff.android", "ru.alfabank.mobile.android",
                "ru.gazprombank.android.mobilebank.app", "ru.raiffeisennews",
                "ru.open.bank", "ru.rosbank.android", "ru.mkb.mobile",
                "ru.psbank.mobile", "ru.mtsbank.mobile", "ru.pochta.bank",
                "ru.gosuslugi.pgu", "ru.mos.mobile", "ru.fns.billing",
                "ru.mts.mymts", "ru.beeline.services", "ru.megafon.mlk",
                "ru.tele2.mytele2",
                "ru.ozon.app.android", "com.wildberries.ru", "ru.avito.android",
                "ru.yandex.market", "ru.sbermegamarket.app", "ru.sbermarket.app",
                "ru.yandex.yandexmaps", "ru.yandex.taxi", "ru.dublgis.dgismobile",
                "ru.rzd.pass", "ru.aeroflot",
                "com.vkontakte.android", "ru.ok.android", "ru.mail.mailapp",
                "ru.rutube.app", "ru.ivi.client", "ru.more.play", "ru.kinopoisk",
                "ru.yandex.searchplugin",
                "ru.cian.main", "ru.hh.android", "ru.auto.ara",
            ),
            directGeoip = listOf("ru", "private"),
        )
    }

    /**
     * Xray `domain` matchers for everything that should go direct.
     *
     * `domain:` matches a label and everything beneath it, so `domain:ru`
     * is every *.ru host — including bare `ru`, which costs nothing.
     */
    fun directDomainRules(): List<String> =
        (directTlds + directDomains).map { "domain:${it.trim().lowercase()}" }

    fun directIpRules(): List<String> = directGeoip.map { "geoip:${it.trim().lowercase()}" }
}
