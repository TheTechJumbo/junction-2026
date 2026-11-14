package com.junction.glasses.core

import com.google.gson.JsonParser
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

fun interface Cancelable { fun cancel() }
data class Observation(val id: String, val type: String, val source: String, val ts: String,
    val zone: String, val objectName: String?, val confidence: Double, val evidenceUrl: String?)
data class Answer(val text: String, val evidence: List<Observation>)
interface BackendClient {
    fun ask(question: String, callback: (Result<Answer>) -> Unit): Cancelable
    fun speak(text: String, callback: (Result<ByteArray>) -> Unit): Cancelable
}
/** Deliberately contains no response body, URL or token in errors. */
class BackendFailure(val kind: String, val status: Int? = null) : IOException(kind)

class HttpBackendClient(baseUrl: String, private val token: String, allowLocalHttp: Boolean = false) : BackendClient {
    private val base = baseUrl.toHttpUrl().also {
        require(it.username.isEmpty() && it.password.isEmpty() && it.query == null && it.fragment == null) { "Use a server URL without credentials, query or fragment" }
        require(it.encodedPath == "/") { "Use the server origin without a path" }
        require(it.isHttps || (allowLocalHttp && it.host in setOf("localhost", "127.0.0.1", "::1", "10.0.2.2"))) { "HTTPS required except explicit local development" }
    }
    init { require(token.isNotBlank() && !token.contains('\n') && !token.contains('\r')) { "A valid session token is required" } }
    private val http = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).callTimeout(12, TimeUnit.SECONDS).build()
    override fun ask(question: String, callback: (Result<Answer>) -> Unit): Cancelable {
        require(question.isNotBlank() && question.length <= 2000)
        return request("ask", "question", question, 256 * 1024, "application/json", callback) { bytes ->
            val root = JsonParser.parseString(bytes.toString(Charsets.UTF_8)).asJsonObject
            val text = root.get("answer").asString
            require(text.isNotBlank())
            val evidence = root.getAsJsonArray("evidence").map { entry ->
                val e = entry.asJsonObject
                fun nullable(key: String) = e.get(key)?.takeUnless { it.isJsonNull }?.asString
                val confidence = e.get("confidence").asDouble
                require(confidence.isFinite() && confidence in 0.0..1.0)
                Observation(e.get("id").asString, e.get("type").asString, e.get("source").asString,
                    e.get("ts").asString, e.get("zone").asString, nullable("object"), confidence, nullable("evidence_url"))
            }
            Answer(text, evidence)
        }
    }
    override fun speak(text: String, callback: (Result<ByteArray>) -> Unit): Cancelable {
        require(text.isNotBlank() && text.length <= 2000)
        return request("speak", "text", text, 5 * 1024 * 1024, "audio/mpeg", callback) { bytes ->
            require(bytes.size >= 3 && ((bytes[0] == 0x49.toByte() && bytes[1] == 0x44.toByte() && bytes[2] == 0x33.toByte()) ||
                (bytes[0] == 0xff.toByte() && (bytes[1].toInt() and 0xe0) == 0xe0)))
            bytes
        }
    }
    private fun <T> request(path: String, key: String, value: String, limit: Int, expectedType: String,
        callback: (Result<T>) -> Unit, decode: (ByteArray) -> T): Cancelable {
        val payload = com.google.gson.JsonObject().apply { addProperty(key, value) }.toString()
        val call = http.newCall(Request.Builder().url(base.newBuilder().addPathSegment(path).build())
            .header("Authorization", "Bearer $token").header("Accept", expectedType)
            .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType())).build())
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) { callback(Result.failure(BackendFailure(if (call.isCanceled()) "cancelled" else "network"))) }
            override fun onResponse(call: Call, response: Response) {
                val result = response.use { r ->
                    try {
                        if (!r.isSuccessful) throw BackendFailure("http", r.code)
                        val body = r.body ?: throw BackendFailure("empty_response")
                        if (body.contentType()?.let { "${it.type}/${it.subtype}" } != expectedType) throw BackendFailure("content_type")
                        if (body.contentLength() > limit) throw BackendFailure("response_too_large")
                        val source = body.source()
                        source.request(limit.toLong() + 1)
                        if (source.buffer.size > limit) throw BackendFailure("response_too_large")
                        Result.success(decode(source.readByteArray()))
                    } catch (e: Exception) { Result.failure(if (e is BackendFailure) e else BackendFailure("invalid_response")) }
                }
                callback(result)
            }
        })
        return Cancelable { call.cancel() }
    }
}
