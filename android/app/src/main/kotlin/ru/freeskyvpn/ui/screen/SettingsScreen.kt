package ru.freeskyvpn.ui.screen

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import ru.freeskyvpn.ui.theme.Metrics
import ru.freeskyvpn.ui.theme.SystemGreen

/** One installed app, as the split-tunnel list shows it. */
data class AppEntry(
    val packageName: String,
    val label: String,
    /** True when the shipped policy already routes this app around the VPN. */
    val inPolicy: Boolean,
    val bypassing: Boolean,
)

/**
 * Settings, which in this app means the split tunnel and nothing else.
 *
 * The master switch is on by default and is genuinely a switch, not a
 * display: several Russian services degrade or refuse foreign addresses, so
 * bypassing them is the right default — but a user who is abroad and wants
 * everything tunnelled has to be able to say so.
 */
@Composable
fun SettingsScreen(
    splitTunnelEnabled: Boolean,
    apps: List<AppEntry>,
    onSplitTunnelChanged: (Boolean) -> Unit,
    onAppToggled: (AppEntry, Boolean) -> Unit,
    onBack: () -> Unit,
) {
    var query by remember { mutableStateOf("") }
    val visible = remember(apps, query) {
        if (query.isBlank()) apps
        else apps.filter { it.label.contains(query, ignoreCase = true) ||
                           it.packageName.contains(query, ignoreCase = true) }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(horizontal = Metrics.screenPadding),
    ) {
        ScreenHeader(title = "Настройки", onBack = onBack)

        Card {
            SettingRow(
                title = "Российские сервисы напрямую",
                subtitle = "Банки, Госуслуги, маркетплейсы и сайты в зоне .ru " +
                    "идут в обход VPN. Многие из них не работают с зарубежных адресов.",
                checked = splitTunnelEnabled,
                onCheckedChange = onSplitTunnelChanged,
            )
        }

        if (splitTunnelEnabled) {
            Spacer(Modifier.height(Metrics.itemSpacing))
            SectionCaption("Приложения в обход VPN")

            SearchField(query = query, onQueryChange = { query = it })

            Spacer(Modifier.height(8.dp))
            Card {
                LazyColumn(modifier = Modifier.heightIn(max = 460.dp)) {
                    items(visible, key = { it.packageName }) { entry ->
                        AppRow(entry = entry, onToggled = onAppToggled)
                        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f))
                    }
                }
            }

            Spacer(Modifier.height(8.dp))
            Text(
                text = "Отмеченные приложения не видят VPN вообще. Это нужно тем, " +
                    "что отказываются работать при включённом туннеле — обычно банковским.",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
            )
        }
    }
}

@Composable
private fun AppRow(entry: AppEntry, onToggled: (AppEntry, Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = Metrics.rowHeight)
            .padding(horizontal = 14.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(entry.label, style = MaterialTheme.typography.bodyLarge)
            if (entry.inPolicy) {
                // Says where the default came from, so a user who turns it
                // off knows they are overriding something deliberate.
                Text(
                    "рекомендовано",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Switch(
            checked = entry.bypassing,
            onCheckedChange = { onToggled(entry, it) },
            colors = SwitchDefaults.colors(checkedTrackColor = SystemGreen),
        )
    }
}

@Composable
private fun SettingRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Spacer(Modifier.height(4.dp))
            Text(
                subtitle,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(checkedTrackColor = SystemGreen),
        )
    }
}

@Composable
private fun SearchField(query: String, onQueryChange: (String) -> Unit) {
    androidx.compose.foundation.text.BasicTextField(
        value = query,
        onValueChange = onQueryChange,
        singleLine = true,
        textStyle = MaterialTheme.typography.bodyLarge.copy(
            color = MaterialTheme.colorScheme.onSurface
        ),
        cursorBrush = androidx.compose.ui.graphics.SolidColor(MaterialTheme.colorScheme.primary),
        decorationBox = { inner ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .background(MaterialTheme.colorScheme.surface)
                    .padding(horizontal = 12.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (query.isEmpty()) {
                    Text(
                        "Поиск",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                inner()
            }
        },
        modifier = Modifier.fillMaxWidth(),
    )
}

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
