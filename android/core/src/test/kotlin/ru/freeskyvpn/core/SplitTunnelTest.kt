package ru.freeskyvpn.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class SplitTunnelTest {

    private val installed = setOf(
        "ru.sberbankmobile", "ru.gosuslugi.pgu", "com.example.game", "ru.freeskyvpn"
    )

    @Test
    fun `only installed packages are excluded`() {
        // addDisallowedApplication throws on an unknown package, and one
        // stale entry must not stop the VPN from starting.
        val resolved = SplitTunnel.resolve(
            policyPackages = listOf("ru.sberbankmobile", "ru.bank.that.was.renamed"),
            installedPackages = installed,
        )
        assertEquals(listOf("ru.sberbankmobile"), resolved)
    }

    @Test
    fun `the user can exclude an app the policy does not mention`() {
        val resolved = SplitTunnel.resolve(
            policyPackages = emptyList(),
            installedPackages = installed,
            userExcluded = listOf("com.example.game"),
        )
        assertEquals(listOf("com.example.game"), resolved)
    }

    @Test
    fun `the user can override the policy and tunnel an app anyway`() {
        // A setting the user cannot actually change is not a setting.
        val resolved = SplitTunnel.resolve(
            policyPackages = listOf("ru.sberbankmobile", "ru.gosuslugi.pgu"),
            installedPackages = installed,
            userIncluded = listOf("ru.sberbankmobile"),
        )
        assertEquals(listOf("ru.gosuslugi.pgu"), resolved)
    }

    @Test
    fun `an explicit include beats an explicit exclude`() {
        val resolved = SplitTunnel.resolve(
            policyPackages = emptyList(),
            installedPackages = installed,
            userExcluded = listOf("com.example.game"),
            userIncluded = listOf("com.example.game"),
        )
        assertTrue(resolved.isEmpty())
    }

    @Test
    fun `we never exclude ourselves`() {
        val resolved = SplitTunnel.resolve(
            policyPackages = listOf("ru.freeskyvpn", "ru.sberbankmobile"),
            installedPackages = installed,
            ownPackage = "ru.freeskyvpn",
        )
        assertFalse("ru.freeskyvpn" in resolved)
    }

    @Test
    fun `duplicates and blanks are dropped`() {
        val resolved = SplitTunnel.resolve(
            policyPackages = listOf("ru.sberbankmobile", " ru.sberbankmobile ", "", "   "),
            installedPackages = installed,
        )
        assertEquals(listOf("ru.sberbankmobile"), resolved)
    }

    @Test
    fun `the result is stable across calls`() {
        // The list is compared against the running tunnel's to decide
        // whether a restart is needed; an unstable order would restart the
        // VPN on every policy refresh.
        val args = listOf("ru.gosuslugi.pgu", "ru.sberbankmobile")
        assertEquals(
            SplitTunnel.resolve(args, installed),
            SplitTunnel.resolve(args.reversed(), installed),
        )
    }

    @Test
    fun `an empty device excludes nothing`() {
        assertTrue(SplitTunnel.resolve(RoutingPolicy.DEFAULT.directPackages, emptySet()).isEmpty())
    }
}
