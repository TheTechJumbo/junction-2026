package com.junction.glasses.core

import com.google.gson.JsonParser
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Explicit upload only. Does not save images or fetch the returned reference. */
class EvidenceClient(baseUrl: String, private val token: String, allowLocalHttp: Boolean = false) {
    private val base = baseUrl.toHttpUrl().also {
        require(it.username.isEmpty() && it.password.isEmpty() && it.query == null && it.fragment == null && it.encodedPath == "/") { "Use a plain server origin" }
        require(it.isHttps || LocalHosts.cleartextAllowed(it.host, allowLocalHttp)) { "HTTPS required except explicit local development" }
    }
    init { require(token.isNotBlank() && !token.contains('\n') && !token.contains('\r')) { "A valid session token is required" } }
    private val http = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).callTimeout(20, TimeUnit.SECONDS).build()

    fun uploadJpeg(eventId: String, bytes: ByteArray, callback: (Result<String>) -> Unit): Cancelable {
        // Dot path segments normalize in HttpUrl; reject them rather than risk a different route.
        if (eventId.isBlank() || eventId == "." || eventId == ".." || eventId.length > 256) {
            callback(Result.failure(BackendFailure("invalid_event_id"))); return Cancelable {}
        }
        if (bytes.size > 5 * 1024 * 1024) {
            callback(Result.failure(BackendFailure("image_too_large"))); return Cancelable {}
        }
        if (bytes.size < 3 || bytes[0] != 0xff.toByte() || bytes[1] != 0xd8.toByte() || bytes[2] != 0xff.toByte()) {
            callback(Result.failure(BackendFailure("invalid_jpeg"))); return Cancelable {}
        }
        val ownedBytes = bytes.copyOf()
        val url = base.newBuilder().addPathSegment("events").addPathSegment(eventId).addPathSegment("evidence").build()
        val call = http.newCall(Request.Builder().url(url).header("Authorization", "Bearer $token")
            .header("Accept", "application/json").post(ownedBytes.toRequestBody("image/jpeg".toMediaType())).build())
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                ownedBytes.fill(0)
                callback(Result.failure(BackendFailure(if (call.isCanceled()) "cancelled" else "network")))
            }
            override fun onResponse(call: Call, response: Response) {
                val result = response.use { r -> try {
                    if (r.code != 200) throw BackendFailure("http", r.code)
                    val body = r.body ?: throw BackendFailure("empty_response")
                    if (body.contentType()?.let { "${it.type}/${it.subtype}" } != "application/json") throw BackendFailure("content_type")
                    if (body.contentLength() > 8192) throw BackendFailure("response_too_large")
                    val source = body.source(); source.request(8193)
                    if (source.buffer.size > 8192) throw BackendFailure("response_too_large")
                    val ref = JsonParser.parseString(source.readUtf8()).asJsonObject.get("evidence_url").asString
                    if (ref.length > 2048 || !ref.startsWith("/evidence/") || ref.contains('\\') || ref.contains('?') || ref.contains('#') || ref.any { it.code < 32 }) throw BackendFailure("invalid_reference")
                    val resolved = base.resolve(ref) ?: throw BackendFailure("invalid_reference")
                    if (resolved.scheme != base.scheme || resolved.host != base.host || resolved.port != base.port || !resolved.encodedPath.startsWith("/evidence/")) throw BackendFailure("invalid_reference")
                    Result.success(ref)
                } catch (e: Exception) { Result.failure(if (e is BackendFailure) e else BackendFailure("invalid_response")) } }
                ownedBytes.fill(0)
                callback(result)
            }
        })
        return Cancelable { call.cancel() }
    }
}
