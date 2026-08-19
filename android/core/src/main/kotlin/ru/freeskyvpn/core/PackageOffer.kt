package ru.freeskyvpn.core

/**
 * What the duration picker shows, and in what order.
 *
 * Lives in `core` because it is a decision about data, not about pixels:
 * which options are offered, how they are ordered, and what to fall back on
 * when the server has told us nothing yet. Getting the fallback wrong is
 * the interesting failure — an app that shows an empty picker on first
 * launch is an app that cannot be used at all, since there is no way to
 * connect without choosing something.
 */
object PackageOffer {

    /**
     * What to show before the first successful /me call, and if the server
     * ever sends an empty list.
     *
     * These mirror the head's own table. They are a floor, not a source of
     * truth: the server's copy always wins, and the server decides what a
     * view is actually worth regardless of what is displayed here.
     */
    val FALLBACK = listOf(
        AccessPackage(
            code = "short",
            label = "15 минут",
            adKind = "interstitial",
            views = 1,
            totalMinutes = 15,
        ),
        AccessPackage(
            code = "hour",
            label = "1 час",
            adKind = "rewarded",
            views = 1,
            totalMinutes = 60,
        ),
        AccessPackage(
            code = "double",
            label = "2 часа",
            adKind = "rewarded",
            views = 2,
            totalMinutes = 120,
        ),
    )

    /** The options to display, shortest first, never empty. */
    fun offered(fromServer: List<AccessPackage>): List<AccessPackage> =
        (fromServer.takeIf { it.isNotEmpty() } ?: FALLBACK).sortedBy { it.totalMinutes }

    /**
     * What each option costs the user, in words.
     *
     * Said plainly rather than hidden: someone choosing between fifteen
     * minutes and two hours is choosing how many videos to sit through, and
     * hiding that until the video starts is how an app earns a one-star
     * review.
     */
    fun cost(pkg: AccessPackage): String = when {
        pkg.isSkippable -> "ролик можно пропустить"
        pkg.views == 1 -> "1 ролик целиком"
        else -> "${pkg.views} ${plural(pkg.views)} целиком"
    }

    private fun plural(n: Int): String = when {
        n % 10 == 1 && n % 100 != 11 -> "ролик"
        n % 10 in 2..4 && n % 100 !in 12..14 -> "ролика"
        else -> "роликов"
    }
}
