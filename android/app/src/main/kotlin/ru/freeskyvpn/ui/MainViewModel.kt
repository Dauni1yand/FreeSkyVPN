package ru.freeskyvpn.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import ru.freeskyvpn.core.Account
import ru.freeskyvpn.core.LinkCode
import ru.freeskyvpn.data.HeadApi
import ru.freeskyvpn.data.Repository
import ru.freeskyvpn.vpn.VpnStateHolder
import ru.freeskyvpn.vpn.VpnStatus

data class UiState(
    val busy: Boolean = false,
    val account: Account? = null,
    val linkCode: LinkCode? = null,
    /** Shown to the user; already translated, never a raw exception. */
    val error: String? = null,
    val needsVpnPermission: Boolean = false,
)

class MainViewModel(app: Application) : AndroidViewModel(app) {

    private val repo = Repository(app)

    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui

    val vpn = VpnStateHolder.state
    val storage get() = repo.storage

    init {
        viewModelScope.launch {
            // Both are best effort. A first launch offline must still reach
            // the main screen — with the connect button disabled and a
            // reason, not with a spinner that never resolves.
            runCatching { repo.ensureRegistered() }
            repo.refreshRoutingPolicy()
            loadAccount()
        }
    }

    fun loadAccount() = launchGuarded {
        _ui.value = _ui.value.copy(account = repo.account())
    }

    /**
     * The connect button.
     *
     * Fetches a config only when there is none cached: a reconnect should
     * not depend on the head being reachable, and asking for a new config
     * every time would also churn assignments on the server for no reason.
     */
    fun connect(onReady: () -> Unit) = launchGuarded {
        if (repo.storage.lastVlessUrl.isNullOrBlank()) {
            repo.fetchConfig()
        }
        repo.refreshRoutingPolicy()
        onReady()
    }

    /** The "не работает" button: the head swaps the config, then we restart. */
    fun reportFailure(onReady: () -> Unit) = launchGuarded {
        repo.reportFailure()
        onReady()
    }

    fun startTrial() = launchGuarded {
        _ui.value = _ui.value.copy(account = repo.startTrial())
    }

    fun requestLinkCode() = launchGuarded {
        _ui.value = _ui.value.copy(linkCode = repo.startLink())
    }

    fun dismissError() { _ui.value = _ui.value.copy(error = null) }

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
     * the genuinely unexpected ones fall through to it.
     */
    private fun messageFor(e: HeadApi.HttpError): String = when (e.code) {
        401 -> "Сессия устарела. Переустановите приложение — аккаунт сохранится, если он привязан к Telegram."
        403 -> "Аккаунт заблокирован."
        409 -> "Пробный период уже был использован."
        429 -> "Слишком часто. Подождите полминуты."
        503 -> "Сейчас нет свободных серверов. Мы уже разбираемся — попробуйте через несколько минут."
        else -> "Что-то пошло не так (${e.code}). Попробуйте ещё раз."
    }
}

/** True while a connection attempt is in flight, for the button's spinner. */
val VpnStatus.isTransient: Boolean get() = this == VpnStatus.Connecting
