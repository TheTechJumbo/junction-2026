package com.junction.family

import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayInputStream
import java.io.IOException

/**
 * Forwards the Live tab's requests to the laptop relay, adding its access key.
 *
 * The Live tab loads the relay's caretaker page (`/care`: the room as a Gaussian splat with the
 * wearer in it) from the app's own origin under `/relay/`, so the page asks `/relay/where`,
 * `/relay/day` and so on. They arrive here, lose the prefix and go to the relay with the key in an
 * `X-Key` header: the key never appears in a page address. Only the read-only routes that page
 * uses are forwarded; nothing that records, names places or spends GPU time.
 */
class RelayProxy(private val client: OkHttpClient, private val relay: () -> RelaySettings?) {

    fun handle(method: String, path: String, query: String?): ApiProxy.Result {
        if (method != "GET") return failure(405)
        val inner = path.removePrefix(PREFIX)
        if (!path.startsWith("$PREFIX/") || !isAllowed(inner, query)) return failure(404)
        val settings = relay() ?: return failure(503)
        val target = settings.address.newBuilder().encodedPath(inner).encodedQuery(query).build()
        val request = Request.Builder().url(target).get()
            .header("X-Key", settings.key).header("Cache-Control", "no-store").build()
        val response = try {
            client.newCall(request).execute()
        } catch (_: IOException) {
            return failure(503)
        }
        if (response.code !in 200..299 && response.code !in 400..599) {
            response.close()
            return failure(502)
        }
        val type = response.header("Content-Type") ?: "application/octet-stream"
        val out = linkedMapOf("Content-Type" to type, "Cache-Control" to "no-store")
        response.header("Content-Length")?.let { out["Content-Length"] = it }
        val body = response.body?.byteStream() ?: ByteArrayInputStream(ByteArray(0))
        return ApiProxy.Result(response.code, response.message.ifBlank { ApiProxy.reason(response.code) },
            type.substringBefore(';').trim(), ApiProxy.charset(type), out, body)
    }

    companion object {
        const val PREFIX = "/relay"
        private const val ZONE = "zone=[a-z0-9_-]{1,32}"
        private val ZONE_QUERY = Regex(ZONE)
        private val DAY_QUERY = Regex("date=\\d{4}-\\d{2}-\\d{2}")
        private val FIND_QUERY = Regex("$ZONE&what=[A-Za-z0-9%+._-]{1,64}")  // /find: where a thing was last seen
        private val EVIDENCE = Regex("/evidence/[A-Za-z0-9_-]{1,96}/\\w{1,64}\\.(splat|json|jpg)")
        private val BY_ZONE = setOf("/care", "/care/space", "/where", "/waypoints", "/frame", "/changes")

        /** [path] is without the `/relay` prefix. The key is added here, so a query may never carry one. */
        fun isAllowed(path: String, query: String?): Boolean = when {
            path in BY_ZONE -> query != null && ZONE_QUERY.matches(query)
            path == "/day" -> query == null || DAY_QUERY.matches(query)
            path == "/reminders" -> query == null
            path == "/find" -> query != null && FIND_QUERY.matches(query)
            EVIDENCE.matches(path) -> query == null
            else -> false
        }

        /** The Live tab's start page, for a room. */
        fun startUrl(origin: String, zone: String): String = "$origin$PREFIX/care?zone=$zone"

        private fun failure(code: Int) = ApiProxy.Result(code, ApiProxy.reason(code), "application/json", "utf-8",
            mapOf("Cache-Control" to "no-store"), ByteArrayInputStream("{}".toByteArray()))
    }
}

/** Where the laptop relay is, its access key, and which room the Live tab shows. */
data class RelaySettings(val address: HttpUrl, val key: String, val zone: String) {
    override fun toString() = "RelaySettings(address=$address, zone=$zone)" // never the key, even in a log
}
