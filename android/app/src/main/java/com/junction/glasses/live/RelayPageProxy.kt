package com.junction.glasses.live

import com.junction.glasses.core.HttpTrace
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.TimeUnit

/**
 * Serves the wearer's 3D view (the relay's page with the room as a Gaussian splat and the wearer in it)
 * to the app's WebView without the relay key ever being in a page address. As in the family app's
 * RelayProxy: the WebView loads `https://appassets.androidplatform.net/relay/<page>` (the app's own
 * origin, never on the network), the app intercepts each request and this class makes it natively,
 * prefix removed, with the key in an `X-Key` header: the USB address first, the Wi-Fi one when the
 * USB one does not answer. Only the read-only routes the page uses are forwarded: nothing that records,
 * names places, starts guidance or spends GPU time. No URL, query or key is logged.
 */
internal class RelayPageProxy(
    private val target: () -> Target?,
    private val client: OkHttpClient = DEFAULT_CLIENT,
) {
    /** Where the relay is (USB address first) and its key. Never printed with the key. */
    class Target(val addresses: List<HttpUrl>, val key: String) {
        override fun toString() = "Target(addresses=$addresses)"
    }

    /** What the WebView needs to build a WebResourceResponse. */
    class Result(
        val status: Int,
        val reason: String,
        val mimeType: String,
        val encoding: String?,
        val headers: Map<String, String>,
        val body: InputStream,
    )

    fun handle(method: String, path: String, query: String?): Result {
        if (method != "GET") return failure(405)
        if (!path.startsWith("$PREFIX/")) return failure(404)
        val inner = path.removePrefix(PREFIX)
        if (!isAllowed(inner, query)) return failure(404)
        val settings = target() ?: return failure(503)
        for (address in settings.addresses) {
            val url = address.newBuilder().encodedPath(inner).encodedQuery(query).build()
            val request = Request.Builder().url(url).get()
                .header("X-Key", settings.key).header("Cache-Control", "no-store").build()
            val response = try {
                client.newCall(request).execute()
            } catch (_: IOException) {
                HttpTrace.record("3D page $inner · ${address.host}:${address.port}", 0, System.currentTimeMillis())
                continue // not answering: the next address (Wi-Fi)
            }
            HttpTrace.record("3D page ${if (inner.startsWith("/evidence/")) "/evidence/…" else inner} · ${address.host}:${address.port}",
                response.code, System.currentTimeMillis())
            if (response.code !in 200..299 && response.code !in 400..599) {
                response.close()
                return failure(502)
            }
            val type = response.header("Content-Type") ?: "application/octet-stream"
            val out = linkedMapOf("Content-Type" to type, "Cache-Control" to "no-store")
            response.header("Content-Length")?.let { out["Content-Length"] = it }
            val body = response.body?.byteStream() ?: ByteArrayInputStream(ByteArray(0))
            return Result(response.code, response.message.ifBlank { reason(response.code) },
                type.substringBefore(';').trim(), charset(type), out, body)
        }
        return failure(503)
    }

    companion object {
        /** The app's own origin for the page (Android's reserved WebViewAssetLoader domain: never fetched). */
        const val ORIGIN = "https://appassets.androidplatform.net"
        const val PREFIX = "/relay"
        /** The wearer's 3D page on the relay, until the relay names it otherwise. */
        const val DEFAULT_PAGE = "/care"
        private const val ZONE = "zone=[a-z0-9_-]{1,32}"
        private val PAGE_QUERY = Regex("$ZONE(&view=[a-z]{1,16})?")
        private val ZONE_QUERY = Regex(ZONE)
        private val SPACE_QUERY = Regex("$ZONE(&lite=1)?")  // /care/space: lite=1 points at the small splat
        private val DAY_QUERY = Regex("date=\\d{4}-\\d{2}-\\d{2}")
        private val FIND_QUERY = Regex("$ZONE&what=[A-Za-z0-9%+._-]{1,64}")
        private val EVIDENCE = Regex("/evidence/[A-Za-z0-9_-]{1,96}/\\w{1,64}\\.(splat|json|jpg)")
        private val PAGES = setOf("/care", "/me")
        private val BY_ZONE = setOf("/where", "/waypoints", "/frame", "/changes")
        private val DEFAULT_CLIENT: OkHttpClient by lazy {
            OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
                .connectTimeout(4, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()
        }

        /**
         * [path] is without the `/relay` prefix. The key is added by the proxy, so a query never carries
         * one (a `k=` parameter matches nothing here). `/navigate` only as the map (no `to`): asking with
         * `to` would start guidance on the relay, which only the wearer does.
         */
        fun isAllowed(path: String, query: String?): Boolean = when {
            path in PAGES -> query != null && PAGE_QUERY.matches(query)
            path == "/care/space" -> query != null && SPACE_QUERY.matches(query)
            path in BY_ZONE -> query != null && ZONE_QUERY.matches(query)
            path == "/navigate" -> query != null && ZONE_QUERY.matches(query)
            path == "/day" -> query == null || DAY_QUERY.matches(query)
            path == "/reminders" -> query == null
            path == "/find" -> query != null && FIND_QUERY.matches(query)
            EVIDENCE.matches(path) -> query == null
            else -> false
        }

        /** The 3D page for [zone], as the WebView loads it: on the app's origin, with no key. */
        fun startUrl(zone: String, page: String = DEFAULT_PAGE, view: String? = "wearer"): String =
            "$ORIGIN$PREFIX$page?zone=$zone" + (view?.let { "&view=$it" } ?: "")

        fun charset(contentType: String): String? =
            Regex("charset=([A-Za-z0-9._-]+)", RegexOption.IGNORE_CASE).find(contentType)?.groupValues?.get(1)

        fun reason(code: Int): String = when (code) {
            200 -> "OK"; 404 -> "Not Found"; 405 -> "Method Not Allowed"; 502 -> "Bad Gateway"; 503 -> "Service Unavailable"
            else -> "Error"
        }

        private fun failure(code: Int) = Result(code, reason(code), "application/json", "utf-8",
            mapOf("Cache-Control" to "no-store"), ByteArrayInputStream("{}".toByteArray()))
    }
}
