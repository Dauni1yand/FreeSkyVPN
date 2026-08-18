package ru.freeskyvpn.vpn

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * What the connect button is showing.
 *
 * Held in a process-wide flow rather than passed through binder callbacks:
 * the service and the UI have independent lifecycles, the UI is frequently
 * not running at all, and a bound-service handshake would add a failure
 * mode to a value that is one enum wide.
 */
enum class VpnStatus { Disconnected, Connecting, Connected, Failed }

data class VpnSnapshot(
    val status: VpnStatus = VpnStatus.Disconnected,
    val nodeCountry: String? = null,
    /** Set only in [VpnStatus.Failed]; shown to the user verbatim. */
    val message: String? = null,
    /** How many apps are currently bypassing the tunnel. */
    val bypassedApps: Int = 0,
)

object VpnStateHolder {
    private val _state = MutableStateFlow(VpnSnapshot())
    val state: StateFlow<VpnSnapshot> = _state

    fun set(snapshot: VpnSnapshot) { _state.value = snapshot }

    fun update(block: (VpnSnapshot) -> VpnSnapshot) { _state.value = block(_state.value) }
}
