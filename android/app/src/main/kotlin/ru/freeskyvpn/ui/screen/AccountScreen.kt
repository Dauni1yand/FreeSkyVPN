package ru.freeskyvpn.ui.screen

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import ru.freeskyvpn.core.Account
import ru.freeskyvpn.core.LinkCode
import ru.freeskyvpn.ui.theme.Metrics
import ru.freeskyvpn.ui.theme.SystemGreen

/**
 * The account, the subscription and the way to not lose either.
 *
 * The link section is the important part of this screen. Registration is
 * anonymous so that nothing stands between installing the app and
 * connecting — the cost is that the account lives only on this phone until
 * it is linked. That is stated plainly here rather than buried, because a
 * user who loses their subscription with their phone will not accept
 * "it was in the settings" as an answer.
 */
@Composable
fun AccountScreen(
    account: Account?,
    linkCode: LinkCode?,
    onRequestLinkCode: () -> Unit,
    onStartTrial: () -> Unit,
    onBack: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(horizontal = Metrics.screenPadding),
    ) {
        ScreenHeader(title = "Аккаунт", onBack = onBack)

        SectionCaption("Подписка")
        Card {
            InfoRow(
                label = "Статус",
                value = when {
                    account == null -> "…"
                    account.subscriptionActive && account.subscriptionType == "trial" -> "Пробный период"
                    account.subscriptionActive -> "Активна"
                    else -> "Бесплатный доступ"
                },
                valueColor = if (account?.subscriptionActive == true) SystemGreen else null,
            )
            account?.expiresAt?.let {
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f))
                InfoRow(label = "Действует до", value = it.take(10))
            }
        }

        if (account?.trialAvailable == true) {
            Spacer(Modifier.height(Metrics.itemSpacing))
            PrimaryButton(text = "Попробовать 7 дней бесплатно", onClick = onStartTrial)
        }

        // Shown only when the server actually has a payment provider. An
        // offer that cannot complete is worse than no offer.
        if (account?.paymentsAvailable == true && !account.subscriptionActive) {
            Spacer(Modifier.height(Metrics.itemSpacing))
            Text(
                text = "Оформить подписку можно в Telegram-боте.",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp),
            )
        }

        Spacer(Modifier.height(28.dp))
        SectionCaption("Восстановление доступа")

        if (account?.telegramLinked == true) {
            Card {
                InfoRow(label = "Telegram", value = "Привязан", valueColor = SystemGreen)
            }
            Spacer(Modifier.height(8.dp))
            Caption(
                "Аккаунт восстановится на новом телефоне: установите приложение " +
                    "и пришлите боту новый код."
            )
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
                            "подключиться сразу. Обратная сторона: если потеряете " +
                            "телефон, подписка потеряется вместе с ним. Привязка к " +
                            "Telegram это чинит.",
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
            Text(
                text = "/link ${code.code}",
                style = MaterialTheme.typography.titleMedium,
            )
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
private fun InfoRow(label: String, value: String, valueColor: androidx.compose.ui.graphics.Color? = null) {
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

@Composable
internal fun PrimaryButton(text: String, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(Metrics.rowHeight)
            .clip(RoundedCornerShape(Metrics.cardCorner))
            .background(MaterialTheme.colorScheme.primary)
            .clickable(onClick = onClick),
        horizontalArrangement = androidx.compose.foundation.layout.Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = androidx.compose.ui.graphics.Color.White)
    }
}
