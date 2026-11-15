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
        require(it.isHttps || LocalHosts.cleartextAllowed(it.host, allowLocalHttp)) { "HTTPS required except explicit local development" }
    }
    init { require(token.isNotBlank() && !token.contains('\n') && !token.contains('\r')) { "A valid session token is required" } }
    /** The first 6 hex of the token's SHA-256, for the debug panel: shows a mismatch, never the token. */
    val tokenPrint: String get() = KeyPrint.of(token)
    private val http = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).callTimeout(12, TimeUnit.SECONDS).build()
    /** Read-only authenticated probe; never creates question logs or sensor events. */
    fun checkConnection(callback: (Result<Unit>) -> Unit): Cancelable {
        val request = Request.Builder()
            .url(base.newBuilder().addPathSegment("events").addQueryParameter("limit", "1").build())
            .header("Authorization", "Bearer $token").header("Accept", "application/json").get().build()
        return execute(request, 256 * 1024, "application/json", callback) { bytes ->
            val root = com.google.gson.GsonBuilder().setStrictness(com.google.gson.Strictness.STRICT)
                .create().fromJson(bytes.toString(Charsets.UTF_8), com.google.gson.JsonObject::class.java)
            require(root.keySet() == setOf("events", "next_cursor"))
            fun string(value: com.google.gson.JsonElement): String {
                require(value.isJsonPrimitive && value.asJsonPrimitive.isString)
                return value.asString
            }
            val cursor = root.get("next_cursor")
            if (!cursor.isJsonNull) string(cursor)
            val events = root.get("events")
            require(events.isJsonArray && events.asJsonArray.size() <= 1)
            events.asJsonArray.forEach { entry ->
                require(entry.isJsonObject)
                val event = entry.asJsonObject
                require(event.keySet() == setOf("id", "type", "source", "ts", "zone", "object", "confidence", "evidence_url"))
                require(string(event.get("id")).matches(Regex("[A-Za-z0-9_-]{1,128}")))
                require(string(event.get("type")).length in 1..256)
                require(string(event.get("zone")).length in 1..256)
                require(string(event.get("source")) in setOf("sensor", "vision", "voice"))
                java.time.OffsetDateTime.parse(string(event.get("ts")))
                val obj = event.get("object")
                if (!obj.isJsonNull) require(string(obj).length <= 256)
                val evidence = event.get("evidence_url")
                if (!evidence.isJsonNull) string(evidence)
                val confidence = event.get("confidence")
                require(confidence.isJsonPrimitive && confidence.asJsonPrimitive.isNumber)
                require(confidence.asDouble.isFinite() && confidence.asDouble in 0.0..1.0)
            }
            Unit
        }
    }
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
        val request = Request.Builder().url(base.newBuilder().addPathSegment(path).build())
            .header("Authorization", "Bearer $token").header("Accept", expectedType)
            .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType())).build()
        return execute(request, limit, expectedType, callback, decode)
    }
    private fun <T> execute(request: Request, limit: Int, expectedType: String,
        callback: (Result<T>) -> Unit, decode: (ByteArray) -> T): Cancelable {
        val call = http.newCall(request)
        val label = "backend ${request.url.encodedPath} · ${request.url.host}:${request.url.port}"
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!call.isCanceled()) HttpTrace.record(label, 0, System.currentTimeMillis(), e.javaClass.simpleName)
                callback(Result.failure(BackendFailure(if (call.isCanceled()) "cancelled" else "network")))
            }
            override fun onResponse(call: Call, response: Response) {
                HttpTrace.record(label, response.code, System.currentTimeMillis())
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
                    } catch (e: Exception) { Result.failure(if (call.isCanceled()) BackendFailure("cancelled") else if (e is BackendFailure) e else BackendFailure("invalid_response")) }
                }
                callback(result)
            }
        })
        return Cancelable { call.cancel() }
    }
}
