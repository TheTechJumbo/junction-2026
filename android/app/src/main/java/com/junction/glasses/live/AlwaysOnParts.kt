package com.junction.glasses.live

import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Locale
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * One chunk of an always-on recording, as the relay's POST /recording takes it: a stored zip of
 * frames named NNNNNN_<epochMillis>.jpg, NNNNNN counting every frame of the session from
 * [firstIndex]. It is due for upload after [maxFrames] frames or [maxAgeMs] since its first frame.
 * Built in memory (100 frames at 504x896 is about 7 MB).
 */
internal class ChunkBuilder(
    private val firstIndex: Long,
    private val maxFrames: Int = MAX_FRAMES,
    private val maxAgeMs: Long = MAX_AGE_MS,
) {
    private val buffer = ByteArrayOutputStream(256 * 1024)
    private val zip = ZipOutputStream(buffer)
    var frames = 0
        private set
    /** Monotonic time of the first frame (ms), the start of this chunk's [maxAgeMs]. */
    private var openedAtMs: Long? = null
    val nextIndex: Long get() = firstIndex + frames

    fun add(jpeg: ByteArray, epochMillis: Long, nowMs: Long) {
        if (openedAtMs == null) openedAtMs = nowMs
        val checksum = CRC32().apply { update(jpeg, 0, jpeg.size) }
        val entry = ZipEntry(name(firstIndex + frames, epochMillis)).apply {
            method = ZipEntry.STORED; size = jpeg.size.toLong(); compressedSize = jpeg.size.toLong(); crc = checksum.value
        }
        zip.putNextEntry(entry)
        zip.write(jpeg)
        zip.closeEntry()
        frames++
    }

    fun due(nowMs: Long): Boolean = frames >= maxFrames || (openedAtMs?.let { nowMs - it >= maxAgeMs } ?: false)

    /** The finished zip; nothing can be added afterwards. */
    fun finish(): ByteArray { zip.finish(); return buffer.toByteArray() }

    companion object {
        /** Chunk every 10 s or 100 frames (10 s at 10 frames/s). */
        const val MAX_FRAMES = 100
        const val MAX_AGE_MS = 10_000L
        fun name(index: Long, epochMillis: Long): String = String.format(Locale.US, "%06d_%d.jpg", index, epochMillis)
    }
}

/** A sealed chunk waiting in app storage for the relay. */
internal data class QueuedChunk(val file: File, val session: String, val seq: Int, val final: Boolean, val zone: String) {
    val bytes: Long get() = file.length()
}

/**
 * Chunks waiting for the relay, kept in app storage until uploaded, in upload order: by session (the
 * names start with their start time) and then by seq. File name `<session>.<seq>.<0|1>.<zone>.zip`:
 * neither names nor zones can contain a dot. Over [capBytes] the oldest chunks are dropped (counted in
 * [dropped]) so the newest of the room survive a long outage. Files survive app restarts.
 */
internal class ChunkQueue(private val dir: File, private val capBytes: Long = CAP_BYTES) {
    var dropped = 0
        private set

    init { dir.mkdirs() }

    @Synchronized
    fun add(session: String, seq: Int, final: Boolean, zone: String, zip: ByteArray): QueuedChunk {
        val name = String.format(Locale.US, "%s.%06d.%d.%s.zip", session, seq, if (final) 1 else 0, zone)
        val part = File(dir, "$name.part")
        part.writeBytes(zip)
        val file = File(dir, name)
        check(part.renameTo(file)) { "Could not store a chunk" }
        enforceCap()
        return QueuedChunk(file, session, seq, final, zone)
    }

    /** Waiting chunks, oldest first. */
    @Synchronized
    fun pending(): List<QueuedChunk> = (dir.listFiles() ?: emptyArray())
        .mapNotNull { parse(it) }
        .sortedWith(compareBy({ it.session }, { it.seq }))

    fun next(): QueuedChunk? = pending().firstOrNull()

    @Synchronized
    fun remove(chunk: QueuedChunk) { chunk.file.delete() }

    fun totalBytes(): Long = pending().sumOf { it.bytes }

    @Synchronized
    private fun enforceCap() {
        val waiting = pending().toMutableList()
        var total = waiting.sumOf { it.bytes }
        // The newest chunk always stays, even if it alone is over the cap.
        while (total > capBytes && waiting.size > 1) {
            val oldest = waiting.removeAt(0)
            total -= oldest.bytes
            oldest.file.delete()
            dropped++
        }
    }

    private fun parse(file: File): QueuedChunk? {
        val match = NAME.matchEntire(file.name) ?: return null
        val (session, seq, final, zone) = match.destructured
        return QueuedChunk(file, session, seq.toInt(), final == "1", zone)
    }

    companion object {
        const val CAP_BYTES = 300L * 1024 * 1024
        private val NAME = Regex("""([A-Za-z0-9_-]{1,64})\.(\d{6})\.([01])\.([a-z0-9_-]{1,32})\.zip""")
    }
}

/** Waits between upload retries: 2 s, 5 s, 10 s, 30 s, then every 60 s. */
internal object UploadBackoff {
    private val STEPS_MS = longArrayOf(2_000, 5_000, 10_000, 30_000, 60_000)
    fun delayMs(failuresInARow: Int): Long = STEPS_MS[(failuresInARow - 1).coerceIn(0, STEPS_MS.size - 1)]

    /** A refusal that retrying cannot fix (a malformed or too large chunk): drop it and go on. */
    fun dropsChunk(status: Int?): Boolean = status == 400 || status == 413
}

/**
 * The relay over USB first (127.0.0.1 through adb reverse) and over Wi-Fi ([lan], the laptop's LAN
 * address) when the USB address does not answer, so recording keeps going when the cable comes out.
 * Only "no answer" switches; an HTTP error from a relay that answered is final. After switching to the
 * LAN the USB address is tried first again after [retryPrimaryMs]. [clock] is in milliseconds.
 */
internal class FailoverRelay<C>(
    private val primary: C,
    private val lan: C?,
    private val unreachable: (Exception) -> Boolean = { (it as? RelayFailure)?.unreachable == true },
    private val clock: () -> Long = { System.nanoTime() / 1_000_000 },
    private val retryPrimaryMs: Long = RETRY_PRIMARY_MS,
) {
    @Volatile var usingLan = false
        private set
    private var lanSinceMs = 0L

    suspend fun <T> call(block: suspend (C) -> T): T {
        val lanFirst = usingLan && lan != null && clock() - lanSinceMs < retryPrimaryMs
        val order = if (lanFirst) listOf(lan!! to true, primary to false) else listOfNotNull(primary to false, lan?.let { it to true })
        var last: Exception? = null
        for ((client, isLan) in order) {
            try {
                val result = block(client)
                if (isLan && (!usingLan || !lanFirst)) lanSinceMs = clock()
                usingLan = isLan
                return result
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException || !unreachable(e)) throw e
                last = e
            }
        }
        throw last ?: IllegalStateException("No relay address")
    }

    companion object { const val RETRY_PRIMARY_MS = 30_000L }
}

/** The always-on recording's decisions that need no Android: when it starts, which camera, heat. */
internal object AlwaysOnRules {
    /**
     * Opt-in: it starts by itself when the app opens only if the user's last choice was Start
     * ([wanted], remembered across app restarts; Stop or `--ez always_on false` clear it), a relay key
     * is set and it is not already running. A fresh install never records until Start is tapped.
     */
    fun autoStart(wanted: Boolean, relayKeySet: Boolean, recording: Boolean): Boolean =
        wanted && relayKeySet && !recording

    /** The glasses, unless the phone was picked in the app; a failing camera never switches by itself. */
    fun source(picked: CameraSource): CameraSource = picked

    /** What the card says while the glasses cannot give frames yet; null when there is nothing to wait for. */
    fun glassesWaiting(registration: String, connection: String, camera: String): String? = when {
        registration != "REGISTERED" -> "Waiting for the glasses: open the app and approve it in Meta AI."
        connection != "STARTED" -> "Waiting for the glasses: put them on or unfold them (they are $connection)."
        camera != "STREAMING" -> "Starting the glasses camera…"
        else -> null
    }

    const val HOT_C = 45.0
    const val COOL_C = 43.0

    /** Capture pauses above 45 °C and resumes below 43 °C (no flapping at the limit). */
    fun heatPaused(temperatureC: Double?, pausedNow: Boolean): Boolean = when {
        temperatureC == null -> false
        pausedNow -> temperatureC > COOL_C
        else -> temperatureC > HOT_C
    }
}
