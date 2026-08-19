package ru.freeskyvpn.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class PackageOfferTest {

    private val fromServer = listOf(
        AccessPackage("double", "2 часа", "rewarded", views = 2, totalMinutes = 120),
        AccessPackage("short", "15 минут", "interstitial", views = 1, totalMinutes = 15),
        AccessPackage("hour", "1 час", "rewarded", views = 1, totalMinutes = 60),
    )

    @Test
    fun `options are shown shortest first whatever order they arrive in`() {
        assertEquals(
            listOf("short", "hour", "double"),
            PackageOffer.offered(fromServer).map { it.code },
        )
    }

    @Test
    fun `an empty server list falls back to something usable`() {
        // There is no way to connect without choosing an option, so an
        // empty picker is an unusable app — not a cosmetic problem.
        assertTrue(PackageOffer.offered(emptyList()).isNotEmpty())
    }

    @Test
    fun `the fallback matches what the head offers`() {
        assertEquals(
            listOf("short", "hour", "double"),
            PackageOffer.offered(emptyList()).map { it.code },
        )
        assertEquals(listOf(15, 60, 120), PackageOffer.offered(emptyList()).map { it.totalMinutes })
    }

    @Test
    fun `a skippable option says so`() {
        val short = PackageOffer.FALLBACK.first { it.code == "short" }
        assertTrue(short.isSkippable)
        assertEquals("ролик можно пропустить", PackageOffer.cost(short))
    }

    @Test
    fun `the cost of each option is stated in videos`() {
        assertEquals("1 ролик целиком", PackageOffer.cost(PackageOffer.FALLBACK[1]))
        assertEquals("2 ролика целиком", PackageOffer.cost(PackageOffer.FALLBACK[2]))
    }

    @Test
    fun `russian plurals survive counts that are not two`() {
        fun cost(n: Int) = PackageOffer.cost(
            AccessPackage("x", "x", "rewarded", views = n, totalMinutes = 60 * n)
        )
        assertEquals("5 роликов целиком", cost(5))
        assertEquals("21 ролик целиком", cost(21))
        assertEquals("11 роликов целиком", cost(11))
    }

    @Test
    fun `a server that adds an option shows it without an app release`() {
        val extended = fromServer + AccessPackage("day", "24 часа", "rewarded", 24, 1440)
        assertEquals("day", PackageOffer.offered(extended).last().code)
    }
}
