package ru.freeskyvpn.core

/**
 * How much of the paid-for hour is left, and what to say about it.
 *
 * Lives in `core` rather than in the UI because it is where two easy
 * mistakes hide: counting down from a stale server value as though it were
 * still fresh, and letting a countdown run past zero into negative time.
 * Both are decidable without a device, so both are tested.
 *
 * The server sends seconds remaining rather than an absolute deadline on
 * purpose — a phone's clock can be wrong by hours, and an expiry the device
 * computes from its own idea of "now" would be wrong by the same amount.
 */
object AccessCountdown {

    /**
     * Seconds left, given what the server said and how long ago it said it.
     *
     * @param serverSeconds `access_seconds_remaining` from the last /me call
     * @param elapsedMillis monotonic time since that call — *not* wall clock,
     *        which can jump when the network corrects it
     */
    fun remaining(serverSeconds: Int, elapsedMillis: Long): Int {
        if (serverSeconds <= 0) return 0
        val elapsedSeconds = (elapsedMillis / 1000).coerceAtLeast(0)
        return (serverSeconds - elapsedSeconds).coerceAtLeast(0).toInt()
    }

    fun isActive(serverSeconds: Int, elapsedMillis: Long): Boolean =
        remaining(serverSeconds, elapsedMillis) > 0

    /**
     * The countdown, as a person reads it.
     *
     * Minutes and seconds under an hour, because the unit people care about
     * near the end is minutes; hours and minutes above it. Never "0:00"
     * while time actually remains, and never a negative anything.
     */
    fun format(seconds: Int): String {
        if (seconds <= 0) return "0 мин"
        val totalMinutes = seconds / 60
        val hours = totalMinutes / 60
        val minutes = totalMinutes % 60

        return when {
            hours > 0 -> "$hours ч $minutes мин"
            totalMinutes > 0 -> "$totalMinutes мин"
            // Under a minute still has to read as time left, or the user
            // sees "0 мин" on a connection that is still working.
            else -> "меньше минуты"
        }
    }

    /** True once it is worth nudging the user to watch another ad. */
    fun shouldWarn(seconds: Int, warnBelowSeconds: Int = 5 * 60): Boolean =
        seconds in 1..warnBelowSeconds
}
