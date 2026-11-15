package com.junction.glasses.live

import org.junit.Assert.*
import org.junit.Test

/** Which glasses videos Meta AI imported, and which of them the app sends by itself. */
class GlassesVideosTest {
    private fun video(id: Long, path: String, addedSec: Long) = GlassesVideo(id, "v$id.mp4", path, addedSec, 5_000, 1_000)

    @Test fun onlyMetaAiFoldersCountAsGlassesVideos() {
        assertTrue(GlassesVideos.isMetaAi("Download/Meta AI/"))
        assertTrue(GlassesVideos.isMetaAi("DCIM/Meta AI/"))
        assertTrue(GlassesVideos.isMetaAi("download/meta ai/"))
        assertFalse(GlassesVideos.isMetaAi("DCIM/Camera/"))
        assertFalse(GlassesVideos.isMetaAi("Movies/"))
        assertFalse(GlassesVideos.isMetaAi(null))
    }

    @Test fun glassesVideosAreListedNewestFirst() {
        val videos = listOf(
            video(1, "Download/Meta AI/", 100), video(2, "DCIM/Camera/", 300),
            video(3, "Download/Meta AI/", 200), video(4, "DCIM/Meta AI/", 200),
        )
        assertEquals(listOf(4L, 3L, 1L), GlassesVideos.metaAiNewestFirst(videos).map { it.id })
        assertEquals(listOf(4L), GlassesVideos.metaAiNewestFirst(videos, limit = 1).map { it.id })
    }

    @Test fun autoSendSendsEachNewMetaAiVideoOnlyOncePerRun() {
        val watch = GlassesVideoWatch(startedAtSec = 1_000)
        val videos = listOf(
            video(1, "Download/Meta AI/", 900), // before the app started: never sent by itself
            video(2, "Download/Meta AI/", 1_010),
            video(3, "Download/Meta AI/", 1_020),
            video(4, "DCIM/Camera/", 1_030), // the phone's own camera: not a glasses video
        )
        assertNull(watch.next(videos, autoSend = false))
        assertEquals(2L, watch.next(videos, autoSend = true)?.id)
        watch.markSent(2)
        assertEquals(3L, watch.next(videos, autoSend = true)?.id)
        watch.markSent(3)
        assertNull(watch.next(videos, autoSend = true))
        assertTrue(watch.wasSent(2))
        assertFalse(watch.wasSent(1))
    }

    @Test fun armedRecordingTakesTheFirstMetaAiVideoAddedAfterArmingAndExpiresAfterTenMinutes() {
        val watch = GlassesVideoWatch(startedAtSec = 1_000)
        val armedAtMs = 2_000_000L
        watch.arm(armedAtMs)
        assertTrue(watch.armed)
        val before = video(1, "Download/Meta AI/", 1_500)
        val after = video(2, "Download/Meta AI/", 2_030)
        assertNull(watch.next(listOf(before), autoSend = false))
        assertEquals(2L, watch.next(listOf(before, after), autoSend = false)?.id)
        assertFalse(watch.expired(armedAtMs + GlassesVideoWatch.ARM_TIMEOUT_MS - 1))
        assertTrue(watch.expired(armedAtMs + GlassesVideoWatch.ARM_TIMEOUT_MS))
        assertEquals(10 * 60 * 1000L, GlassesVideoWatch.ARM_TIMEOUT_MS)
        watch.disarm()
        assertFalse(watch.armed)
        assertFalse(watch.expired(Long.MAX_VALUE))
        assertNull(watch.next(listOf(after), autoSend = false))
    }

    @Test fun aVideoIsSentOnlyOnceItsSizeHasStoppedGrowingForThreeSeconds() {
        val settle = SizeSettle()
        assertFalse(settle.settled(0, 100))
        assertFalse(settle.settled(1_000, 5_000))
        assertFalse(settle.settled(2_000, 5_000))
        assertFalse(settle.settled(3_999, 5_000))
        assertTrue(settle.settled(4_000, 5_000))
        val empty = SizeSettle()
        assertFalse(empty.settled(0, 0))
        assertFalse(empty.settled(10_000, 0))
    }
}
