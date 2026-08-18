package ru.freeskyvpn.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import ru.freeskyvpn.MainActivity
import ru.freeskyvpn.R
import ru.freeskyvpn.core.SplitTunnel
import ru.freeskyvpn.core.VlessLink
import ru.freeskyvpn.core.XrayConfigBuilder
import ru.freeskyvpn.data.Storage
import java.io.File

/**
 * The tunnel.
 *
 * Shape, which is the standard one for an Xray client on Android: this
 * service opens a tun device, a tun2socks bridge reads packets off it and
 * forwards them to the SOCKS inbound Xray is listening on, and Xray routes
 * from there. Xray-core has no tun inbound of its own, which is why the
 * bridge exists at all.
 *
 * Split tunnelling happens in two different places, and it is worth being
 * clear about which does what:
 *
 * * **Per app**, here, via [VpnService.Builder.addDisallowedApplication].
 *   Traffic from those apps never enters the tun in the first place. This
 *   is the only thing that helps with an app that refuses to run while a
 *   VPN interface exists — several Russian banking apps do exactly that,
 *   and no routing rule changes their mind.
 * * **Per destination**, inside the Xray config
 *   ([XrayConfigBuilder]), which is what routes the Russian internet
 *   direct for every other app on the device.
 */
class FreeSkyVpnService : VpnService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val xray: XrayRuntime = XrayRuntime.reflective()
    private var tun: ParcelFileDescriptor? = null
    private lateinit var storage: Storage

    override fun onCreate() {
        super.onCreate()
        storage = Storage(applicationContext)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_DISCONNECT -> {
                shutdown(VpnStatus.Disconnected, null)
                return START_NOT_STICKY
            }
            else -> {
                // Immediately, before any work. Android gives a service
                // roughly five seconds from being started to calling this,
                // and misses it with a ForegroundServiceDidNotStartInTime
                // crash — so it cannot wait for the tun device or a file
                // write. The notification's text is filled in later.
                goForeground(getString(R.string.notification_connecting))
                scope.launch { connect() }
            }
        }
        // NOT_STICKY on purpose: a VPN that silently reconnects itself after
        // the system killed it is a VPN the user cannot turn off reliably.
        return START_NOT_STICKY
    }

    private fun connect() {
        VpnStateHolder.set(VpnSnapshot(VpnStatus.Connecting))

        val vlessUrl = storage.lastVlessUrl
        if (vlessUrl.isNullOrBlank()) {
            shutdown(VpnStatus.Failed, "нет конфигурации — нажмите «Подключиться» ещё раз")
            return
        }

        val link = try {
            VlessLink.parse(vlessUrl)
        } catch (e: IllegalArgumentException) {
            // A config we cannot parse is a config we must not silently keep:
            // clearing it makes the next connect fetch a fresh one.
            storage.lastVlessUrl = null
            shutdown(VpnStatus.Failed, "конфигурация повреждена, получите новую")
            return
        }

        val bypassed = resolveBypassedApps()
        val descriptor = try {
            openTun(bypassed)
        } catch (t: Throwable) {
            Log.e(TAG, "could not establish the tun device", t)
            shutdown(VpnStatus.Failed, "не удалось поднять VPN-интерфейс")
            return
        }
        if (descriptor == null) {
            // establish() returns null when the VPN permission was revoked
            // while we were preparing — the user has to grant it again.
            shutdown(VpnStatus.Failed, "разрешение на VPN отозвано")
            return
        }
        tun = descriptor

        val configFile = File(filesDir, "xray_config.json").apply {
            writeText(XrayConfigBuilder.buildJson(link, storage.routingPolicy))
        }

        goForeground(link.label)

        if (!xray.start(configFile, assetsDir())) {
            shutdown(VpnStatus.Failed, "не удалось запустить ядро — обновите приложение")
            return
        }

        if (!Tun2Socks.start(descriptor, XrayConfigBuilder.SOCKS_PORT, XrayConfigBuilder.DNS_PORT)) {
            shutdown(VpnStatus.Failed, "не удалось подключить туннель")
            return
        }

        VpnStateHolder.set(
            VpnSnapshot(
                status = VpnStatus.Connected,
                nodeCountry = storage.lastNodeCountry,
                bypassedApps = bypassed.size,
            )
        )
    }

    /**
     * Which installed apps bypass the tunnel entirely.
     *
     * The list is filtered against what is actually installed before it
     * reaches [VpnService.Builder]: `addDisallowedApplication` throws on an
     * unknown package, and one renamed bank app must not be able to stop
     * the VPN from starting at all.
     */
    private fun resolveBypassedApps(): List<String> {
        if (!storage.splitTunnelEnabled) return emptyList()

        val installed = packageManager
            .getInstalledApplications(PackageManager.GET_META_DATA)
            .map { it.packageName }
            .toSet()

        return SplitTunnel.resolve(
            policyPackages = storage.routingPolicy.directPackages,
            installedPackages = installed,
            userExcluded = storage.userExcluded,
            userIncluded = storage.userIncluded,
            ownPackage = packageName,
        )
    }

    private fun openTun(bypassed: List<String>): ParcelFileDescriptor? {
        val builder = Builder()
            .setSession("FreeSkyVPN")
            .setMtu(MTU)
            .addAddress("10.10.10.1", 32)
            .addRoute("0.0.0.0", 0)
            // IPv6 is claimed as well, and that is a leak fix rather than a
            // feature: with only the IPv4 route above, a device on a
            // dual-stack network sends IPv6 traffic straight out of the
            // tunnel. Since most exit nodes are IPv4-only, the point is to
            // capture IPv6 and let it fail inside the tunnel rather than
            // succeed outside it.
            .addAddress("fd00:1:1:1::1", 128)
            .addRoute("::", 0)
            // Xray answers DNS on loopback; pointing the tun's resolver at a
            // real address here would let queries escape the routing rules
            // and defeat the whole split.
            .addDnsServer("10.10.10.1")
            .setBlocking(false)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Otherwise Android may treat the tunnel as metered and hold
            // back background sync for every app behind it.
            builder.setMetered(false)
        }

        bypassed.forEach { pkg ->
            try {
                builder.addDisallowedApplication(pkg)
            } catch (e: PackageManager.NameNotFoundException) {
                // Filtered above, so this is a race with an uninstall. Skip
                // it rather than failing the whole connection.
                Log.w(TAG, "package vanished while building the tunnel: $pkg")
            }
        }

        return builder.establish()
    }

    /**
     * Where geoip.dat and geosite.dat live.
     *
     * Xray needs them on disk to resolve `geoip:ru`. They are shipped in the
     * APK's assets and copied out once; without them the IP-based direct
     * rules silently match nothing, and only the domain rules would work.
     */
    private fun assetsDir(): File {
        val dir = File(filesDir, "xray").apply { mkdirs() }
        listOf("geoip.dat", "geosite.dat").forEach { name ->
            val target = File(dir, name)
            if (!target.exists()) {
                runCatching {
                    assets.open(name).use { input ->
                        target.outputStream().use { input.copyTo(it) }
                    }
                }.onFailure { Log.w(TAG, "no $name in assets; geoip rules will not match", it) }
            }
        }
        return dir
    }

    /**
     * Enters the foreground with the typed API where the platform wants one.
     *
     * Android 14 requires a foreground service to declare what it is for.
     * `specialUse` with the `vpn` subtype in the manifest is what VPN apps
     * use; passing the type here has to match it or the service is refused.
     */
    private fun goForeground(text: String) {
        val notification = notification(text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun shutdown(status: VpnStatus, message: String?) {
        Tun2Socks.stop()
        xray.stop()
        runCatching { tun?.close() }
        tun = null
        VpnStateHolder.set(VpnSnapshot(status = status, message = message))
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onRevoke() {
        // The system or another VPN app took the tunnel away.
        shutdown(VpnStatus.Disconnected, "VPN отключён системой")
        super.onRevoke()
    }

    override fun onDestroy() {
        Tun2Socks.stop()
        xray.stop()
        runCatching { tun?.close() }
        scope.cancel()
        super.onDestroy()
    }

    // --- notification ----------------------------------------------------

    private fun notification(label: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "VPN",
                    // LOW: a persistent notification is mandatory for a
                    // foreground service, but it should not make a sound
                    // every time someone connects.
                    NotificationManager.IMPORTANCE_LOW,
                ).apply { setShowBadge(false) }
            )
        }

        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, FreeSkyVpnService::class.java).setAction(ACTION_DISCONNECT),
            PendingIntent.FLAG_IMMUTABLE,
        )

        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_connected))
            .setContentText(label)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(null, getString(R.string.disconnect), stop).build()
            )
            .setOngoing(true)
            .build()
    }

    companion object {
        const val ACTION_DISCONNECT = "ru.freeskyvpn.DISCONNECT"
        private const val CHANNEL_ID = "vpn"
        private const val NOTIFICATION_ID = 1
        // 1500 rather than 1500-ish: leaves room for the VLESS/Reality
        // headers inside a 1500-byte path without fragmenting.
        private const val MTU = 1400
        private const val TAG = "FreeSkyVpn"
    }
}
