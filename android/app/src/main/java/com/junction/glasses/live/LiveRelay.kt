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
import com.junction.glasses.core.HttpTrace
import com.junction.glasses.core.LocalHosts
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.File
import java.io.IOException
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.roundToInt

/** A named place in the room, seen from the wearer's position (0 degrees ahead, + to the right). */
internal data class Waypoint(val name: String, val distanceM: Double, val bearingDeg: Int)

/**
 * The relay's quick match against the room map: how similar the photo was, how far the view turned,
 * and whether the photo was skipped (blurred, dark or unrecognised: the last answer is repeated).
 */
internal data class QuickMatch(val similarity: Double?, val turnDeg: Double?, val skipped: Boolean) {
    /** "quick match 0.87 · turned 12°", "skipped (repeat)", or null when there is nothing to show. */
    fun summary(): String? {
        if (skipped) return "skipped (repeat)"
        val parts = listOfNotNull(
            similarity?.let { "quick match " + String.format(Locale.US, "%.2f", it) },
            turnDeg?.let { "turned ${it.roundToInt()}°" },
        )
        return if (parts.isEmpty()) null else parts.joinToString(" · ")
    }
}

/** The part of a relay /locate reply the phone shows. */
internal data class LocateReply(
    val state: String,
    val headline: String,
    val waypoints: List<Waypoint>,
    val quick: QuickMatch? = null,
) {
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
            val quick = root.get("quick")?.takeIf { it.isJsonObject }?.asJsonObject?.let { match ->
                QuickMatch(match.number("similarity"), match.number("turn_deg"), match.flag("skipped") ?: false)
            }
            return LocateReply(
                state = root.text("state")?.take(32) ?: "unknown",
                headline = root.text("headline")?.take(300) ?: "",
                waypoints = waypoints,
                quick = quick,
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

        private fun JsonObject.flag(key: String): Boolean? =
            get(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean }?.asBoolean
    }
}

/**
 * A relay or network failure, worded for the screen. Never contains the key. [status] is the HTTP
 * status when the relay answered; [unreachable] means no answer at all (cable out, wrong address).
 */
internal class RelayFailure(message: String, val status: Int? = null, val unreachable: Boolean = false) : IOException(message)

/** A camera problem, worded for the screen. */
internal class CameraFailure(message: String) : Exception(message)

/**
 * Posts one JPEG to `{relay}/locate?zone={zone}`. The key goes only in the X-Key header, never in the
 * URL, and is never logged. `fresh` and `view` are never sent, so an unchanged view is answered from
 * the relay's cheap gate. Debug builds may use cleartext loopback; everything else needs HTTPS.
 */
internal class RelayClient(baseUrl: String, private val key: String, private val zone: String, allowLocalHttp: Boolean) {
    private val base: HttpUrl = baseUrl.trim().toHttpUrl().also {
        require(it.username.isEmpty() && it.password.isEmpty() && it.query == null && it.fragment == null) { "Use a relay address without credentials, query or fragment" }
        require(it.encodedPath == "/") { "Use the relay origin without a path" }
        require(it.isHttps || LocalHosts.cleartextAllowed(it.host, allowLocalHttp)) { "HTTPS required except debug loopback or LAN" }
    }
    init {
        require(key.isNotBlank() && key.none { it == '\n' || it == '\r' }) { "A relay key is required" }
        require(ZONE.matches(zone)) { "Zone names use a-z, 0-9, _ and -" }
    }
    private val http = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS).writeTimeout(20, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS).callTimeout(60, TimeUnit.SECONDS).build()

    /** A baseline upload takes the relay's vision worker a while: generous timeouts for this call only. */
    private val slowHttp = http.newBuilder().writeTimeout(5, TimeUnit.MINUTES)
        .readTimeout(15, TimeUnit.MINUTES).callTimeout(16, TimeUnit.MINUTES).build()
    /** A room video can be large: long write and read timeouts and no overall limit. */
    private val videoHttp = http.newBuilder().writeTimeout(10, TimeUnit.MINUTES)
        .readTimeout(10, TimeUnit.MINUTES).callTimeout(0, TimeUnit.MILLISECONDS).build()

    fun request(jpeg: ByteArray): Request = Request.Builder()
        .url(base.newBuilder().addPathSegment("locate").addQueryParameter("zone", zone).build())
        .header("X-Key", key)
        .header("Accept", "application/json")
        .post(jpeg.toRequestBody(JPEG))
        .build()

    /** POST {relay}/baseline?zone={zone} with a zip of 000.jpg, 001.jpg, ...: replaces that zone's baseline. */
    fun baselineRequest(zip: ByteArray, zone: String): Request {
        require(ZONE.matches(zone)) { "Zone names use a-z, 0-9, _ and -" }
        return Request.Builder()
            .url(base.newBuilder().addPathSegment("baseline").addQueryParameter("zone", zone).build())
            .header("X-Key", key)
            .header("Accept", "application/json")
            .post(zip.toRequestBody(ZIP))
            .build()
    }

    /**
     * A room video for the laptop, sent whole as the only and last chunk of a recording session:
     * POST {relay}/recording?zone={zone}&session={session}&seq=0&final=1, the body streamed (video/mp4).
     * The relay maps the room and trains a splat at final=1; the same session sent again counts once.
     */
    fun recordingRequest(zone: String, video: RequestBody, session: String): Request {
        require(ZONE.matches(zone)) { "Zone names use a-z, 0-9, _ and -" }
        require(SESSION.matches(session)) { "Session names use A-Z, a-z, 0-9, _ and -" }
        return Request.Builder()
            .url(base.newBuilder().addPathSegment("recording").addQueryParameter("zone", zone)
                .addQueryParameter("session", session).addQueryParameter("seq", "0").addQueryParameter("final", "1").build())
            .header("X-Key", key)
            .header("Accept", "application/json")
            .post(video)
            .build()
    }

    /**
     * One chunk of an always-on recording: POST {relay}/recording?zone=&session=&seq=&final=0|1 with a
     * zip of NNNNNN_<epochMillis>.jpg frames (empty only for a final chunk with no new frames).
     */
    fun chunkRequest(zone: String, session: String, seq: Int, final: Boolean, zip: File): Request {
        require(ZONE.matches(zone)) { "Zone names use a-z, 0-9, _ and -" }
        require(SESSION.matches(session)) { "Session names use A-Z, a-z, 0-9, _ and -" }
        require(seq >= 0) { "Chunks count from 0" }
        return Request.Builder()
            .url(base.newBuilder().addPathSegment("recording").addQueryParameter("zone", zone)
                .addQueryParameter("session", session).addQueryParameter("seq", seq.toString())
                .addQueryParameter("final", if (final) "1" else "0").build())
            .header("X-Key", key)
            .header("Accept", "application/json")
            .post(zip.asRequestBody(ZIP))
            .build()
    }

    suspend fun uploadChunk(zone: String, session: String, seq: Int, final: Boolean, zip: File): RecordingReply =
        RecordingReply.parse(send(videoHttp, chunkRequest(zone, session, seq, final, zip)) { "zone $zone, chunk $seq" + if (final) " (last)" else "" })

    /** Where this client sends, for the screen: the address without the key. */
    val address: String get() = base.toString().trimEnd('/')

    /** The laptop's status for the zone's latest video: GET {relay}/recording?zone={zone}. */
    fun recordingStatusRequest(zone: String): Request {
        require(ZONE.matches(zone)) { "Zone names use a-z, 0-9, _ and -" }
        return Request.Builder()
            .url(base.newBuilder().addPathSegment("recording").addQueryParameter("zone", zone).build())
            .header("X-Key", key)
            .header("Accept", "application/json")
            .get()
            .build()
    }

    suspend fun uploadRecording(zone: String, video: RequestBody, session: String): RecordingReply =
        RecordingReply.parse(send(videoHttp, recordingRequest(zone, video, session)) { "zone $zone, whole video" })

    suspend fun recordingStatus(zone: String): RecordingReply =
        RecordingReply.parse(send(http, recordingStatusRequest(zone)))

    suspend fun locate(jpeg: ByteArray): LocateReply = LocateReply.parse(send(http, request(jpeg)) { body ->
        runCatching { LocateReply.parse(body) }.getOrNull()?.let { r -> "zone $zone: " + listOf(r.state, r.headline.take(80)).filter { it.isNotBlank() }.joinToString(", ") }
    })

    /**
     * GET {relay}/navigate?zone=&to=: where the wearer is, the way to [to] and the room for the map.
     * Without [to] it is only the map (where the wearer is and the places).
     */
    fun navigateRequest(to: String?): Request = Request.Builder()
        .url(base.newBuilder().addPathSegment("navigate").addQueryParameter("zone", zone)
            .apply { if (!to.isNullOrBlank()) addQueryParameter("to", to) }.build())
        .header("X-Key", key)
        .header("Accept", "application/json")
        .get()
        .build()

    suspend fun navigate(to: String?): com.junction.glasses.core.NavReply =
        com.junction.glasses.core.NavReply.parse(send(http, navigateRequest(to)) { body ->
            val r = com.junction.glasses.core.NavReply.parse(body)
            "zone $zone" + (to?.let { " to $it" } ?: " (map)") + ": " + when {
                !r.ok -> r.error ?: "refused"
                r.instruction != null -> r.instruction
                else -> "${r.places.size} places, " + (if (r.position != null) "wearer placed" else "wearer not placed yet")
            }
        })

    /** The zone's named places (GET {relay}/waypoints?zone=): names only, for "take me to …". Seen things excluded. */
    suspend fun placeNames(): List<String> = spots().filter { it.second != "object" }.map { it.first }

    /**
     * Every entry of GET {relay}/waypoints?zone=: (name, kind). kind is null for a named place and "object"
     * for a thing the relay has seen (the orange box), which is asked about with /find rather than walked to.
     */
    suspend fun spots(): List<Pair<String, String?>> {
        val body = send(http, Request.Builder()
            .url(base.newBuilder().addPathSegment("waypoints").addQueryParameter("zone", zone).build())
            .header("X-Key", key).header("Accept", "application/json").get().build()) { "zone $zone" }
        return try {
            JsonParser.parseString(body).asJsonObject.getAsJsonArray("waypoints")?.mapNotNull { e ->
                val o = e.takeIf { it.isJsonObject }?.asJsonObject ?: return@mapNotNull null
                val name = o.get("name")?.takeIf { it.isJsonPrimitive }?.asString ?: return@mapNotNull null
                name to o.get("kind")?.takeIf { it.isJsonPrimitive }?.asString
            }.orEmpty()
        } catch (_: Exception) { emptyList() }
    }

    /** GET {relay}/find?zone=&what=: whether and where the thing was seen, and the words to say. */
    fun findRequest(what: String): Request = Request.Builder()
        .url(base.newBuilder().addPathSegment("find").addQueryParameter("zone", zone).addQueryParameter("what", what.take(60)).build())
        .header("X-Key", key)
        .header("Accept", "application/json")
        .get()
        .build()

    suspend fun find(what: String): com.junction.glasses.core.FindReply =
        com.junction.glasses.core.FindReply.parse(send(http, findRequest(what)) { body ->
            val r = com.junction.glasses.core.FindReply.parse(body)
            "zone $zone, $what: " + when {
                r.found -> "found" + (r.place?.let { " by $it" } ?: "")
                r.looking -> "looking"
                else -> r.error ?: "not seen"
            }
        })

    suspend fun baseline(zip: ByteArray, zone: String): BaselineReply =
        BaselineReply.parse(send(slowHttp, baselineRequest(zip, zone)))

    /**
     * The reply body of a successful call; a RelayFailure (never containing the key) otherwise. Each
     * answer (or no answer) is noted for the debug panel: path, address, status and [summary] of the
     * reply. Never the key, the query or the body.
     */
    private suspend fun send(client: OkHttpClient, request: Request, summary: ((String) -> String?)? = null): String = suspendCancellableCoroutine { cont ->
        val label = traceLabel(request)
        val call = client.newCall(request)
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!call.isCanceled()) HttpTrace.record(label, 0, System.currentTimeMillis(), e.javaClass.simpleName)
                cont.resumeWithException(RelayFailure("Relay unreachable (${e.javaClass.simpleName}). Check the relay address, Wi-Fi or adb reverse.", unreachable = true))
            }
            override fun onResponse(call: Call, response: Response) {
                val code = response.code
                val outcome = runCatching { response.use { read(it) } }
                val note = outcome.fold(
                    onSuccess = { body -> runCatching { summary?.invoke(body) }.getOrNull() },
                    onFailure = { it.message },
                )
                HttpTrace.record(label, code, System.currentTimeMillis(), note)
                outcome.fold(
                    onSuccess = { cont.resume(it) },
                    onFailure = { cont.resumeWithException(it as? RelayFailure ?: RelayFailure("Relay reply could not be read.")) },
                )
            }
        })
    }

    /** "relay /locate · 127.0.0.1:8090": the path and address only, never the query or the key. */
    private fun traceLabel(request: Request): String =
        "relay ${request.url.encodedPath} · ${request.url.host}:${request.url.port}"

    private fun read(response: Response): String {
        val body = response.body ?: throw RelayFailure("Relay HTTP ${response.code}: empty reply.", status = response.code)
        val source = body.source()
        if (source.request(MAX_REPLY_BYTES + 1)) throw RelayFailure("Relay reply too large.")
        val text = source.buffer.readUtf8()
        if (!response.isSuccessful) {
            throw RelayFailure("Relay HTTP ${response.code}" + (LocateReply.errorText(text)?.let { ": $it" } ?: "."), status = response.code)
        }
        return text
    }

    companion object {
        const val DEFAULT_URL = "http://127.0.0.1:8090"
        const val DEFAULT_ZONE = "kitchen"
        private const val MAX_REPLY_BYTES = 8L * 1024 * 1024
        private val JPEG = "image/jpeg".toMediaType()
        private val ZIP = "application/zip".toMediaType()
        val VIDEO_MP4 = "video/mp4".toMediaType()
        private val ZONE = Regex("[a-z0-9_-]{1,32}")
        /** The relay's SESSION_NAME. */
        private val SESSION = Regex("[A-Za-z0-9_-]{1,64}")

        /** A recording session name from a video's file name: no extension, other characters as '-'. */
        fun sessionFor(fileName: String): String =
            fileName.substringBeforeLast('.').replace(Regex("[^A-Za-z0-9_-]"), "-").take(64).ifEmpty { "video" }

        fun isZone(name: String): Boolean = ZONE.matches(name)
    }
}

/** What the live camera has done so far, for the screen. */
internal data class LiveSnapshot<F>(
    val framesSent: Int = 0,
    val lastFrame: F? = null,
    val reply: LocateReply? = null,
    val roundTripSeconds: Double? = null,
    /** Replies per second over the last 10 s (or since the start, if shorter). */
    val framesPerSecond: Double? = null,
    val error: String? = null,
)

/**
 * The live camera loop, back to back with one request in flight: capture the newest frame, post
 * it, and as soon as the reply arrives capture and post the next one, with no delay and no queue.
 * A failed capture or post is shown and retried after [retryDelayMs]. Runs in the caller's scope
 * until [stop]. [clock] is in nanoseconds.
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
            val startedAt = clock()
            val replyTimes = ArrayDeque<Long>()
            while (isActive) {
                try {
                    val frame = capture()
                    snapshot = snapshot.copy(framesSent = snapshot.framesSent + 1, lastFrame = frame)
                    onUpdate(snapshot)
                    val sentAt = clock()
                    val reply = post(frame)
                    val repliedAt = clock()
                    replyTimes.addLast(repliedAt)
                    while (replyTimes.first() <= repliedAt - RATE_WINDOW_NS) replyTimes.removeFirst()
                    val span = minOf(RATE_WINDOW_NS, repliedAt - startedAt).coerceAtLeast(1L)
                    snapshot = snapshot.copy(reply = reply, roundTripSeconds = (repliedAt - sentAt) / 1e9,
                        framesPerSecond = replyTimes.size * 1e9 / span, error = null)
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
        const val RATE_WINDOW_NS = 10_000_000_000L
        fun describe(e: Exception): String = (e as? RelayFailure)?.message ?: (e as? CameraFailure)?.message
            ?: "Camera or connection error (${e.javaClass.simpleName})."
    }
}
