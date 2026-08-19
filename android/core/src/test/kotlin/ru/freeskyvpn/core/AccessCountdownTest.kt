package ru.freeskyvpn.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class AccessCountdownTest {

    // --- counting down ---------------------------------------------------

    @Test
    fun `counts down from what the server said`() {
        assertEquals(3540, AccessCountdown.remaining(serverSeconds = 3600, elapsedMillis = 60_000))
    }

    @Test
    fun `never goes negative`() {
        // A countdown running past zero shows "-3 мин" on a screen the user
        // is staring at while wondering why nothing works.
        assertEquals(0, AccessCountdown.remaining(serverSeconds = 60, elapsedMillis = 600_000))
    }

    @Test
    fun `no access stays no access`() {
        assertEquals(0, AccessCountdown.remaining(serverSeconds = 0, elapsedMillis = 0))
        assertEquals(0, AccessCountdown.remaining(serverSeconds = -5, elapsedMillis = 0))
    }

    @Test
    fun `a clock that jumped backwards does not add time`() {
        // elapsed is monotonic, but defensive: a negative elapsed must not
        // become free minutes.
        assertEquals(3600, AccessCountdown.remaining(serverSeconds = 3600, elapsedMillis = -50_000))
    }

    @Test
    fun `access is active while any time remains`() {
        assertTrue(AccessCountdown.isActive(10, 0))
        assertFalse(AccessCountdown.isActive(10, 11_000))
    }

    // --- how it reads ----------------------------------------------------

    @Test
    fun `an hour reads in hours and minutes`() {
        assertEquals("1 ч 0 мин", AccessCountdown.format(3600))
        assertEquals("2 ч 30 мин", AccessCountdown.format(2 * 3600 + 30 * 60))
    }

    @Test
    fun `under an hour reads in minutes`() {
        assertEquals("59 мин", AccessCountdown.format(59 * 60))
        assertEquals("1 мин", AccessCountdown.format(60))
    }

    @Test
    fun `the last minute does not read as zero`() {
        // "0 мин" on a working connection is the app telling the user it is
        // broken when it is not.
        assertEquals("меньше минуты", AccessCountdown.format(30))
        assertEquals("меньше минуты", AccessCountdown.format(1))
    }

    @Test
    fun `expired reads as zero`() {
        assertEquals("0 мин", AccessCountdown.format(0))
        assertEquals("0 мин", AccessCountdown.format(-100))
    }

    // --- warning ---------------------------------------------------------

    @Test
    fun `warns near the end so the ad can be watched before the drop`() {
        assertTrue(AccessCountdown.shouldWarn(4 * 60))
        assertFalse(AccessCountdown.shouldWarn(30 * 60))
    }

    @Test
    fun `does not warn about time that has already run out`() {
        // At zero the UI shows the ad gate itself; a warning on top of it
        // would be telling someone to hurry for something already gone.
        assertFalse(AccessCountdown.shouldWarn(0))
    }
}
