package com.junction.glasses.live

import com.google.gson.JsonParser
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import java.io.ByteArrayOutputStream
import java.util.Locale
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * A room recording as the relay's /baseline takes it (as `simulate.py as_zip` builds it): an
 * uncompressed (stored) zip of 000.jpg, 001.jpg, ... in capture order, built in memory only.
 * [add] refuses a frame that would take the finished zip over [maxBytes] or past [maxFrames].
 */
internal class FrameZip(private val maxBytes: Int = MAX_BYTES, private val maxFrames: Int = MAX_FRAMES) {
    private val buffer = ByteArrayOutputStream(64 * 1024)
    private val zip = ZipOutputStream(buffer)
    private var finished: ByteArray? = null
    var count = 0
        private set
    /** Bytes written so far (the finished zip adds its directory: a few dozen bytes per frame). */
    val size: Int get() = finished?.size ?: buffer.size()

    fun add(jpeg: ByteArray): Boolean {
        check(finished == null) { "The zip is already finished" }
        val projected = buffer.size().toLong() + LOCAL_ENTRY + jpeg.size + CENTRAL_ENTRY * (count + 1L) + END_RECORD
        if (count >= maxFrames || projected > maxBytes) return false
        val checksum = CRC32()
        checksum.update(jpeg, 0, jpeg.size)
        val entry = ZipEntry(String.format(Locale.US, "%03d.jpg", count))
        entry.method = ZipEntry.STORED
        entry.size = jpeg.size.toLong()
        entry.compressedSize = jpeg.size.toLong()
        entry.crc = checksum.value
        zip.putNextEntry(entry)
        zip.write(jpeg)
        zip.closeEntry()
        count++
        return true
    }

    /** The finished zip. No frame can be added afterwards. */
    fun bytes(): ByteArray = finished ?: run {
        zip.finish()
        buffer.toByteArray()
    }.also { finished = it }

    companion object {
        const val MAX_BYTES = 40 * 1024 * 1024
        const val MAX_FRAMES = 999
        // Generous per-entry overheads, so the finished zip always stays under the cap.
        private const val LOCAL_ENTRY = 64L
        private const val CENTRAL_ENTRY = 96L
        private const val END_RECORD = 64L
    }
}

internal data class RecordProgress(val frames: Int, val bytes: Int, val elapsedMs: Long, val failedFrames: Int)

/**
 * Records the room for [durationMs] (clamped to 10 to 60 s) at about [framesPerSecond]: frame k is
 * taken at k / rate seconds, or as soon as the camera can after that. A failed frame is counted and
 * skipped. Stops early when the zip is full. Cancelling the coroutine stops it; nothing is kept.
 * [clock] and [pause] are in nanoseconds.
 */
internal class RoomRecorder(
    private val capture: suspend () -> ByteArray,
    durationMs: Long,
    private val framesPerSecond: Int = FRAMES_PER_SECOND,
    private val maxBytes: Int = FrameZip.MAX_BYTES,
    private val clock: () -> Long = System::nanoTime,
    private val pause: suspend (Long) -> Unit = { delay((it + 999_999) / 1_000_000) },
) {
    val durationMs: Long = durationMs.coerceIn(MIN_DURATION_MS, MAX_DURATION_MS)

    suspend fun record(onProgress: (RecordProgress) -> Unit): FrameZip {
        val zip = FrameZip(maxBytes)
        val durationNs = durationMs * 1_000_000
        val planned = (durationMs * framesPerSecond / 1000).toInt().coerceAtLeast(1)
        val start = clock()
        var failed = 0
        for (k in 0 until planned) {
            val wait = start + durationNs * k / planned - clock()
            if (wait > 0) pause(wait)
            if (clock() - start >= durationNs) break
            val jpeg = try {
                capture()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                failed++
                null
            }
            if (jpeg != null && !zip.add(jpeg)) break
            onProgress(RecordProgress(zip.count, zip.size, (clock() - start) / 1_000_000, failed))
        }
        return zip
    }

    companion object {
        const val FRAMES_PER_SECOND = 3
        const val MIN_DURATION_MS = 10_000L
        const val MAX_DURATION_MS = 60_000L
        const val DEFAULT_DURATION_MS = 30_000L
        /** The relay's worker refuses a baseline with fewer frames than this. */
        const val MIN_FRAMES = 6
    }
}

/** The relay's answer to a new baseline: how many frames it kept for the zone. */
internal data class BaselineReply(val zone: String?, val framesKept: Int?) {
    companion object {
        fun parse(body: String): BaselineReply {
            val root = try { JsonParser.parseString(body).asJsonObject } catch (_: Exception) {
                throw RelayFailure("Relay reply was not JSON.")
            }
            val ok = root.get("ok")
            if (ok != null && ok.isJsonPrimitive && ok.asJsonPrimitive.isBoolean && !ok.asBoolean) {
                throw RelayFailure("Relay refused the recording" + (LocateReply.errorText(body)?.let { ": $it" } ?: "."))
            }
            val zone = root.get("zone")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString?.take(32)
            val frames = root.get("frames")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asInt
            return BaselineReply(zone, frames)
        }
    }
}
