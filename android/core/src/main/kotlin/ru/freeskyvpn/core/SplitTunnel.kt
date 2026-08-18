package ru.freeskyvpn.core

/**
 * Which installed apps are kept off the tunnel entirely.
 *
 * This is a second, coarser layer on top of the routing rules in
 * [XrayConfigBuilder], and it exists because routing cannot solve one
 * specific problem: an app that inspects the network itself and refuses to
 * run while any VPN interface is present. Several Russian banking apps do
 * exactly that, and no amount of correct routing changes their mind — the
 * traffic has to not enter the tunnel at all.
 *
 * The package list is advisory. It is resolved against what is actually
 * installed, so a renamed or misspelled id is skipped rather than throwing:
 * `VpnService.Builder.addDisallowedApplication` rejects an unknown package,
 * and one stale entry must not be able to stop the VPN from starting.
 */
object SplitTunnel {

    /**
     * The packages to exclude, given the policy and what is on the device.
     *
     * @param policyPackages package ids the head proposes
     * @param installedPackages every package id present on this device
     * @param userExcluded extra packages the user chose to keep off the VPN
     * @param userIncluded packages the user chose to send *through* the VPN
     *        even though the policy excludes them
     * @param ownPackage this app; see below
     */
    fun resolve(
        policyPackages: Collection<String>,
        installedPackages: Set<String>,
        userExcluded: Collection<String> = emptyList(),
        userIncluded: Collection<String> = emptyList(),
        ownPackage: String? = null,
    ): List<String> {
        val included = userIncluded.toSet()
        return (policyPackages + userExcluded)
            .asSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()
            // The user's decision wins over the shipped policy in both
            // directions; a policy that could not be overridden would be a
            // setting the user cannot actually change.
            .filter { it !in included }
            .filter { it in installedPackages }
            // Excluding ourselves would put our own API calls outside the
            // tunnel. That is not a bug in itself — but it also breaks
            // `protect()`-free designs in confusing ways, and there is no
            // reason to route our own traffic differently from the rest.
            .filter { it != ownPackage }
            .sorted()
            .toList()
    }
}
