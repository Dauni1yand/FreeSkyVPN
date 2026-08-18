package ru.freeskyvpn.ui.screen

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import ru.freeskyvpn.ui.theme.Metrics
import ru.freeskyvpn.ui.theme.SystemGreen
import ru.freeskyvpn.vpn.VpnSnapshot
import ru.freeskyvpn.vpn.VpnStatus

/**
 * The whole product, on one screen.
 *
 * There is no server list and no country picker — choosing a node is the
 * head's job. What is left is one button that is either on or off, a line of
 * status underneath it, and the two escape hatches: "не работает" and the
 * account. Anything else added here should have to justify itself against
 * that.
 */
@Composable
fun ConnectScreen(
    vpn: VpnSnapshot,
    busy: Boolean,
    splitTunnelActive: Boolean,
    onToggle: () -> Unit,
    onReportFailure: () -> Unit,
    onOpenAccount: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(Metrics.screenPadding),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        TopRow(onOpenAccount = onOpenAccount, onOpenSettings = onOpenSettings)

        Spacer(Modifier.weight(1f))

        PowerButton(status = vpn.status, busy = busy, onClick = onToggle)

        Spacer(Modifier.height(28.dp))
        StatusLine(vpn)

        Spacer(Modifier.weight(1f))

        if (splitTunnelActive) {
            SplitTunnelHint(vpn.bypassedApps)
            Spacer(Modifier.height(Metrics.itemSpacing))
        }

        // Only offered once there is something to report about. Showing it
        // while disconnected would invite people to burn inbounds that were
        // never in use.
        if (vpn.status == VpnStatus.Connected || vpn.status == VpnStatus.Failed) {
            SecondaryButton(text = "Не работает", onClick = onReportFailure)
        }
    }
}

@Composable
private fun TopRow(onOpenAccount: () -> Unit, onOpenSettings: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconTextButton("Аккаунт", onOpenAccount)
        IconTextButton("Настройки", onOpenSettings)
    }
}

@Composable
private fun IconTextButton(text: String, onClick: () -> Unit) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 8.dp),
    )
}

/**
 * The button.
 *
 * A filled circle rather than a switch: it is the only control on the
 * screen, so it should read as the screen's subject. Colour carries the
 * state — grey off, green on — with the label repeating it in words, because
 * colour alone is not a state anyone should have to interpret.
 */
@Composable
private fun PowerButton(status: VpnStatus, busy: Boolean, onClick: () -> Unit) {
    val connected = status == VpnStatus.Connected
    val working = busy || status == VpnStatus.Connecting

    val target = when {
        connected -> SystemGreen
        status == VpnStatus.Failed -> MaterialTheme.colorScheme.error
        else -> MaterialTheme.colorScheme.surfaceVariant
    }
    val color by animateColorAsState(target, tween(320), label = "power")

    // A slow breath while connecting. Not a spinner on top of the button —
    // the button itself is what is busy.
    val pulse = rememberInfiniteTransition(label = "pulse")
    val scale by pulse.animateFloat(
        initialValue = 1f,
        targetValue = if (working) 1.04f else 1f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse),
        label = "scale",
    )

    Box(
        modifier = Modifier
            .size(184.dp)
            .scale(scale)
            .clip(CircleShape)
            .background(color)
            .clickable(enabled = !working, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        if (working) {
            CircularProgressIndicator(
                color = Color.White.copy(alpha = 0.9f),
                strokeWidth = 2.5.dp,
                modifier = Modifier.size(30.dp),
            )
        } else {
            Text(
                text = if (connected) "Вкл" else "Выкл",
                style = MaterialTheme.typography.headlineLarge,
                color = if (connected) Color.Black.copy(alpha = 0.85f)
                        else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun StatusLine(vpn: VpnSnapshot) {
    val headline = when (vpn.status) {
        VpnStatus.Connected -> "Защищено"
        VpnStatus.Connecting -> "Подключаюсь"
        VpnStatus.Failed -> "Не подключилось"
        VpnStatus.Disconnected -> "Отключено"
    }
    val detail = when (vpn.status) {
        VpnStatus.Connected -> vpn.nodeCountry?.uppercase()?.let { "Сервер $it" }
        VpnStatus.Failed -> vpn.message
        VpnStatus.Disconnected -> "Нажмите, чтобы подключиться"
        else -> null
    }

    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(headline, style = MaterialTheme.typography.titleLarge)
        detail?.let {
            Spacer(Modifier.height(6.dp))
            Text(
                text = it,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun SplitTunnelHint(bypassedApps: Int) {
    val suffix = if (bypassedApps > 0) " · $bypassedApps ${plural(bypassedApps)} в обход" else ""
    Text(
        text = "Российские сайты и сервисы идут напрямую$suffix",
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.75f),
        textAlign = TextAlign.Center,
        modifier = Modifier.alpha(0.9f),
    )
}

private fun plural(n: Int): String = when {
    n % 10 == 1 && n % 100 != 11 -> "приложение"
    n % 10 in 2..4 && n % 100 !in 12..14 -> "приложения"
    else -> "приложений"
}

@Composable
internal fun SecondaryButton(text: String, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(Metrics.rowHeight)
            .clip(RoundedCornerShape(Metrics.cardCorner))
            .background(MaterialTheme.colorScheme.surface)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge)
    }
}
