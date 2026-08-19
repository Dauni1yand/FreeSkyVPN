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
import ru.freeskyvpn.ui.theme.SystemOrange
import ru.freeskyvpn.vpn.VpnSnapshot
import ru.freeskyvpn.vpn.VpnStatus

/**
 * The whole product, on one screen.
 *
 * There is no server list and no country picker — choosing a node is the
 * head's job. What is left is one button, a line of status, and the two
 * escape hatches: "не работает" and the account.
 *
 * The button carries one more thing now. The service is funded entirely by
 * advertising, so with no time bought it opens the duration picker and the
 * ads run as part of the same gesture — the user tapped connect, not "watch
 * adverts, then connect". With time already bought it simply connects: they
 * paid for those minutes once, and charging again for a reconnect would be
 * charging twice for the same thing.
 */
@Composable
fun ConnectScreen(
    vpn: VpnSnapshot,
    busy: Boolean,
    watchingAd: Boolean,
    hasAccess: Boolean,
    remainingLabel: String,
    runningLow: Boolean,
    isGrace: Boolean,
    splitTunnelActive: Boolean,
    onToggle: () -> Unit,
    onExtend: () -> Unit,
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

        PowerButton(
            status = vpn.status,
            busy = busy || watchingAd,
            hasAccess = hasAccess,
            onClick = onToggle,
        )

        Spacer(Modifier.height(28.dp))
        StatusLine(vpn = vpn, hasAccess = hasAccess, watchingAd = watchingAd)

        if (hasAccess) {
            Spacer(Modifier.height(14.dp))
            RemainingLine(remainingLabel = remainingLabel, runningLow = runningLow, isGrace = isGrace)
        }

        Spacer(Modifier.weight(1f))

        // Only once the time is nearly up. Offering it at fifty minutes
        // would be asking for attention we have already been paid for.
        if (runningLow) {
            PrimaryButton(text = "Продлить", onClick = onExtend)
            Spacer(Modifier.height(Metrics.itemSpacing))
        }

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
private fun PowerButton(
    status: VpnStatus,
    busy: Boolean,
    hasAccess: Boolean,
    onClick: () -> Unit,
) {
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
                // Three states, not two: without a bought hour the button's
                // job is to start an ad, and saying "Выкл" there would hide
                // what tapping it actually does.
                text = when {
                    connected -> "Вкл"
                    // Tapping here opens the duration picker, so the label
                    // has to promise a choice rather than an advert.
                    !hasAccess -> "Включить"
                    else -> "Выкл"
                },
                style = if (connected || hasAccess) MaterialTheme.typography.headlineLarge
                        else MaterialTheme.typography.titleLarge,
                textAlign = TextAlign.Center,
                color = if (connected) Color.Black.copy(alpha = 0.85f)
                        else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun StatusLine(vpn: VpnSnapshot, hasAccess: Boolean, watchingAd: Boolean) {
    val headline = when {
        watchingAd -> "Реклама"
        vpn.status == VpnStatus.Connected -> "Защищено"
        vpn.status == VpnStatus.Connecting -> "Подключаюсь"
        vpn.status == VpnStatus.Failed -> "Не подключилось"
        else -> "Отключено"
    }
    val detail = when {
        watchingAd -> "Один ролик открывает час доступа"
        vpn.status == VpnStatus.Connected -> vpn.nodeCountry?.uppercase()?.let { "Сервер $it" }
        vpn.status == VpnStatus.Failed -> vpn.message
        vpn.status == VpnStatus.Disconnected && !hasAccess ->
            "Выберите время — его оплачивает реклама"
        vpn.status == VpnStatus.Disconnected -> "Нажмите, чтобы подключиться"
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
private fun RemainingLine(remainingLabel: String, runningLow: Boolean, isGrace: Boolean) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = "Осталось $remainingLabel",
            style = MaterialTheme.typography.titleMedium,
            color = when {
                runningLow -> SystemOrange
                isGrace -> SystemOrange
                else -> SystemGreen
            },
        )
        if (isGrace) {
            Spacer(Modifier.height(4.dp))
            Text(
                text = "Запасной доступ: рекламу показать не удалось",
                style = MaterialTheme.typography.labelMedium,
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
