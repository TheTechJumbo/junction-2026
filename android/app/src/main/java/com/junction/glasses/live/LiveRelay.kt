package com.junction.glasses.live

import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.roundToInt

/** A named place in the room, seen from the wearer's position (0 degrees ahead, + to the right). */
internal data class Waypoint(val name: String, val distanceM: Double, val bearingDeg: Int)

/** The part of a relay /locate reply the phone shows. */
internal data class LocateReply(val state: String, val headline: String, val waypoints: List<Waypoint>) {
    companion object {
        private const val MAX_WAYPOINTS = 20

        fun parse(body: String): LocateReply {
            val root = try { JsonParser.parseString(body).asJsonObject } catch (_: Exception) {
                throw RelayFailure("Relay reply was not JSON.")
            }
            val ok = root.get("ok")
            if (ok != null && ok.isJsonPrimitive && ok.asJsonPrimitive.isBoolean && !ok.asBoolean) {
                throw RelayFailure("Relay refused the photo" + (root.text("error")?.let { ": ${it.take(200)}" } ?: "."))
            }
            val waypoints = root.get("waypoints")?.takeIf { it.isJsonArray }?.asJsonArray
                ?.mapNotNull { waypoint(it) }?.take(MAX_WAYPOINTS) ?: emptyList()
            return LocateReply(
                state = root.text("state")?.take(32) ?: "unknown",
                headline = root.text("headline")?.take(300) ?: "",
                waypoints = waypoints,
            )
        }

        /** The relay's own error text from a JSON error body, or null. Never contains the key. */
        fun errorText(body: String): String? = try {
            JsonParser.parseString(body).asJsonObject.text("error")?.take(200)
        } catch (_: Exception) { null }

        private fun waypoint(entry: JsonElement): Waypoint? {
            if (!entry.isJsonObject) return null
            val place = entry.asJsonObject
            val name = place.text("name") ?: return null
            val distance = place.number("distance_m") ?: return null
            val bearing = place.number("bearing_deg") ?: return null
            return Waypoint(name.take(64), distance, bearing.roundToInt())
        }

        private fun JsonObject.text(key: String): String? =
            get(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString

        private fun JsonObject.number(key: String): Double? =
            get(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asDouble?.takeIf { it.isFinite() }
    }
}

/** A relay or network failure, worded for the screen. Never contains the key. */
internal class RelayFailure(message: String) : IOException(message)

/**
 * Posts one JPEG to `{relay}/locate?zone={zone}`. The key goes only in the X-Key header, never in the
 * URL, and is never logged. `fresh` and `view` are never sent, so an unchanged view is answered from
 * the relay's cheap gate. Debug builds may use cleartext loopback; everything else needs HTTPS.
 */
internal class RelayClient(baseUrl: String, private val key: String, private val zone: String, allowLocalHttp: Boolean) {
    private val base: HttpUrl = baseUrl.trim().toHttpUrl().also {
        require(it.username.isEmpty() && it.password.isEmpty() && it.query == null && it.fragment == null) { "Use a relay address without credentials, query or fragment" }
        require(it.encodedPath == "/") { "Use the relay origin without a path" }
        require(it.isHttps || (allowLocalHttp && it.host in LOOPBACK)) { "HTTPS required except debug loopback" }
    }
    init {
        require(key.isNotBlank() && key.none { it == '\n' || it == '\r' }) { "A relay key is required" }
        require(ZONE.matches(zone)) { "Zone names use a-z, 0-9, _ and -" }
    }
    private val http = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS).writeTimeout(20, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS).callTimeout(60, TimeUnit.SECONDS).build()

    fun request(jpeg: ByteArray): Request = Request.Builder()
        .url(base.newBuilder().addPathSegment("locate").addQueryParameter("zone", zone).build())
        .header("X-Key", key)
        .header("Accept", "application/json")
        .post(jpeg.toRequestBody(JPEG))
        .build()

    suspend fun locate(jpeg: ByteArray): LocateReply = suspendCancellableCoroutine { cont ->
        val call = http.newCall(request(jpeg))
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                cont.resumeWithException(RelayFailure("Relay unreachable (${e.javaClass.simpleName}). Check the relay address, Wi-Fi or adb reverse."))
            }
            override fun onResponse(call: Call, response: Response) {
                val outcome = runCatching { response.use { read(it) } }
                outcome.fold(
                    onSuccess = { cont.resume(it) },
                    onFailure = { cont.resumeWithException(it as? RelayFailure ?: RelayFailure("Relay reply could not be read.")) },
                )
            }
        })
    }

    private fun read(response: Response): LocateReply {
        val body = response.body ?: throw RelayFailure("Relay HTTP ${response.code}: empty reply.")
        val source = body.source()
        if (source.request(MAX_REPLY_BYTES + 1)) throw RelayFailure("Relay reply too large.")
        val text = source.buffer.readUtf8()
        if (!response.isSuccessful) {
            throw RelayFailure("Relay HTTP ${response.code}" + (LocateReply.errorText(text)?.let { ": $it" } ?: "."))
        }
        return LocateReply.parse(text)
    }

    companion object {
        const val DEFAULT_URL = "http://127.0.0.1:8090"
        const val DEFAULT_ZONE = "kitchen"
        private const val MAX_REPLY_BYTES = 8L * 1024 * 1024
        private val JPEG = "image/jpeg".toMediaType()
        private val LOOPBACK = setOf("localhost", "127.0.0.1", "::1", "10.0.2.2")
        private val ZONE = Regex("[a-z0-9_-]{1,32}")
    }
}

/** What the live camera has done so far, for the screen. */
internal data class LiveSnapshot<F>(
    val framesSent: Int = 0,
    val lastFrame: F? = null,
    val reply: LocateReply? = null,
    val roundTripSeconds: Double? = null,
    val error: String? = null,
)

/**
 * The live camera loop, with one request in flight at a time: capture the newest frame, post it, wait
 * for the reply, then capture the next one. A failed capture or post is shown and retried after
 * [retryDelayMs]. Runs in the caller's scope until [stop].
 */
internal class LiveRelayLoop<F>(
    private val scope: CoroutineScope,
    private val capture: suspend () -> F,
    private val post: suspend (F) -> LocateReply,
    private val onUpdate: (LiveSnapshot<F>) -> Unit,
    private val retryDelayMs: Long = RETRY_DELAY_MS,
    private val pause: suspend (Long) -> Unit = { delay(it) },
    private val clock: () -> Long = System::nanoTime,
) {
    private var job: Job? = null
    val running: Boolean get() = job?.isActive == true

    fun start() {
        if (running) return
        job = scope.launch {
            var snapshot = LiveSnapshot<F>()
            while (isActive) {
                try {
                    val frame = capture()
                    snapshot = snapshot.copy(framesSent = snapshot.framesSent + 1, lastFrame = frame)
                    onUpdate(snapshot)
                    val started = clock()
                    val reply = post(frame)
                    snapshot = snapshot.copy(reply = reply, roundTripSeconds = (clock() - started) / 1e9, error = null)
                    onUpdate(snapshot)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    snapshot = snapshot.copy(error = describe(e))
                    onUpdate(snapshot)
                    pause(retryDelayMs)
                }
            }
        }
    }

    fun stop() { job?.cancel(); job = null }

    companion object {
        const val RETRY_DELAY_MS = 2_000L
        fun describe(e: Exception): String = (e as? RelayFailure)?.message ?: "Camera or connection error (${e.javaClass.simpleName})."
    }
}
