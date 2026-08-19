package ru.freeskyvpn.ui.screen

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import ru.freeskyvpn.core.AccessPackage
import ru.freeskyvpn.core.PackageOffer
import ru.freeskyvpn.ui.theme.Metrics

/**
 * "На сколько включить?" — the sheet that opens when the connect button is
 * tapped with no time left.
 *
 * Each option states what it costs before it is chosen. Someone deciding
 * between fifteen minutes and two hours is deciding how many videos to sit
 * through, and hiding that until the video starts is how an app earns a
 * one-star review.
 *
 * It is dismissible. A picker that cannot be closed would trap a user who
 * opened it by accident on a screen whose only exit is watching an advert.
 */
@Composable
fun DurationPicker(
    packages: List<AccessPackage>,
    onPick: (AccessPackage) -> Unit,
    onDismiss: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            // Tapping the dimmed area closes it, the way a sheet should.
            .background(Color.Black.copy(alpha = 0.72f))
            .clickable(onClick = onDismiss),
        contentAlignment = Alignment.BottomCenter,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Metrics.screenPadding)
                .clip(RoundedCornerShape(Metrics.cardCorner))
                .background(MaterialTheme.colorScheme.surface)
                // Swallows the tap so choosing an option does not also
                // dismiss the sheet underneath it.
                .clickable(enabled = false) {}
                .padding(vertical = 6.dp),
        ) {
            Text(
                text = "На сколько включить?",
                style = MaterialTheme.typography.titleLarge,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth().padding(vertical = 14.dp),
            )
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f))

            packages.forEachIndexed { index, pkg ->
                DurationRow(pkg = pkg, onClick = { onPick(pkg) })
                if (index < packages.lastIndex) {
                    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f))
                }
            }

            Spacer(Modifier.height(6.dp))
            Text(
                text = "Реклама — единственное, чем оплачиваются серверы. Подписки нет.",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            )
        }
    }
}

@Composable
private fun DurationRow(pkg: AccessPackage, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(pkg.label, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(3.dp))
            Text(
                text = PackageOffer.cost(pkg),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(
            text = "›",
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * Shown while the videos are actually playing.
 *
 * Carries the "2 из 2" counter, because a package that needs two videos
 * looks broken without it: the first ad ends, the screen goes back to the
 * app for a moment, and another one starts.
 */
@Composable
fun AdOverlay(progress: Pair<Int, Int>?) {
    Box(
        modifier = Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.85f)),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Реклама", style = MaterialTheme.typography.headlineLarge)
            progress?.let { (current, total) ->
                if (total > 1) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = "$current из $total",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}
