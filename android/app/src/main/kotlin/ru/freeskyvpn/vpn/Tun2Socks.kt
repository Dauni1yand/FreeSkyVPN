package ru.freeskyvpn.vpn

import android.os.ParcelFileDescriptor
import android.util.Log

/**
 * The bridge between the tun device and Xray's SOCKS inbound.
 *
 * Xray-core has no tun inbound, so something has to read IP packets off the
 * tun file descriptor, turn them into TCP and UDP flows, and hand those to a
 * SOCKS proxy. `hev-socks5-tunnel` is the usual choice and is what the
 * native library referenced here is expected to be.
 *
 * Isolated behind this object for the same reason [XrayRuntime] is: the
 * native library is vendored rather than fetched from a repository, and
 * everything above this line should be reasonable about without it. A build
 * missing the library logs and fails to connect instead of failing to link.
 */
object Tun2Socks {

    private const val TAG = "Tun2Socks"
    private var running = false

    private val available: Boolean by lazy {
        try {
            System.loadLibrary("hev-socks5-tunnel")
            true
        } catch (t: UnsatisfiedLinkError) {
            Log.e(TAG, "tun2socks native library missing; see app/libs/README.md", t)
            false
        }
    }

    /**
     * @param tun the descriptor from `VpnService.Builder.establish()`
     * @param socksPort where Xray is listening
     * @param dnsPort Xray's DNS listener, so lookups stay inside the routing rules
     */
    fun start(tun: ParcelFileDescriptor, socksPort: Int, dnsPort: Int): Boolean {
        if (!available) return false
        if (running) stop()

        val config = """
            tunnel:
              mtu: 1400
            socks5:
              address: 127.0.0.1
              port: $socksPort
              udp: udp
            dns:
              address: 127.0.0.1
              port: $dnsPort
        """.trimIndent()

        return try {
            nativeStart(config, tun.fd)
            running = true
            true
        } catch (t: Throwable) {
            Log.e(TAG, "tun2socks refused to start", t)
            false
        }
    }

    fun stop() {
        if (!running) return
        running = false
        runCatching { nativeStop() }.onFailure { Log.w(TAG, "tun2socks stop failed", it) }
    }

    @JvmStatic private external fun nativeStart(config: String, fd: Int)
    @JvmStatic private external fun nativeStop()
}
