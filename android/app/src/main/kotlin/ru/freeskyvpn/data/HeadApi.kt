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

    private val base = BuildConfig.HEAD_API_URL.trimEnd('/')

    // --- calls -----------------------------------------------------------

    suspend fun registerDevice(deviceLabel: String): DeviceRegistration {
        val body = json.encodeToString(mapOf("device_label" to deviceLabel))
        return post("/api/v1/auth/device", body, authed = false)
    }

    suspend fun connect(): ConnectionConfig = post("/api/v1/me/connect", "{}")

    suspend fun reportFailure(): FailureOutcome = post("/api/v1/me/report-failure", "{}")

    suspend fun account(): Account = get("/api/v1/me")

    suspend fun startTrial(): Account = post("/api/v1/me/trial", "{}")

    suspend fun startLink(): LinkCode = post("/api/v1/me/link/start", "{}")

    suspend fun routingPolicy(): RoutingPolicy = get("/api/v1/routing-policy")

    // --- plumbing --------------------------------------------------------

    private suspend inline fun <reified T> get(path: String): T =
        execute(requestBuilder(path).get().build())

    private suspend inline fun <reified T> post(
        path: String,
        body: String,
        authed: Boolean = true,
    ): T = execute(
        requestBuilder(path, authed)
            .post(body.toRequestBody(JSON_MEDIA))
            .build()
    )

    private fun requestBuilder(path: String, authed: Boolean = true): Request.Builder {
        val builder = Request.Builder()
            .url("$base$path")
            .header("X-Service-Token", BuildConfig.HEAD_SERVICE_TOKEN)
        if (authed) {
            storage.token?.let { builder.header("Authorization", "Bearer $it") }
        }
        return builder
    }

    private suspend inline fun <reified T> execute(request: Request): T =
        withContext(Dispatchers.IO) {
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    throw HttpError(response.code, detailOf(text, response.code))
                }
                json.decodeFromString<T>(text)
            }
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
