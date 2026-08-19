package ru.freeskyvpn.ui

import android.app.Activity
import android.app.Application
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import ru.freeskyvpn.ads.AdGateway
import ru.freeskyvpn.core.Account
import ru.freeskyvpn.core.AccessCountdown
import ru.freeskyvpn.core.LinkCode
import ru.freeskyvpn.data.HeadApi
import ru.freeskyvpn.data.Repository
import ru.freeskyvpn.vpn.VpnStateHolder

data class UiState(
    val busy: Boolean = false,
    /** True while a rewarded ad is on screen or being fetched. */
    val watchingAd: Boolean = false,
    val account: Account? = null,
    val linkCode: LinkCode? = null,
    /** Seconds of access left, counted down locally between /me calls. */
    val secondsRemaining: Int = 0,
    /** Shown to the user; already translated, never a raw exception. */
    val error: String? = null,
    /** Set after a fallback grant, so the app can say why it feels slower. */
    val notice: String? = null,
) {
    val hasAccess: Boolean get() = secondsRemaining > 0
    val runningLow: Boolean get() = AccessCountdown.shouldWarn(secondsRemaining)
    val remainingLabel: String get() = AccessCountdown.format(secondsRemaining)
}

class MainViewModel(app: Application) : AndroidViewModel(app) {

    private val repo = Repository(app)
    private val ads: AdGateway = (app as ru.freeskyvpn.FreeSkyApp).ads

    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui

    val vpn = VpnStateHolder.state
    val storage get() = repo.storage

    /**
     * The server's number, and the moment we received it.
     *
     * Elapsed time is measured with the monotonic clock rather than the wall
     * clock: a phone that corrects its time over the network would otherwise
     * gain or lose hours of access in one step.
     */
    private var serverSeconds = 0
    private var receivedAtMillis = 0L

    init {
        viewModelScope.launch {
            // Both best effort. A first launch offline must still reach the
            // main screen — with a reason, not a spinner that never ends.
            runCatching { repo.ensureRegistered() }
            repo.refreshRoutingPolicy()
            loadAccount()
        }
        viewModelScope.launch { tickWhileRunning() }
        ads.preload()
    }

    /** Keeps the countdown honest without asking the server every second. */
    private suspend fun tickWhileRunning() {
        while (true) {
            delay(1_000)
            if (serverSeconds <= 0) continue
            val left = AccessCountdown.remaining(serverSeconds, SystemClock.elapsedRealtime() - receivedAtMillis)
            if (left != _ui.value.secondsRemaining) {
                _ui.value = _ui.value.copy(secondsRemaining = left)
            }
        }
    }

    fun loadAccount() = launchGuarded {
        applyAccount(repo.account())
    }

    private fun applyAccount(account: Account) {
        serverSeconds = account.accessSecondsRemaining
        receivedAtMillis = SystemClock.elapsedRealtime()
        _ui.value = _ui.value.copy(
            account = account,
            secondsRemaining = account.accessSecondsRemaining,
        )
    }

    /**
     * The connect button.
     *
     * Access is checked first because the whole product is gated on it: no
     * watched ad, no hour, no tunnel. When there is time left, connecting
     * does not touch the ad path at all — a reconnect mid-hour must not
     * cost the user another video.
     */
    fun connect(activity: Activity, onReady: () -> Unit) {
        if (_ui.value.hasAccess) {
            fetchConfigAndStart(onReady)
        } else {
            watchAdThen(activity) { fetchConfigAndStart(onReady) }
        }
    }

    /** Buy the next hour without disconnecting — offered as time runs low. */
    fun extendAccess(activity: Activity) = watchAdThen(activity) {}

    private fun fetchConfigAndStart(onReady: () -> Unit) = launchGuarded {
        if (repo.storage.lastVlessUrl.isNullOrBlank()) {
            repo.fetchConfig()
        }
        repo.refreshRoutingPolicy()
        onReady()
    }

    /**
     * Show an ad, claim the hour, then run [next].
     *
     * The failure branch is the one that matters. If no ad can be shown —
     * no fill, no network, no SDK — the user is not left unable to connect:
     * the head grants a short fallback on the lower-priority class instead.
     * Treating "the ad network is down" as "the VPN is down" would hand our
     * availability to somebody else's.
     */
    private fun watchAdThen(activity: Activity, next: () -> Unit) {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(watchingAd = true, error = null, notice = null)
            try {
                val nonce = repo.prepareAd()

                when (val outcome = ads.show(activity)) {
                    is AdGateway.Outcome.Rewarded -> {
                        applyAccount(repo.completeAd(nonce))
                        next()
                    }

                    is AdGateway.Outcome.Skipped -> {
                        _ui.value = _ui.value.copy(
                            error = "Ролик нужно досмотреть до конца — это и оплачивает сервер."
                        )
                    }

                    is AdGateway.Outcome.Unavailable -> takeFallback(outcome.reason, next)
                }
            } catch (e: HeadApi.HttpError) {
                _ui.value = _ui.value.copy(error = messageFor(e))
            } catch (e: Exception) {
                _ui.value = _ui.value.copy(
                    error = "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз."
                )
            } finally {
                _ui.value = _ui.value.copy(watchingAd = false)
                ads.preload()
            }
        }
    }

    private suspend fun takeFallback(reason: String, next: () -> Unit) {
        try {
            applyAccount(repo.accessWithoutAd())
            _ui.value = _ui.value.copy(
                notice = "Рекламу показать не удалось ($reason). " +
                    "Выдали короткий доступ — скорость будет ниже обычной."
            )
            next()
        } catch (e: HeadApi.HttpError) {
            // 429 is the rate limit on the fallback, and it is the honest
            // answer: it exists so it cannot become the way to skip the ad.
            _ui.value = _ui.value.copy(
                error = if (e.code == 429) {
                    "Запасной доступ уже выдавался недавно. Попробуйте посмотреть ролик ещё раз."
                } else {
                    messageFor(e)
                }
            )
        }
    }

    fun requestLinkCode() = launchGuarded {
        _ui.value = _ui.value.copy(linkCode = repo.startLink())
    }

    /** The "не работает" button: the head swaps the config, then we restart. */
    fun reportFailure(onReady: () -> Unit) = launchGuarded {
        repo.reportFailure()
        onReady()
    }

    fun dismissError() { _ui.value = _ui.value.copy(error = null) }
    fun dismissNotice() { _ui.value = _ui.value.copy(notice = null) }

    fun onVpnPermissionDenied() {
        _ui.value = _ui.value.copy(
            error = "Без разрешения на VPN подключиться нельзя — Android требует его для любого туннеля."
        )
    }

    private fun launchGuarded(block: suspend () -> Unit) {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(busy = true, error = null)
            try {
                block()
            } catch (e: HeadApi.HttpError) {
                _ui.value = _ui.value.copy(error = messageFor(e))
            } catch (e: Exception) {
                _ui.value = _ui.value.copy(
                    error = "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз."
                )
            } finally {
                _ui.value = _ui.value.copy(busy = false)
            }
        }
    }

    /**
     * Turns a status code into something worth reading.
     *
     * The head's `detail` is written for an operator, not for someone
     * holding a phone, so the common cases get their own wording and only
     * the genuinely unexpected fall through to a code.
     */
    private fun messageFor(e: HeadApi.HttpError): String = when (e.code) {
        400 -> "Награда не засчиталась. Попробуйте посмотреть ролик ещё раз."
        401 -> "Сессия устарела. Переустановите приложение — аккаунт сохранится, если он привязан к Telegram."
        402 -> "Время закончилось. Посмотрите ролик, чтобы открыть следующий час."
        403 -> "Аккаунт заблокирован."
        429 -> "Слишком часто. Подождите полминуты."
        503 -> "Сейчас нет свободных серверов. Мы уже разбираемся — попробуйте через несколько минут."
        else -> "Что-то пошло не так (${e.code}). Попробуйте ещё раз."
    }
}
