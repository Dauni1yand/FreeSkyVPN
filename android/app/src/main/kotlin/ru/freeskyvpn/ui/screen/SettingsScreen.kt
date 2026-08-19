package ru.freeskyvpn.ui.screen

import androidx.compose.foundation.background
import androidx.compose.foundation.text.BasicTextField
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
import androidx.compose.ui.graphics.SolidColor
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
    /** Non-null only in debug builds; see [DebugServerCard]. */
    apiOverride: String? = null,
    onSplitTunnelChanged: (Boolean) -> Unit,
    onAppToggled: (AppEntry, Boolean) -> Unit,
    onApiOverrideChanged: (String) -> Unit = {},
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

        if (apiOverride != null) {
            DebugServerCard(value = apiOverride, onChange = onApiOverrideChanged)
            Spacer(Modifier.height(Metrics.itemSpacing))
        }

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

/**
 * Points a debug build at a different head without rebuilding.
 *
 * Useful precisely while testing, when the head might be a laptop one
 * minute and the real server the next. Shown only in debug builds — the
 * caller passes null otherwise — because a release build that could be
 * aimed anywhere by anyone would be a way to hand somebody's token to a
 * server we do not run.
 *
 * Empty means "use the addresses the build shipped with".
 */
@Composable
private fun DebugServerCard(value: String, onChange: (String) -> Unit) {
    Column {
        SectionCaption("Сервер (только в debug)")
        Card {
            Column(modifier = Modifier.padding(14.dp)) {
                PlainField(
                    value = value,
                    placeholder = "https://api.вашдомен.ru",
                    onValueChange = onChange,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "Пусто — используются адреса из сборки. " +
                        "Открытый HTTP разрешён только к локальной сети.",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}


@Composable
private fun SearchField(query: String, onQueryChange: (String) -> Unit) =
    PlainField(value = query, placeholder = "Поиск", onValueChange = onQueryChange)


/** One unstyled text field, so the search box and the server box match. */
@Composable
private fun PlainField(value: String, placeholder: String, onValueChange: (String) -> Unit) {
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        singleLine = true,
        textStyle = MaterialTheme.typography.bodyLarge.copy(
            color = MaterialTheme.colorScheme.onSurface
        ),
        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        decorationBox = { inner ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .padding(horizontal = 12.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (value.isEmpty()) {
                    Text(
                        placeholder,
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
