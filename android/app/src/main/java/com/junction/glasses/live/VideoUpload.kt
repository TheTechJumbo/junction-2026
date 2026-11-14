package com.junction.glasses.live

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.CancellationException
import okhttp3.MediaType
import okhttp3.RequestBody
import okio.BufferedSink
import okio.Source
import okio.source
import java.io.File
import java.io.InputStream
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Names of room videos in the app's recordings folder: room-<zone>-<yyyyMMdd-HHmmss>.mp4. */
internal object RecordingNames {
    private val STAMP = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss", Locale.US)
    private val NAME = Regex("room-([a-z0-9_-]{1,32})-(\\d{8}-\\d{6})\\.mp4")

    fun fileName(zone: String, at: LocalDateTime): String {
        require(RelayClient.isZone(zone)) { "Zone names use a-z, 0-9, _ and -" }
        return "room-$zone-${at.format(STAMP)}.mp4"
    }

    fun isRecording(name: String): Boolean = NAME.matches(name)

    /** The zone a recording was made for, from its file name; null for any other file. */
    fun zoneOf(name: String): String? = NAME.matchEntire(name)?.groupValues?.get(1)

    /** How to copy a recording off the phone when the upload failed. */
    fun adbPull(packageName: String, name: String): String =
        "adb pull /sdcard/Android/data/$packageName/files/recordings/$name"
}

/** Copies [source] into [sink] a chunk at a time, reporting (bytes sent, total) after each chunk. */
private fun copyWithProgress(source: Source, sink: BufferedSink, total: Long, onProgress: (Long, Long) -> Unit) {
    var sent = 0L
    while (true) {
        val read = source.read(sink.buffer, UPLOAD_CHUNK_BYTES)
        if (read == -1L) break
        sent += read
        sink.emitCompleteSegments()
        onProgress(sent, total)
    }
}

private const val UPLOAD_CHUNK_BYTES = 256L * 1024

/**
 * Streams a file as a request body, a chunk at a time, so a long video is never held in memory.
 * [onProgress] gets (bytes sent, total bytes) on the network thread. Repeatable if OkHttp retries.
 */
internal class FileUploadBody(
    private val file: File,
    private val type: MediaType,
    private val onProgress: (Long, Long) -> Unit,
) : RequestBody() {
    override fun contentType(): MediaType = type
    override fun contentLength(): Long = file.length()
    override fun writeTo(sink: BufferedSink) {
        file.source().use { copyWithProgress(it, sink, contentLength(), onProgress) }
    }
}

/**
 * Streams a video another app owns (a content URI) as a request body, opened afresh for each write
 * and read a chunk at a time: never the whole video in memory, and the source is only read.
 * [length] is the size MediaStore reports, or 0 or less when unknown (then sent chunked).
 */
internal class StreamUploadBody(
    private val open: () -> InputStream,
    private val length: Long,
    private val type: MediaType,
    private val onProgress: (Long, Long) -> Unit,
) : RequestBody() {
    override fun contentType(): MediaType = type
    override fun contentLength(): Long = if (length > 0) length else -1L
    override fun writeTo(sink: BufferedSink) {
        open().source().use { copyWithProgress(it, sink, length, onProgress) }
    }
}

/** The relay's answer about a room video: `{"ok", "id", "zone", "seconds", "status", "viewer"}`. */
internal data class RecordingReply(
    val id: String?,
    val zone: String?,
    val seconds: Double?,
    val status: String?,
    val viewer: String?,
) {
    /** The page to open on the laptop: the relay address plus the viewer path the relay returned. */
    fun viewerUrl(relayUrl: String): String? =
        viewer?.let { relayUrl.trim().trimEnd('/') + (if (it.startsWith("/")) it else "/$it") }

    companion object {
        fun parse(body: String): RecordingReply {
            val root = try { JsonParser.parseString(body).asJsonObject } catch (_: Exception) {
                throw RelayFailure("Relay reply was not JSON.")
            }
            val ok = root.get("ok")
            if (ok != null && ok.isJsonPrimitive && ok.asJsonPrimitive.isBoolean && !ok.asBoolean) {
                throw RelayFailure("Relay refused the video" + (LocateReply.errorText(body)?.let { ": $it" } ?: "."))
            }
            return RecordingReply(
                id = (root.value("id") ?: root.value("session"))?.take(64),
                zone = root.value("zone")?.take(32),
                seconds = root.get("seconds")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asDouble?.takeIf { it.isFinite() },
                status = root.value("status")?.take(200),
                viewer = root.value("viewer")?.take(500),
            )
        }

        private fun JsonObject.value(key: String): String? =
            get(key)?.takeIf { it.isJsonPrimitive }?.asString
    }
}

/** What happened to one upload: sent (and maybe deleted from the phone) or failed (the video stays). */
internal sealed class UploadOutcome {
    data class Sent(val reply: RecordingReply, val deletedFromPhone: Boolean) : UploadOutcome()
    data class Failed(val message: String, val adbPull: String) : UploadOutcome()
}

/**
 * Sends a finished phone video (a file in the app's recordings folder) to the laptop. After a
 * successful upload it is deleted from the phone unless kept; after a failure it always stays, with
 * an adb pull command, and the same call can simply be made again to retry.
 */
internal class RecordingUploader(
    private val packageName: String,
    /** The zone for a video whose file name does not name one (any video is accepted). */
    private val defaultZone: String? = null,
    private val upload: suspend (file: File, zone: String, onProgress: (Long, Long) -> Unit) -> RecordingReply,
) {
    suspend fun send(file: File, keepOnPhone: Boolean, onProgress: (Long, Long) -> Unit): UploadOutcome {
        val pull = RecordingNames.adbPull(packageName, file.name)
        val zone = RecordingNames.zoneOf(file.name) ?: defaultZone ?: return UploadOutcome.Failed("No zone for ${file.name}.", pull)
        if (!file.isFile) return UploadOutcome.Failed("The video is no longer on the phone.", pull)
        val reply = try {
            upload(file, zone, onProgress)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return UploadOutcome.Failed(LiveRelayLoop.describe(e), pull)
        }
        val delete = deleteAfterUpload(uploaded = true, keepOnPhone = keepOnPhone)
        return UploadOutcome.Sent(reply, delete && file.delete())
    }

    companion object {
        /** Only a successful upload without "Keep video on phone" deletes the video. */
        fun deleteAfterUpload(uploaded: Boolean, keepOnPhone: Boolean): Boolean = uploaded && !keepOnPhone
    }
}
