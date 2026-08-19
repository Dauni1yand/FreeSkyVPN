package ru.freeskyvpn

import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.VpnService
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.activity.ComponentActivity
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import ru.freeskyvpn.ui.MainViewModel
import ru.freeskyvpn.ui.screen.AccountScreen
import ru.freeskyvpn.ui.screen.AppEntry
import ru.freeskyvpn.ui.screen.Banner
import ru.freeskyvpn.ui.screen.ConnectScreen
import ru.freeskyvpn.ui.screen.SettingsScreen
import ru.freeskyvpn.ui.theme.FreeSkyTheme
import ru.freeskyvpn.vpn.FreeSkyVpnService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import ru.freeskyvpn.vpn.VpnStatus

private enum class Screen { Connect, Settings, Account }

/**
 * The only activity.
 *
 * Navigation is one enum rather than a nav graph: there are three screens
 * and no deep links, and a navigation library here would be more moving
 * parts than the thing it navigates.
 */
class MainActivity : ComponentActivity() {

    private val vm: MainViewModel by viewModels()

    /**
     * Android's own VPN consent dialog. It has to be granted once per
     * install, and it is the user's only real guarantee that a VPN cannot
     * start behind their back — so a refusal is a normal outcome to handle,
     * not an error to swallow.
     */
    private val vpnPermission = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) startTunnel() else vm.onVpnPermissionDenied()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            FreeSkyTheme {
                var screen by remember { mutableStateOf(Screen.Connect) }
                val ui by vm.ui.collectAsState()
                val vpn by vm.vpn.collectAsState()

                // Enumerating packages takes hundreds of milliseconds on a
                // full device, so it happens once per visit to the settings
                // screen and off the main thread — not on every
                // recomposition, which is what a plain call here would mean.
                var apps by remember { mutableStateOf(emptyList<AppEntry>()) }
                LaunchedEffect(screen) {
                    if (screen == Screen.Settings) {
                        apps = withContext(Dispatchers.IO) { installedApps() }
                    }
                }

                // A Box so the banner floats over the screen rather than
                // pushing it: two siblings straight inside the theme would
                // draw on top of each other with no layout at all.
                Box(modifier = Modifier.fillMaxSize()) {
                    when (screen) {
                        Screen.Connect -> ConnectScreen(
                            vpn = vpn,
                            busy = ui.busy,
                            watchingAd = ui.watchingAd,
                            hasAccess = ui.hasAccess,
                            remainingLabel = ui.remainingLabel,
                            runningLow = ui.runningLow,
                            isGrace = ui.account?.accessIsGrace == true,
                            splitTunnelActive = vm.storage.splitTunnelEnabled,
                            onToggle = {
                                if (vpn.status == VpnStatus.Connected) stopTunnel()
                                // The ad, if one is needed, happens inside this
                                // call — the user tapped connect, not "watch an
                                // advert then connect".
                                else vm.connect(this@MainActivity, ::requestVpnPermission)
                            },
                            onExtend = { vm.extendAccess(this@MainActivity) },
                            onReportFailure = { vm.reportFailure { restartTunnel() } },
                            onOpenAccount = {
                                vm.loadAccount()
                                screen = Screen.Account
                            },
                            onOpenSettings = { screen = Screen.Settings },
                        )

                        Screen.Settings -> {
                            var splitOn by remember { mutableStateOf(vm.storage.splitTunnelEnabled) }
                            SettingsScreen(
                                splitTunnelEnabled = splitOn,
                                apps = apps,
                                onSplitTunnelChanged = {
                                    vm.storage.splitTunnelEnabled = it
                                    splitOn = it
                                },
                                onAppToggled = { entry, bypass ->
                                    onAppToggled(entry, bypass)
                                    // Updated in place rather than re-enumerating:
                                    // the switch has to move under the finger, and
                                    // a re-read would also resort the list out from
                                    // under it.
                                    apps = apps.map {
                                        if (it.packageName == entry.packageName) it.copy(bypassing = bypass)
                                        else it
                                    }
                                },
                                onBack = { screen = Screen.Connect },
                            )
                        }

                        Screen.Account -> AccountScreen(
                            account = ui.account,
                            remainingLabel = ui.remainingLabel,
                            isGrace = ui.account?.accessIsGrace == true,
                            linkCode = ui.linkCode,
                            onRequestLinkCode = vm::requestLinkCode,
                            onBack = { screen = Screen.Connect },
                        )
                    }

                    ui.error?.let { message ->
                        Banner(
                            text = message,
                            isError = true,
                            onDismiss = vm::dismissError,
                            modifier = Modifier.align(Alignment.TopCenter),
                        )
                    }
                    ui.notice?.let { message ->
                        Banner(
                            text = message,
                            isError = false,
                            onDismiss = vm::dismissNotice,
                            modifier = Modifier.align(Alignment.TopCenter),
                        )
                    }
                }
            }
        }
    }

    // --- the tunnel ------------------------------------------------------

    private fun requestVpnPermission() {
        val intent = VpnService.prepare(this)
        if (intent == null) startTunnel() else vpnPermission.launch(intent)
    }

    private fun startTunnel() {
        startService(Intent(this, FreeSkyVpnService::class.java))
    }

    private fun stopTunnel() {
        startService(
            Intent(this, FreeSkyVpnService::class.java)
                .setAction(FreeSkyVpnService.ACTION_DISCONNECT)
        )
    }

    /** After the head swaps a config, the tunnel has to be rebuilt around it. */
    private fun restartTunnel() {
        stopTunnel()
        requestVpnPermission()
    }

    // --- split tunnel ----------------------------------------------------

    /**
     * Apps the user can put in or out of the tunnel.
     *
     * System apps are filtered out: the list is long enough without them,
     * and excluding a system component from a VPN is not something anyone
     * scrolling this screen is trying to do.
     */
    private fun installedApps(): List<AppEntry> {
        val policy = vm.storage.routingPolicy.directPackages.toSet()
        val excluded = vm.storage.userExcluded
        val included = vm.storage.userIncluded

        return packageManager.getInstalledApplications(PackageManager.GET_META_DATA)
            .asSequence()
            .filter { it.packageName != packageName }
            .filter { (it.flags and ApplicationInfo.FLAG_SYSTEM) == 0 || it.packageName in policy }
            .map { info ->
                val inPolicy = info.packageName in policy
                AppEntry(
                    packageName = info.packageName,
                    label = packageManager.getApplicationLabel(info).toString(),
                    inPolicy = inPolicy,
                    bypassing = info.packageName in excluded ||
                        (inPolicy && info.packageName !in included),
                )
            }
            // Bypassing apps first so the effect of the setting is visible
            // without scrolling, then alphabetically.
            .sortedWith(compareByDescending<AppEntry> { it.bypassing }.thenBy { it.label.lowercase() })
            .toList()
    }

    /**
     * Records a per-app choice.
     *
     * Stored as two explicit sets rather than one, because "the user turned
     * this off" and "the policy never mentioned it" have to stay
     * distinguishable: if the head later adds an app the user already
     * declined, their decision must survive.
     */
    private fun onAppToggled(entry: AppEntry, bypass: Boolean) {
        val storage = vm.storage
        val inPolicy = entry.packageName in storage.routingPolicy.directPackages

        if (bypass) {
            storage.userIncluded = storage.userIncluded - entry.packageName
            if (!inPolicy) storage.userExcluded = storage.userExcluded + entry.packageName
        } else {
            storage.userExcluded = storage.userExcluded - entry.packageName
            if (inPolicy) storage.userIncluded = storage.userIncluded + entry.packageName
        }
    }
}
