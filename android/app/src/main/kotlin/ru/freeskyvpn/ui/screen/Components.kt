package ru.freeskyvpn.ui.screen

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import ru.freeskyvpn.ui.theme.Metrics

/**
 * The handful of pieces every screen is built from.
 *
 * Gathered here rather than left wherever they were first needed: they were
 * scattered across three screens, and a shared control defined inside the
 * file that happened to use it first is a shared control nobody can find.
 */

/** A grouped block on black, the way iOS lays out settings. */
@Composable
internal fun Card(content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(Metrics.cardCorner))
            .background(MaterialTheme.colorScheme.surface),
        content = content,
    )
}

@Composable
internal fun SectionCaption(text: String) {
    Text(
        text = text.uppercase(),
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(start = 4.dp, bottom = 8.dp, top = 4.dp),
    )
}

@Composable
internal fun ScreenHeader(title: String, onBack: () -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            text = "‹ Назад",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier
                .clip(RoundedCornerShape(8.dp))
                .clickable(onClick = onBack)
                .padding(vertical = 10.dp, horizontal = 4.dp),
        )
        Spacer(Modifier.height(4.dp))
        Text(title, style = MaterialTheme.typography.displayLarge)
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
internal fun PrimaryButton(text: String, enabled: Boolean = true, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(Metrics.rowHeight)
            .clip(RoundedCornerShape(Metrics.cardCorner))
            .background(
                if (enabled) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.surfaceVariant
            )
            .clickable(enabled = enabled, onClick = onClick),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelLarge,
            color = if (enabled) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
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

/**
 * A message floating over whatever screen is showing.
 *
 * Errors and notices used to be computed and never displayed, which is the
 * worst of both: the code looks like it handles a failure and the user sees
 * nothing. This is deliberately dismissible rather than timed — a message
 * that explains why the connection is slower should stay until it is read.
 */
@Composable
internal fun Banner(
    text: String,
    isError: Boolean,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(Metrics.screenPadding)
            .clip(RoundedCornerShape(Metrics.cardCorner))
            .background(
                if (isError) MaterialTheme.colorScheme.error.copy(alpha = 0.18f)
                else MaterialTheme.colorScheme.surfaceVariant
            )
            .clickable(onClick = onDismiss)
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.height(0.dp))
        Text(
            text = "✕",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 10.dp),
        )
    }
}
