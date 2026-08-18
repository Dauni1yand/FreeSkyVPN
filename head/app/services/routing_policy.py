"""What goes around the VPN rather than through it.

The requirement is "все российские сервисы, приложения и сайты открываются
без VPN", and it is not only about speed. Many Russian services — banks,
Gosuslugi, some media — refuse or degrade connections from foreign
addresses, so sending their traffic through a node in Amsterdam does not
make them slow, it makes them broken. Routing them direct is what makes
them work at all.

Three layers, because each catches what the others miss:

1. **TLD rules.** `.ru`, `.рф` and `.su` are, by definition, the Russian
   internet, and they are reachable without a VPN from inside Russia. One
   rule covers hundreds of thousands of hosts and needs no maintenance —
   which is the whole reason this is not a hand-curated list of domains.
2. **Named exceptions.** A number of large Russian services live on foreign
   TLDs: vk.com, 2gis.com, okko.tv, premier.one. These have to be listed
   individually because nothing about the name identifies them.
3. **geoip:ru.** Catches traffic addressed by IP, and Russian services
   hosted under a TLD nobody thought of.

A fourth layer lives on the client: whole apps excluded from the tunnel by
package name (`direct_packages`). Layers 1–3 handle traffic; that one
handles applications that inspect the network themselves and refuse to run
while any VPN is up, which no routing rule can help with.

Served from the head rather than compiled into the app so that a service
that starts misbehaving can be moved to the direct list without waiting for
a Play review. The app ships this same content as a fallback for its first
launch and for when the head is unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_settings

# The Russian internet, by name. Xray's `domain:` matcher covers a label and
# everything under it, so `domain:ru` is every *.ru host in one rule.
DIRECT_TLDS: tuple[str, ...] = ("ru", "su", "рф", "moscow", "tatar")

# Russian services that do not live under a Russian TLD. Each one is here
# because its name gives no hint of where it belongs.
DIRECT_DOMAINS: tuple[str, ...] = (
    # VK group
    "vk.com",
    "vk.me",
    "vk-cdn.net",
    "vkuser.net",
    "vkuservideo.net",
    "userapi.com",
    "mycdn.me",
    # Yandex outside .ru
    "yandex.com",
    "yandex.net",
    "yastatic.net",
    "yandex-team.com",
    # marketplaces and retail
    "wildberries.com",
    "wb.ru",
    "ozon.com",
    "lenta.com",
    "sportmaster.com",
    # media and streaming
    "okko.tv",
    "premier.one",
    "start.ru",
    "kion.ru",
    # maps, transport, services
    "2gis.com",
    "pobeda.aero",
    "utair.com",
    # banking and payments
    "tbank.ru",
    "gazprombank.com",
    "psbank.com",
    "mirconnect.ru",
    # infrastructure Russian sites depend on
    "cdn-a.ru",
    "megafon.com",
    "beeline.com",
)

# Whole applications kept off the tunnel. Two reasons an app lands here even
# though its traffic would already be routed direct by the rules above:
# some refuse to run while any VPN interface exists at all, and some pin
# certificates or use IP literals that no domain rule can catch.
#
# Package names are matched against what is actually installed and anything
# unknown is skipped, so a wrong or renamed entry costs nothing — which is
# the only reason it is safe to ship a list this long without being able to
# verify every id against a real device.
DIRECT_PACKAGES: tuple[str, ...] = (
    # banking — the category that most often refuses to run under a VPN
    "ru.sberbankmobile",
    "ru.sberbank.sbol",
    "ru.vtb24.mobilebanking.android",
    "com.idamob.tinkoff.android",
    "ru.alfabank.mobile.android",
    "ru.gazprombank.android.mobilebank.app",
    "ru.raiffeisennews",
    "ru.open.bank",
    "ru.rosbank.android",
    "ru.mkb.mobile",
    "ru.sovcombank.halva",
    "ru.psbank.mobile",
    "ru.mtsbank.mobile",
    "ru.pochta.bank",
    "ru.uralsib.mobile",
    "ru.akbars.mobile",
    "ru.rshb.mobile",
    # government and public services
    "ru.gosuslugi.pgu",
    "ru.mos.mobile",
    "ru.gibdd.gosuslugi",
    "ru.fns.billing",
    "ru.gosuslugi.dom",
    # telecoms — self-service apps are tied to the operator's own network
    "ru.mts.mymts",
    "ru.beeline.services",
    "ru.megafon.mlk",
    "ru.tele2.mytele2",
    "ru.rt.video.app.mobile",
    # marketplaces and delivery
    "ru.ozon.app.android",
    "com.wildberries.ru",
    "ru.avito.android",
    "ru.yandex.market",
    "ru.sbermegamarket.app",
    "ru.sbermarket.app",
    "com.lamoda.lite",
    "ru.dns_shop.android",
    "ru.mvideo.mobile",
    "ru.citilink.android",
    "ru.samokat.android",
    "ru.vkusvill.client",
    "com.perekrestok.app",
    "ru.lenta.lentochka",
    "ru.magnit.mobile",
    "ru.x5.pyaterochka",
    "ru.dodopizza.app",
    # transport and maps
    "ru.yandex.yandexmaps",
    "ru.yandex.taxi",
    "ru.dublgis.dgismobile",
    "ru.rzd.pass",
    "ru.aeroflot",
    # media and social
    "com.vkontakte.android",
    "ru.ok.android",
    "ru.mail.mailapp",
    "ru.rutube.app",
    "ru.ivi.client",
    "ru.more.play",
    "ru.kinopoisk",
    "ru.yandex.searchplugin",
    # classifieds, property, jobs
    "ru.cian.main",
    "ru.hh.android",
    "ru.auto.ara",
    "ru.drom.auto",
)

# `private` keeps the local network reachable while the tunnel is up —
# printers, NAS, the router's admin page. Forgetting it is the classic way
# to make a VPN feel broken at home.
DIRECT_GEOIP: tuple[str, ...] = ("ru", "private")


@dataclass(frozen=True)
class RoutingPolicy:
    version: int
    direct_tlds: tuple[str, ...] = field(default=DIRECT_TLDS)
    direct_domains: tuple[str, ...] = field(default=DIRECT_DOMAINS)
    direct_packages: tuple[str, ...] = field(default=DIRECT_PACKAGES)
    direct_geoip: tuple[str, ...] = field(default=DIRECT_GEOIP)


def current_policy() -> RoutingPolicy:
    """The policy every app fetches on launch.

    A plain function over module constants rather than a table: this is not
    operator-tunable data like the SNI pool, it is a product decision that
    changes with a deploy. Bump `routing_policy_version` when the content
    changes so clients notice.
    """
    return RoutingPolicy(version=get_settings().routing_policy_version)
