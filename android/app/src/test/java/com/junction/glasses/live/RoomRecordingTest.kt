package com.junction.glasses.live

import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.util.Locale
import java.util.concurrent.TimeUnit
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream

/** Room recording without a camera or a relay: the zip, the timer, the camera choice and the upload. */
class RoomRecordingTest {
    @Test fun zipHoldsTheFramesAs0000JpgOnwardInCaptureOrder() {
        val zip = FrameZip()
        val frames = List(12) { i -> ByteArray(100 + i) { (i + it).toByte() } }
        frames.forEach { assertTrue(zip.add(it)) }
        assertEquals(12, zip.count)
        val bytes = zip.bytes()
        val names = mutableListOf<String>()
        val contents = mutableListOf<ByteArray>()
        ZipInputStream(ByteArrayInputStream(bytes)).use { input ->
            while (true) {
                val entry = input.nextEntry ?: break
                assertEquals(ZipEntry.STORED, entry.method)
                names.add(entry.name)
                contents.add(input.readBytes())
            }
        }
        assertEquals((0 until 12).map { String.format(Locale.US, "%04d.jpg", it) }, names)
        frames.forEachIndexed { i, frame -> assertArrayEquals(frame, contents[i]) }
        assertSame(bytes, zip.bytes())
        assertThrows(IllegalStateException::class.java) { zip.add(byteArrayOf(1)) }
    }

    @Test fun zipRefusesFramesBeyondItsSizeOrCountCap() {
        val zip = FrameZip(maxBytes = 1000)
        val frame = ByteArray(300) { 7 }
        assertTrue(zip.add(frame))
        assertTrue(zip.add(frame))
        assertFalse(zip.add(frame))
        assertEquals(2, zip.count)
        assertTrue(zip.bytes().size <= 1000)
        val few = FrameZip(maxFrames = 3)
        repeat(3) { assertTrue(few.add(byteArrayOf(1))) }
        assertFalse(few.add(byteArrayOf(1)))
        assertEquals(3, few.count)
    }

    @Test fun recordingTakesTenFramesPerSecondForTheChosenTime() {
        runBlocking {
            var now = 0L
            val captureTimes = mutableListOf<Long>()
            val progress = mutableListOf<RecordProgress>()
            val recorder = RoomRecorder(capture = { captureTimes += now; ByteArray(1000) }, durationMs = 30_000,
                clock = { now }, pause = { now += it })
            val zip = recorder.record { progress += it }
            assertEquals(300, zip.count)
            assertEquals(300, captureTimes.size)
            assertEquals(0L, captureTimes.first())
            assertTrue(captureTimes.last() < 30_000_000_000L)
            captureTimes.zipWithNext().forEach { (a, b) -> assertEquals(100_000_000.0, (b - a).toDouble(), 1.0) }
            assertEquals(300, progress.last().frames)
            assertEquals(0, progress.last().failedFrames)
            assertEquals(0, progress.last().skippedFrames)
        }
    }

    @Test fun aSlowCameraGivesFewerFramesButTheRecordingStillEndsOnTime() {
        runBlocking {
            var now = 0L
            val recorder = RoomRecorder(capture = { now += 1_000_000_000L; ByteArray(10) }, durationMs = 10_000,
                clock = { now }, pause = { now += it })
            val zip = recorder.record {}
            assertEquals(10, zip.count)
            assertEquals(10_000_000_000L, now)
        }
    }

    @Test fun failedFramesAreCountedAndSkipped() {
        runBlocking {
            var now = 0L
            var calls = 0
            val progress = mutableListOf<RecordProgress>()
            val recorder = RoomRecorder(capture = {
                calls++
                if (calls % 2 == 0) throw CameraFailure("Glasses not connected.")
                ByteArray(5)
            }, durationMs = 10_000, clock = { now }, pause = { now += it })
            val zip = recorder.record { progress += it }
            assertEquals(100, calls)
            assertEquals(50, zip.count)
            assertEquals(50, progress.last().failedFrames)
            assertEquals(0, progress.last().skippedFrames)
        }
    }

    @Test fun aTickWithNoNewFrameIsSkippedNotWaitedForOrRepeated() {
        runBlocking {
            var now = 0L
            var calls = 0
            val progress = mutableListOf<RecordProgress>()
            // The stream has a new frame on two ticks out of three.
            val recorder = RoomRecorder(capture = { calls++; if (calls % 3 == 0) null else ByteArray(5) },
                durationMs = 10_000, clock = { now }, pause = { now += it })
            val zip = recorder.record { progress += it }
            assertEquals(100, calls)
            assertEquals(67, zip.count)
            assertEquals(33, progress.last().skippedFrames)
            assertEquals(0, progress.last().failedFrames)
            assertTrue(now < 10_000_000_000L)
        }
    }

    @Test fun sixtySecondsAtTenFramesPerSecondFitTheZip() {
        val zip = FrameZip()
        val frame = ByteArray(70 * 1024)
        repeat(600) { assertTrue(zip.add(frame)) }
        assertTrue(zip.bytes().size < FrameZip.MAX_BYTES)
        assertTrue(FrameZip.MAX_FRAMES >= 600)
    }

    @Test fun recordingTimeIsKeptBetweenTenAndSixtySeconds() {
        assertEquals(10_000L, RoomRecorder(capture = { ByteArray(1) }, durationMs = 2_000).durationMs)
        assertEquals(60_000L, RoomRecorder(capture = { ByteArray(1) }, durationMs = 600_000).durationMs)
        assertEquals(30_000L, RoomRecorder(capture = { ByteArray(1) }, durationMs = RoomRecorder.DEFAULT_DURATION_MS).durationMs)
    }

    @Test fun cancellingTheRecordingStopsTakingFrames() {
        runBlocking {
            var captures = 0
            val recorder = RoomRecorder(capture = { captures++; ByteArray(5) }, durationMs = 10_000)
            val job = launch { recorder.record {} }
            yield()
            assertEquals(1, captures)
            job.cancel()
            job.join()
            assertEquals(1, captures)
        }
    }

    @Test fun onlyTheSelectedSourceIsCreatedAndItsFailureNeverFallsBackToTheOther() {
        runBlocking {
            val log = mutableListOf<String>()
            val created = mutableListOf<CameraSource>()
            val sources = LiveSources<String>(CameraSource.PHONE) { source ->
                created += source
                when (source) {
                    CameraSource.PHONE -> FakeCamera("phone", log, failing = false)
                    CameraSource.GLASSES -> FakeCamera("glasses", log, failing = true)
                }
            }
            assertFalse(sources.started)
            sources.select(CameraSource.GLASSES)
            assertTrue(created.isEmpty())
            val error = runCatching { sources.capture() }.exceptionOrNull()
            assertEquals("glasses not connected", error?.message)
            assertEquals(listOf(CameraSource.GLASSES), created)
            sources.select(CameraSource.PHONE)
            assertEquals("phone frame", sources.capture())
            sources.select(CameraSource.PHONE)
            assertEquals(listOf("glasses capture", "glasses stop", "phone capture"), log)
            sources.stop()
            assertFalse(sources.started)
            assertEquals("phone stop", log.last())
            assertEquals(listOf(CameraSource.GLASSES, CameraSource.PHONE), created)
        }
    }

    @Test fun baselineRequestCarriesTheZipWithTheKeyOnlyInTheHeader() {
        val client = RelayClient("http://127.0.0.1:8090", "synthetic-relay-key", "kitchen", allowLocalHttp = true)
        val request = client.baselineRequest(byteArrayOf(80, 75, 3, 4), "living_room")
        assertEquals("POST", request.method)
        assertEquals("/baseline", request.url.encodedPath)
        assertEquals(setOf("zone"), request.url.queryParameterNames)
        assertEquals("living_room", request.url.queryParameter("zone"))
        assertFalse(request.url.toString().contains("synthetic-relay-key"))
        assertEquals("synthetic-relay-key", request.header("X-Key"))
        assertEquals("application/zip", request.body!!.contentType().toString())
        assertThrows(IllegalArgumentException::class.java) { client.baselineRequest(byteArrayOf(1), "Kitchen&fresh=1") }
        assertTrue(RelayClient.isZone("kitchen"))
        assertFalse(RelayClient.isZone("my kitchen"))
    }

    @Test fun baselineUploadReportsTheFramesTheRelayKeptOrItsError() {
        runBlocking {
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setHeader("Content-Type", "application/json")
                    .setBody("""{"ok":true,"zone":"kitchen","frames":24,"seconds":{"total":41.2}}"""))
                server.enqueue(MockResponse().setResponseCode(502).setHeader("Content-Type", "application/json")
                    .setBody("""{"ok":false,"error":"The baseline clip gave only 3 frames. Record a longer one."}"""))
                server.start()
                val client = RelayClient(server.url("/").toString(), "synthetic-relay-key", "kitchen", allowLocalHttp = true)
                val zip = FrameZip().apply { repeat(6) { add(byteArrayOf(-1, -40, it.toByte())) } }.bytes()
                assertEquals(BaselineReply("kitchen", 24), client.baseline(zip, "kitchen"))
                val request = server.takeRequest(1, TimeUnit.SECONDS)!!
                assertEquals("/baseline?zone=kitchen", request.path)
                assertEquals("synthetic-relay-key", request.getHeader("X-Key"))
                assertEquals("application/zip", request.getHeader("Content-Type"))
                assertArrayEquals(zip, request.body.readByteArray())
                val failure = try { client.baseline(zip, "kitchen"); null } catch (e: RelayFailure) { e }
                assertEquals("Relay HTTP 502: The baseline clip gave only 3 frames. Record a longer one.", failure?.message)
            }
        }
    }

    private class FakeCamera(private val name: String, private val log: MutableList<String>, private val failing: Boolean) : FrameCamera<String> {
        override suspend fun capture(): String {
            log += "$name capture"
            if (failing) throw CameraFailure("$name not connected")
            return "$name frame"
        }
        override fun stop() { log += "$name stop" }
    }
}
