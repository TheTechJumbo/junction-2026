package com.junction.glasses.live

import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.util.zip.ZipInputStream

/** The always-on recording's pure parts: chunks, the upload queue, retries, USB/Wi-Fi and its rules. */
class AlwaysOnPartsTest {
    @get:Rule val folder = TemporaryFolder()

    private fun names(zip: ByteArray): List<String> = ZipInputStream(ByteArrayInputStream(zip)).use { z ->
        generateSequence { z.nextEntry }.map { it.name }.toList()
    }

    @Test fun chunkFramesAreNamedBySessionIndexAndEpochMillis() {
        val chunk = ChunkBuilder(firstIndex = 200)
        chunk.add(ByteArray(10) { 1 }, epochMillis = 1_794_695_190_228, nowMs = 0)
        chunk.add(ByteArray(20) { 2 }, epochMillis = 1_794_695_190_328, nowMs = 100)
        chunk.add(ByteArray(30) { 3 }, epochMillis = 1_794_695_190_428, nowMs = 200)
        assertEquals(3, chunk.frames)
        assertEquals(203L, chunk.nextIndex)
        assertEquals(listOf("000200_1794695190228.jpg", "000201_1794695190328.jpg", "000202_1794695190428.jpg"), names(chunk.finish()))
        assertTrue(Regex("""\d{6}_\d+\.jpg""").matches(ChunkBuilder.name(5, 1_794_695_190_228)))
    }

    @Test fun aChunkIsDueAfterTenSecondsOrAHundredFrames() {
        val timed = ChunkBuilder(0)
        assertFalse("an empty chunk is never due", timed.due(1_000_000))
        timed.add(ByteArray(1), 1, nowMs = 5_000)
        assertFalse(timed.due(14_999))
        assertTrue(timed.due(15_000))
        val counted = ChunkBuilder(0)
        repeat(ChunkBuilder.MAX_FRAMES - 1) { counted.add(ByteArray(1), it.toLong(), nowMs = 0) }
        assertFalse(counted.due(0))
        counted.add(ByteArray(1), 99, nowMs = 0)
        assertTrue(counted.due(0))
        assertEquals(10_000L, ChunkBuilder.MAX_AGE_MS)
        assertEquals(100, ChunkBuilder.MAX_FRAMES)
    }

    @Test fun chunksWaitInOrderAcrossSessionsAndSurviveARestart() {
        val dir = folder.newFolder("chunks")
        val queue = ChunkQueue(dir)
        queue.add("aon-20261115-010000", 1, final = false, zone = "kitchen", zip = ByteArray(5))
        queue.add("aon-20261115-010000", 0, final = false, zone = "kitchen", zip = ByteArray(5))
        queue.add("aon-20261115-000000", 7, final = true, zone = "hall", zip = ByteArray(0))
        val order = queue.pending().map { Triple(it.session, it.seq, it.final) }
        assertEquals(listOf(Triple("aon-20261115-000000", 7, true), Triple("aon-20261115-010000", 0, false),
            Triple("aon-20261115-010000", 1, false)), order)
        assertEquals("hall", queue.next()!!.zone)
        queue.remove(queue.next()!!)
        // A new queue on the same folder (the app restarted) finds what is still waiting, in order.
        val restarted = ChunkQueue(dir)
        assertEquals(listOf(0, 1), restarted.pending().map { it.seq })
        assertEquals(10L, restarted.totalBytes())
        assertTrue("no half-written files", dir.listFiles()!!.none { it.name.endsWith(".part") })
    }

    @Test fun overTheCapTheOldestChunksAreDroppedButTheNewestStays() {
        val queue = ChunkQueue(folder.newFolder("capped"), capBytes = 250)
        queue.add("aon-1", 0, false, "kitchen", ByteArray(100))
        queue.add("aon-1", 1, false, "kitchen", ByteArray(100))
        assertEquals(0, queue.dropped)
        queue.add("aon-1", 2, false, "kitchen", ByteArray(100))
        assertEquals(1, queue.dropped)
        assertEquals(listOf(1, 2), queue.pending().map { it.seq })
        queue.add("aon-1", 3, true, "kitchen", ByteArray(400))
        assertEquals(listOf(3), queue.pending().map { it.seq })
        assertEquals(3, queue.dropped)
        assertEquals(300L * 1024 * 1024, ChunkQueue.CAP_BYTES)
    }

    @Test fun uploadsRetryWithBackoffAndOnlyRefusalsDropAChunk() {
        assertEquals(listOf(2_000L, 5_000L, 10_000L, 30_000L, 60_000L, 60_000L), (1..6).map { UploadBackoff.delayMs(it) })
        assertTrue(UploadBackoff.dropsChunk(400))
        assertTrue(UploadBackoff.dropsChunk(413))
        assertFalse(UploadBackoff.dropsChunk(502))
        assertFalse(UploadBackoff.dropsChunk(401))
        assertFalse("no answer: keep it and retry", UploadBackoff.dropsChunk(null))
    }

    @Test fun theRelayIsTriedOverUsbFirstAndOverWifiWhenTheCableIsOut() {
        runBlocking {
            var now = 0L
            var usbUp = true
            val calls = mutableListOf<String>()
            val relay = FailoverRelay("usb", "wifi", clock = { now }, retryPrimaryMs = 30_000)
            suspend fun send(): String = relay.call { address ->
                calls += address
                if (address == "usb" && !usbUp) throw RelayFailure("Relay unreachable.", unreachable = true)
                address
            }
            assertEquals("usb", send())
            assertFalse(relay.usingLan)
            usbUp = false
            assertEquals("wifi", send())
            assertTrue(relay.usingLan)
            calls.clear()
            now += 10_000
            assertEquals("wifi", send())
            assertEquals("Wi-Fi first while USB was down recently", listOf("wifi"), calls)
            usbUp = true
            now += 30_000
            calls.clear()
            assertEquals("usb", send())
            assertEquals(listOf("usb"), calls)
            assertFalse(relay.usingLan)
        }
    }

    @Test fun anAnswerFromTheRelayNeverSwitchesAddressesAndWithoutWifiTheFailureStands() {
        runBlocking {
            val calls = mutableListOf<String>()
            val relay = FailoverRelay("usb", "wifi")
            val refused = runCatching {
                relay.call<String> { calls += it; throw RelayFailure("Relay HTTP 502.", status = 502) }
            }.exceptionOrNull()
            assertEquals(502, (refused as RelayFailure).status)
            assertEquals(listOf("usb"), calls)
            val usbOnly = FailoverRelay<String>("usb", null)
            val down = runCatching { usbOnly.call<String> { throw RelayFailure("Relay unreachable.", unreachable = true) } }.exceptionOrNull()
            assertTrue((down as RelayFailure).unreachable)
        }
    }

    @Test fun recordingStartsByItselfOnlyWithAKeyAndUntilTheUserStopsIt() {
        assertTrue(AlwaysOnRules.autoStart(enabled = true, relayKeySet = true, recording = false, stoppedByUser = false))
        assertFalse(AlwaysOnRules.autoStart(enabled = true, relayKeySet = false, recording = false, stoppedByUser = false))
        assertFalse(AlwaysOnRules.autoStart(enabled = true, relayKeySet = true, recording = true, stoppedByUser = false))
        assertFalse(AlwaysOnRules.autoStart(enabled = true, relayKeySet = true, recording = false, stoppedByUser = true))
        assertFalse(AlwaysOnRules.autoStart(enabled = false, relayKeySet = true, recording = false, stoppedByUser = false))
    }

    @Test fun theGlassesAreTheSourceAndThePhoneOnlyWhenPicked() {
        assertEquals(CameraSource.GLASSES, AlwaysOnRules.source(CameraSource.GLASSES))
        assertEquals(CameraSource.PHONE, AlwaysOnRules.source(CameraSource.PHONE))
        assertEquals("Waiting for the glasses: open the app and approve it in Meta AI.",
            AlwaysOnRules.glassesWaiting("AVAILABLE", "DISCONNECTED", "STOPPED"))
        assertEquals("Waiting for the glasses: put them on or unfold them (they are DISCONNECTED).",
            AlwaysOnRules.glassesWaiting("REGISTERED", "DISCONNECTED", "STOPPED"))
        assertEquals("Starting the glasses camera…", AlwaysOnRules.glassesWaiting("REGISTERED", "STARTED", "STARTING"))
        assertNull(AlwaysOnRules.glassesWaiting("REGISTERED", "STARTED", "STREAMING"))
    }

    @Test fun captureFollowsThePhoneTemperatureWithoutFlapping() {
        assertFalse(AlwaysOnRules.heatPaused(null, pausedNow = false))
        assertFalse(AlwaysOnRules.heatPaused(45.0, pausedNow = false))
        assertTrue(AlwaysOnRules.heatPaused(45.1, pausedNow = false))
        assertTrue("stays paused until below 43", AlwaysOnRules.heatPaused(44.0, pausedNow = true))
        assertFalse(AlwaysOnRules.heatPaused(43.0, pausedNow = true))
    }

    @Test fun aChunkUploadNamesTheSessionSeqAndFinalAndSendsAZip() {
        val zip = folder.newFile("chunk.zip").apply { writeBytes(ByteArray(64)) }
        val client = RelayClient("http://192.168.1.20:8090", "synthetic-relay-key", "kitchen", allowLocalHttp = true)
        val request = client.chunkRequest("kitchen", "aon-20261115-010000", 3, final = false, zip = zip)
        assertEquals("/recording", request.url.encodedPath)
        assertEquals("aon-20261115-010000", request.url.queryParameter("session"))
        assertEquals("3", request.url.queryParameter("seq"))
        assertEquals("0", request.url.queryParameter("final"))
        assertEquals("1", client.chunkRequest("kitchen", "aon-1", 4, final = true, zip = zip).url.queryParameter("final"))
        assertEquals("application/zip", request.body!!.contentType().toString())
        assertEquals(64L, request.body!!.contentLength())
        assertEquals("synthetic-relay-key", request.header("X-Key"))
        assertFalse(request.url.toString().contains("synthetic-relay-key"))
        assertEquals("http://192.168.1.20:8090", client.address)
        // Plain HTTP to the LAN only in debug builds, never to a public address.
        assertThrows(IllegalArgumentException::class.java) { RelayClient("http://192.168.1.20:8090", "k", "kitchen", allowLocalHttp = false) }
        assertThrows(IllegalArgumentException::class.java) { RelayClient("http://8.8.8.8:8090", "k", "kitchen", allowLocalHttp = true) }
    }
}
