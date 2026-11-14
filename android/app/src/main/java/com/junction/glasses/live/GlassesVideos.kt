package com.junction.glasses.live

/** A video in the phone's MediaStore: only the fields the app reads. The app never modifies it. */
internal data class GlassesVideo(
    val id: Long,
    val name: String,
    val relativePath: String,
    val dateAddedSec: Long,
    val durationMs: Long,
    val sizeBytes: Long,
)

internal object GlassesVideos {
    /** Meta AI imports glasses media into a folder named "Meta AI" (Download/Meta AI/, or one under DCIM). */
    fun isMetaAi(relativePath: String?): Boolean = relativePath?.contains("Meta AI", ignoreCase = true) == true

    fun newestFirst(videos: List<GlassesVideo>): List<GlassesVideo> =
        videos.sortedWith(compareByDescending<GlassesVideo> { it.dateAddedSec }.thenByDescending { it.id })

    /** The Meta AI videos among [videos], newest first, at most [limit]. */
    fun metaAiNewestFirst(videos: List<GlassesVideo>, limit: Int = Int.MAX_VALUE): List<GlassesVideo> =
        newestFirst(videos.filter { isMetaAi(it.relativePath) }).take(limit)
}

/**
 * Which new glasses video to send by itself. Armed ("Record video" with the Glasses source), the
 * first Meta AI video added at or after the arm time is sent, then the caller disarms; with
 * "Auto-send" on, every Meta AI video added while the app runs is sent, oldest first. Each video is
 * sent at most once per app run, remembered by its MediaStore id in memory only. Arming expires
 * after [armTimeoutMs].
 */
internal class GlassesVideoWatch(private val startedAtSec: Long, private val armTimeoutMs: Long = ARM_TIMEOUT_MS) {
    private val sent = mutableSetOf<Long>()
    var armedAtMs: Long? = null
        private set
    val armed: Boolean get() = armedAtMs != null

    fun arm(nowMs: Long) { armedAtMs = nowMs }
    fun disarm() { armedAtMs = null }
    fun expired(nowMs: Long): Boolean = armedAtMs?.let { nowMs - it >= armTimeoutMs } ?: false

    /** The next video to send automatically, or null. Call [markSent] when sending it starts. */
    fun next(videos: List<GlassesVideo>, autoSend: Boolean): GlassesVideo? {
        val armedAt = armedAtMs
        val sinceSec = when {
            armedAt != null -> armedAt / 1000
            autoSend -> startedAtSec
            else -> return null
        }
        return GlassesVideos.metaAiNewestFirst(videos).lastOrNull { it.dateAddedSec >= sinceSec && it.id !in sent }
    }

    fun markSent(id: Long) { sent += id }
    fun wasSent(id: Long): Boolean = id in sent

    companion object {
        const val ARM_TIMEOUT_MS = 10 * 60 * 1000L
    }
}

/** A video still being imported is settled once its size has stayed the same, above zero, for [quietMs]. */
internal class SizeSettle(private val quietMs: Long = 3_000) {
    private var lastSize = -1L
    private var sinceMs = 0L

    fun settled(nowMs: Long, size: Long): Boolean {
        if (size != lastSize) {
            lastSize = size
            sinceMs = nowMs
            return false
        }
        return size > 0 && nowMs - sinceMs >= quietMs
    }
}
