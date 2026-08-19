package ru.freeskyvpn.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import ru.freeskyvpn.BuildConfig
import ru.freeskyvpn.core.Account
import ru.freeskyvpn.core.AdProgress
import ru.freeskyvpn.core.AdTicket
import ru.freeskyvpn.core.ApiEndpoints
import ru.freeskyvpn.core.ConnectionConfig
import ru.freeskyvpn.core.DeviceRegistration
import ru.freeskyvpn.core.FailureOutcome
import ru.freeskyvpn.core.LinkCode
import ru.freeskyvpn.core.RoutingPolicy
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Thin client for the head.
 *
 * Every decision — which node, whether a config is dead, what the account is
 * entitled to — belongs to the head, exactly as it does for the bot. This
 * class carries requests and nothing else.
 *
 * Both credentials travel on every call and they are not interchangeable:
 * the service token says "this is the FreeSkyVPN app", the bearer token says
 * "acting for this account". The first is in the APK and is not a secret;
 * the second is why that does not matter.
 */
class HeadApi(private val storage: Storage) {

    class HttpError(val code: Int, val detail: String) :
        IOException("head returned $code: $detail")

    private val json = Json { ignoreUnknownKeys = true }

    private val client = OkHttpClient.Builder()
        // The head sits in Russia and the phone may be on a bad mobile link.
        // Long enough to survive that, short enough that a button press does
        // not appear to hang forever.
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    /**
     * Addresses to try, most likely first.
     *
     * Recomputed per call rather than cached in a field: the debug override
     * and the last-known-good address both change while the app is running,
     * and a field captured at construction would ignore both until restart.
     */
    private fun endpoints(): List<String> = ApiEndpoints.resolve(
        configured = ApiEndpoints.parse(BuildConfig.HEAD_API_URL),
        override = if (BuildConfig.DEBUG) storage.apiOverride else null,
        lastGood = storage.lastGoodApiUrl,
    )

    // --- calls -----------------------------------------------------------

    suspend fun registerDevice(deviceLabel: String): DeviceRegistration {
        val body = json.encodeToString(mapOf("device_label" to deviceLabel))
        return post("/api/v1/auth/device", body, authed = false)
    }

    suspend fun connect(): ConnectionConfig = post("/api/v1/me/connect", "{}")

    suspend fun reportFailure(): FailureOutcome = post("/api/v1/me/report-failure", "{}")

    suspend fun account(): Account = get("/api/v1/me")

    /**
     * Ask for the token covering one run through a package's ads.
     *
     * The package is named here but priced by the server: the reply says
     * how many views it wants and what each is worth, and those are the
     * numbers that count.
     */
    suspend fun prepareAd(packageCode: String): AdTicket =
        post("/api/v1/me/ad/prepare", json.encodeToString(mapOf("package" to packageCode)))

    /** Credit one completed view. The token is short-lived and spent when the package finishes. */
    suspend fun completeAd(nonce: String): AdProgress =
        post("/api/v1/me/ad/complete", json.encodeToString(mapOf("nonce" to nonce)))

    /**
     * Tell the head no ad could be shown, and take the fallback.
     *
     * Rate limited server-side, so a 429 here is expected rather than
     * exceptional — it means the fallback was already used recently.
     */
    suspend fun adUnavailable(): Account = post("/api/v1/me/ad/unavailable", "{}")

    suspend fun startLink(): LinkCode = post("/api/v1/me/link/start", "{}")

    suspend fun routingPolicy(): RoutingPolicy = get("/api/v1/routing-policy")

    // --- plumbing --------------------------------------------------------

    private suspend inline fun <reified T> get(path: String): T =
        attempt(path, authed = true) { it.get() }

    private suspend inline fun <reified T> post(
        path: String,
        body: String,
        authed: Boolean = true,
    ): T = attempt(path, authed) { it.post(body.toRequestBody(JSON_MEDIA)) }

    private fun requestBuilder(base: String, path: String, authed: Boolean): Request.Builder {
        val builder = Request.Builder()
            .url("$base$path")
            .header("X-Service-Token", BuildConfig.HEAD_SERVICE_TOKEN)
        if (authed) {
            storage.token?.let { builder.header("Authorization", "Bearer $it") }
        }
        return builder
    }

    /**
     * Runs one request against each address until something answers.
     *
     * Only *transport* failures move on to the next address. An HTTP error
     * means the head is there and said no, and retrying that against a
     * backup would turn one honest 402 into three, then report whichever
     * error the last host happened to give.
     *
     * The address that worked is remembered, so the next launch starts with
     * it instead of paying a connect timeout on a dead primary.
     */
    private suspend inline fun <reified T> attempt(
        path: String,
        authed: Boolean,
        crossinline build: (Request.Builder) -> Request.Builder,
    ): T = withContext(Dispatchers.IO) {
        val candidates = endpoints()
        if (candidates.isEmpty()) {
            throw IOException(
                "адрес сервера не задан — соберите с -PheadApiUrl=https://…"
            )
        }

        var lastTransportError: IOException? = null
        for (base in candidates) {
            val request = build(requestBuilder(base, path, authed)).build()
            try {
                client.newCall(request).execute().use { response ->
                    val text = response.body?.string().orEmpty()
                    // Reached it, whatever it said. Remember it and stop.
                    storage.lastGoodApiUrl = base
                    if (!response.isSuccessful) {
                        throw HttpError(response.code, detailOf(text, response.code))
                    }
                    return@withContext json.decodeFromString<T>(text)
                }
            } catch (e: HttpError) {
                throw e
            } catch (e: IOException) {
                lastTransportError = e
            }
        }
        throw lastTransportError ?: IOException("сервер недоступен")
    }

    /** Pulls FastAPI's `detail` out of an error body, falling back to the status. */
    fun detailOf(body: String, code: Int): String =
        runCatching {
            json.parseToJsonElement(body).let { element ->
                (element as? kotlinx.serialization.json.JsonObject)
                    ?.get("detail")?.toString()?.trim('"')
            }
        }.getOrNull().orEmpty().ifBlank { "HTTP $code" }

    private companion object {
        val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
