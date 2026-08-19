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
import ru.freeskyvpn.core.AccessCountdown
import ru.freeskyvpn.core.AccessPackage
import ru.freeskyvpn.core.Account
import ru.freeskyvpn.core.LinkCode
import ru.freeskyvpn.core.PackageOffer
import ru.freeskyvpn.data.HeadApi
import ru.freeskyvpn.data.Repository
import ru.freeskyvpn.vpn.VpnStateHolder

data class UiState(
    val busy: Boolean = false,
    /** True while an ad is on screen or being fetched. */
    val watchingAd: Boolean = false,
    /** Which video of the package is playing, for "2 из 2". */
    val adProgress: Pair<Int, Int>? = null,
    /** True while the duration picker is up. */
    val choosingDuration: Boolean = false,
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
    val packages: List<AccessPackage> get() = PackageOffer.offered(account?.packages.orEmpty())
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

    /**
     * Keeps the countdown honest without asking the server every second,
     * and stops the tunnel the moment it reaches zero.
     *
     * The disconnect here is courtesy, not enforcement. A modified build
     * simply would not do it — what actually ends the session is the head
     * removing the client's UUID from the node's config
     * (`services/enforcement.py`). Doing it locally too means the user sees
     * the VPN stop when their time runs out rather than at some point in
     * the next minute, which is the difference between a product that
     * behaves as described and one that seems to lag.
     */
    private suspend fun tickWhileRunning() {
        while (true) {
            delay(1_000)
            if (serverSeconds <= 0) continue

            val left = AccessCountdown.remaining(
                serverSeconds, SystemClock.elapsedRealtime() - receivedAtMillis
            )
            if (left == _ui.value.secondsRemaining) continue
            _ui.value = _ui.value.copy(secondsRemaining = left)

            if (left == 0) {
                serverSeconds = 0
                onExpired?.invoke()
                _ui.value = _ui.value.copy(
                    notice = "Время закончилось, VPN отключён. " +
                        "Выберите, на сколько включить снова."
                )
            }
        }
    }

    /**
     * Called when the bought time runs out, so the activity can stop the
     * tunnel. A callback rather than the ViewModel touching the service
     * directly: starting and stopping a VpnService needs the Activity's
     * permission context, which a ViewModel has no business holding.
     */
    var onExpired: (() -> Unit)? = null

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
     * Time already bought reconnects straight away, with no ad and no
     * picker. That is the point of selling time rather than sessions:
     * someone who turned the VPN off with forty minutes left has already
     * paid for those forty minutes, and charging them again would be taking
     * payment twice for the same thing.
     *
     * With nothing left, the picker opens and the ads run as part of the
     * same gesture — the user tapped connect, not "watch adverts".
     */
    fun connect(onReady: () -> Unit) {
        if (_ui.value.hasAccess) {
            fetchConfigAndStart(onReady)
        } else {
            _ui.value = _ui.value.copy(choosingDuration = true, error = null)
        }
    }

    /** Open the picker deliberately — used to top up before time runs out. */
    fun chooseDuration() {
        _ui.value = _ui.value.copy(choosingDuration = true, error = null)
    }

    fun dismissDurationPicker() {
        _ui.value = _ui.value.copy(choosingDuration = false)
    }

    /**
     * A duration was picked: run its ads, then connect if we are not already.
     *
     * [andConnect] is false when topping up mid-session — the tunnel is
     * already running and rebuilding it would drop the user's connections
     * for no reason.
     */
    fun buyAccess(activity: Activity, pkg: AccessPackage, andConnect: Boolean, onReady: () -> Unit) {
        _ui.value = _ui.value.copy(choosingDuration = false)
        watchAdsThen(activity, pkg) { if (andConnect) fetchConfigAndStart(onReady) }
    }

    private fun fetchConfigAndStart(onReady: () -> Unit) = launchGuarded {
        if (repo.storage.lastVlessUrl.isNullOrBlank()) {
            repo.fetchConfig()
        }
        repo.refreshRoutingPolicy()
        onReady()
    }

    /**
     * Run a package's ads one after another, crediting each as it finishes.
     *
     * Each view is claimed the moment it ends rather than at the end of the
     * package, because the server grants per view: if the user closes the
     * app between two videos they keep the hour they earned. Batching the
     * claims would mean taking a view and giving nothing for it.
     *
     * The failure branch is the one that matters. If no ad can be shown —
     * no fill, no network, no SDK — the user is not left unable to connect:
     * the head grants a short fallback on the lower-priority class instead.
     * Treating "the ad network is down" as "the VPN is down" would hand our
     * availability to somebody else's.
     */
    private fun watchAdsThen(activity: Activity, pkg: AccessPackage, next: () -> Unit) {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(watchingAd = true, error = null, notice = null)
            val kind =
                if (pkg.isSkippable) AdGateway.Kind.Interstitial else AdGateway.Kind.Rewarded

            try {
                val ticket = repo.prepareAd(pkg.code)
                var watched = 0

                while (watched < ticket.viewsRequired) {
                    _ui.value = _ui.value.copy(adProgress = (watched + 1) to ticket.viewsRequired)

                    when (val outcome = ads.show(activity, kind)) {
                        is AdGateway.Outcome.Rewarded -> {
                            val progress = repo.completeAd(ticket.nonce)
                            applyAccount(progress.account)
                            watched = progress.viewsDone
                        }

                        is AdGateway.Outcome.Skipped -> {
                            // Only a rewarded ad can be skipped short; the
                            // time already earned in this package stays.
                            _ui.value = _ui.value.copy(
                                error = if (watched == 0) {
                                    "Ролик нужно досмотреть до конца — это и оплачивает сервер."
                                } else {
                                    "Второй ролик не досмотрен. Первый час уже начислен."
                                }
                            )
                            break
                        }

                        is AdGateway.Outcome.Unavailable -> {
                            if (watched == 0) takeFallback(outcome.reason, next)
                            return@launch
                        }
                    }
                }

                if (watched > 0) next()
            } catch (e: HeadApi.HttpError) {
                _ui.value = _ui.value.copy(error = messageFor(e))
            } catch (e: Exception) {
                _ui.value = _ui.value.copy(
                    error = "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз."
                )
            } finally {
                _ui.value = _ui.value.copy(watchingAd = false, adProgress = null)
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
