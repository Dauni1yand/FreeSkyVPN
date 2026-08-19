package ru.freeskyvpn.ui.screen

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import ru.freeskyvpn.core.Account
import ru.freeskyvpn.core.LinkCode
import ru.freeskyvpn.ui.theme.Metrics
import ru.freeskyvpn.ui.theme.SystemGreen
import ru.freeskyvpn.ui.theme.SystemOrange

/**
 * The account: how much time is left, and how not to lose it.
 *
 * There is no subscription section because there is no subscription. The
 * service is paid for with attention — one ad, one hour — so what this
 * screen has to explain is where the time came from and where it went.
 *
 * The link section is the important half. Registration is anonymous so that
 * nothing stands between installing the app and connecting; the cost is
 * that the account lives on this phone only until it is linked. That is
 * said plainly rather than buried.
 */
@Composable
fun AccountScreen(
    account: Account?,
    remainingLabel: String,
    isGrace: Boolean,
    linkCode: LinkCode?,
    onRequestLinkCode: () -> Unit,
    onBack: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(horizontal = Metrics.screenPadding),
    ) {
        ScreenHeader(title = "Аккаунт", onBack = onBack)

        SectionCaption("Доступ")
        Card {
            InfoRow(
                label = "Осталось",
                value = if (account?.accessActive == true) remainingLabel else "нет",
                valueColor = when {
                    account?.accessActive != true -> null
                    isGrace -> SystemOrange
                    else -> SystemGreen
                },
            )
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f))
            InfoRow(
                label = "Один ролик даёт",
                value = "${account?.adRewardMinutes ?: 60} мин",
            )
        }

        Spacer(Modifier.height(8.dp))
        if (isGrace) {
            Caption(
                "Сейчас работает запасной доступ: рекламу показать не удалось. " +
                    "Он короче обычного и идёт с меньшим приоритетом — " +
                    "посмотрите ролик, когда получится."
            )
        } else {
            Caption(
                "Сервис бесплатный и живёт на рекламе. Один досмотренный ролик " +
                    "открывает час — этим и оплачиваются серверы."
            )
        }

        Spacer(Modifier.height(28.dp))
        SectionCaption("Восстановление доступа")

        if (account?.telegramLinked == true) {
            Card {
                InfoRow(label = "Telegram", value = "Привязан", valueColor = SystemGreen)
            }
            Spacer(Modifier.height(8.dp))
            Caption("На новом телефоне установите приложение и пришлите боту новый код.")
        } else {
            Card {
                Column(modifier = Modifier.padding(14.dp)) {
                    Text(
                        "Аккаунт живёт только на этом телефоне",
                        style = MaterialTheme.typography.bodyLarge,
                    )
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Мы не спрашивали ни почту, ни телефон — чтобы вы могли " +
                            "подключиться сразу. Обратная сторона: потеряете телефон — " +
                            "потеряете аккаунт. Привязка к Telegram это чинит.",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Spacer(Modifier.height(Metrics.itemSpacing))

            if (linkCode == null) {
                PrimaryButton(text = "Привязать к Telegram", onClick = onRequestLinkCode)
            } else {
                LinkCodeCard(linkCode)
            }
        }

        Spacer(Modifier.height(28.dp))
        account?.userId?.let {
            Caption("ID: ${it.take(8)}… — назовите его в поддержке, если понадобится.")
        }
    }
}

@Composable
private fun LinkCodeCard(code: LinkCode) {
    Card {
        Column(
            modifier = Modifier.fillMaxWidth().padding(18.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = code.code,
                style = MaterialTheme.typography.displayLarge,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(10.dp))
            Text(
                text = buildString {
                    append("Отправьте боту")
                    code.botUsername?.let { append(" @$it") }
                    append(" сообщение:")
                },
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(6.dp))
            Text("/link ${code.code}", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(10.dp))
            Text(
                text = "Код работает 10 минут.",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String, valueColor: Color? = null) {
    Row(
        modifier = Modifier.fillMaxWidth().height(Metrics.rowHeight).padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        Text(
            text = value,
            style = MaterialTheme.typography.bodyLarge,
            color = valueColor ?: MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun Caption(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 4.dp),
    )
}
