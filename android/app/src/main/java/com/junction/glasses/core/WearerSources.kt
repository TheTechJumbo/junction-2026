package com.junction.glasses.core

import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.time.ZoneId
import java.util.concurrent.TimeUnit

private const val LIMIT = 256 * 1024

private fun checkedOrigin(raw: String, allowLocalHttp: Boolean): HttpUrl = raw.trim().trimEnd('/').toHttpUrl().also {
    require(it.username.isEmpty() && it.password.isEmpty() && it.query == null && it.fragment == null) { "Use a server URL without credentials, query or fragment" }
    require(it.encodedPath == "/") { "Use the server origin without a path" }
    require(it.isHttps || (allowLocalHttp && it.host in setOf("localhost", "127.0.0.1", "::1", "10.0.2.2"))) { "HTTPS required except explicit local development" }
}

private val http: OkHttpClient by lazy {
    OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(8, TimeUnit.SECONDS).callTimeout(10, TimeUnit.SECONDS).build()
}

/** Runs [request], hands the JSON body (at most 256 KB) to [decode], and reports success or a body-free failure. */
private fun <T> fetchJson(request: Request, decode: (String) -> T, callback: (Result<T>) -> Unit): Cancelable {
    val call = http.newCall(request)
    // The relay is the one that takes X-Key; the event API takes a bearer token.
    val label = (if (request.header("X-Key") != null) "relay " else "backend ") +
        "${request.url.encodedPath} · ${request.url.host}:${request.url.port}"
    call.enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            if (!call.isCanceled()) HttpTrace.record(label, 0, System.currentTimeMillis(), e.javaClass.simpleName)
            callback(Result.failure(BackendFailure(if (call.isCanceled()) "cancelled" else "network")))
        }
        override fun onResponse(call: Call, response: Response) {
            HttpTrace.record(label, response.code, System.currentTimeMillis())
            callback(response.use { r ->
                try {
                    if (!r.isSuccessful) throw BackendFailure("http", r.code)
                    val body = r.body ?: throw BackendFailure("empty_response")
                    val source = body.source(); source.request(LIMIT.toLong() + 1)
                    if (source.buffer.size > LIMIT) throw BackendFailure("response_too_large")
                    Result.success(decode(source.readUtf8()))
                } catch (e: Exception) { Result.failure(if (e is BackendFailure) e else BackendFailure("invalid_response")) }
            })
        }
    })
    return Cancelable { call.cancel() }
}

/** Today's sensor observations from the event API (`GET /timeline?day=`), with the same session token as /ask. */
class DayClient(baseUrl: String, private val token: String, private val zone: ZoneId, allowLocalHttp: Boolean = false) {
    private val base = checkedOrigin(baseUrl, allowLocalHttp)
    init { require(token.isNotBlank() && !token.contains('\n') && !token.contains('\r')) { "A valid session token is required" } }

    fun today(day: java.time.LocalDate, callback: (Result<List<Sensed>>) -> Unit): Cancelable = fetchJson(
        Request.Builder().url(base.newBuilder().addPathSegment("timeline").addQueryParameter("day", day.toString()).build())
            .header("Authorization", "Bearer $token").header("Accept", "application/json").get().build(),
        { WearerJson.sensed(it, zone) }, callback)
}

/** The vision relay: reminders and where things are. Optional; the key goes in a header, never in a URL. */
class RelayClient(baseUrl: String, private val key: String, allowLocalHttp: Boolean = false) {
    private val base = checkedOrigin(baseUrl, allowLocalHttp)
    init { require(key.isNotBlank() && !key.contains('\n') && !key.contains('\r')) { "A valid relay key is required" } }

    private fun get(path: String) = Request.Builder().url(base.newBuilder().addPathSegment(path).build())
        .header("X-Key", key).header("Accept", "application/json").get().build()

    fun reminders(callback: (Result<List<Reminder>>) -> Unit) = fetchJson(get("reminders"), WearerJson::reminders, callback)
    fun where(callback: (Result<Fix>) -> Unit) = fetchJson(get("where"), WearerJson::fix, callback)

    /** Marks a reminder finished by hand (the wearer pressed Done on the glasses or phone). */
    fun done(id: String, callback: (Result<Unit>) -> Unit): Cancelable {
        val url = base.newBuilder().addPathSegment("reminder").addQueryParameter("id", id).addQueryParameter("done", "1").build()
        return fetchJson(Request.Builder().url(url).header("X-Key", key).post(ByteArray(0).toRequestBody()).build(), { }, callback)
    }
}
