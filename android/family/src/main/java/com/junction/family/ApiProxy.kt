package com.junction.family

import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream

/**
 * Forwards the dashboard's data requests to the home backend.
 *
 * The page is served from the app's own origin, so it asks that origin for `/timeline`,
 * `/summary` and `/evidence/<id>`, exactly as it does when the backend serves it. The WebView
 * hands those requests here and this class makes them natively with OkHttp: certificate checks,
 * connection reuse and HTTP/2 stay in native code, and the page's same-origin security policy
 * needs no exceptions. Only those three read-only routes are forwarded; the bearer token travels
 * in the page's Authorization header and is never stored here.
 */
class ApiProxy(private val client: OkHttpClient, private val backend: () -> HttpUrl?) {

    /** What the WebView needs to build a WebResourceResponse. */
    class Result(
        val status: Int,
        val reason: String,
        val mimeType: String,
        val encoding: String?,
        val headers: Map<String, String>,
        val body: InputStream,
    )

    fun handle(method: String, path: String, query: String?, headers: Map<String, String>): Result {
        if (method != "GET") return failure(405)
        if (!isAllowed(path, query)) return failure(404)
        val root = backend() ?: return failure(503)
        val target = root.newBuilder().encodedPath(path).encodedQuery(query).build()
        val request = Request.Builder().url(target).get().header("Cache-Control", "no-store")
        headers.entries.firstOrNull { it.key.equals("Authorization", ignoreCase = true) }
            ?.let { request.header("Authorization", it.value) }
        val response = try {
            client.newCall(request.build()).execute()
        } catch (_: IOException) {
            return failure(503)
        }
        // The page refuses redirects; WebView responses cannot carry a 3xx status either.
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

    companion object {
        private val DAY_QUERY = Regex("day=\\d{4}-\\d{2}-\\d{2}")
        private val EVIDENCE_PATH = Regex("/evidence/[A-Za-z0-9._-]{1,128}")

        fun isAllowed(path: String, query: String?): Boolean = when {
            path == "/timeline" || path == "/summary" -> query != null && DAY_QUERY.matches(query)
            EVIDENCE_PATH.matches(path) -> query == null
            path == "/mesh" || path == "/hazards" -> query == null
            path == "/night" -> query == null || DAY_QUERY.matches(query)
            else -> false
        }

        fun charset(contentType: String): String? =
            Regex("charset=\"?([^;\"]+)", RegexOption.IGNORE_CASE).find(contentType)?.groupValues?.get(1)?.trim()

        fun reason(code: Int): String = when (code) {
            200 -> "OK"; 401 -> "Unauthorized"; 403 -> "Forbidden"; 404 -> "Not Found"
            405 -> "Method Not Allowed"; 502 -> "Bad Gateway"; 503 -> "Service Unavailable"
            in 200..299 -> "OK"; in 400..499 -> "Client Error"; else -> "Server Error"
        }

        private fun failure(code: Int) = Result(code, reason(code), "application/json", "utf-8",
            mapOf("Cache-Control" to "no-store"), ByteArrayInputStream("{}".toByteArray()))
    }
}
