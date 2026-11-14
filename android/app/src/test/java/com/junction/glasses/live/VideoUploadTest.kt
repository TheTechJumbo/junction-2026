package com.junction.glasses.live

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.time.LocalDateTime
import java.util.concurrent.TimeUnit

/** Room videos for the laptop: names, the streamed upload, the reply, and what stays on the phone. */
class VideoUploadTest {
    @get:Rule val folder = TemporaryFolder()

    @Test fun phoneVideoNamesCarryTheZoneAndTimeAndCanBeReadBack() {
        val name = RecordingNames.fileName("living-room", LocalDateTime.of(2026, 11, 14, 21, 5, 9))
        assertEquals("room-living-room-20261114-210509.mp4", name)
        assertTrue(RecordingNames.isRecording(name))
        assertEquals("living-room", RecordingNames.zoneOf(name))
        assertNull(RecordingNames.zoneOf("20261114_192951_d4ffcf1e.mp4"))
        assertFalse(RecordingNames.isRecording("room-kitchen-20261114.mp4"))
        assertThrows(IllegalArgumentException::class.java) { RecordingNames.fileName("Kitchen 2", LocalDateTime.of(2026, 1, 1, 0, 0)) }
        assertEquals("adb pull /sdcard/Android/data/com.junction.glasses/files/recordings/$name",
            RecordingNames.adbPull("com.junction.glasses", name))
    }

    @Test fun uploadRequestIsOneFinalChunkOfASessionWithTheKeyInTheHeader() {
        val file = folder.newFile("room-kitchen-20261114-210509.mp4").apply { writeBytes(ByteArray(1234) { it.toByte() }) }
        val client = RelayClient("http://127.0.0.1:8090", "synthetic-relay-key", "kitchen", allowLocalHttp = true)
        val body = FileUploadBody(file, RelayClient.VIDEO_MP4) { _, _ -> }
        val request = client.recordingRequest("kitchen", body, RelayClient.sessionFor(file.name))
        assertEquals("POST", request.method)
        assertEquals("/recording", request.url.encodedPath)
        // The relay's contract: POST /recording?zone=ROOM&session=NAME&seq=N&final=0|1; a whole video is chunk 0, final.
        assertEquals(setOf("zone", "session", "seq", "final"), request.url.queryParameterNames)
        assertEquals("kitchen", request.url.queryParameter("zone"))
        assertEquals("room-kitchen-20261114-210509", request.url.queryParameter("session"))
        assertEquals("0", request.url.queryParameter("seq"))
        assertEquals("1", request.url.queryParameter("final"))
        assertFalse(request.url.toString().contains("synthetic-relay-key"))
        assertEquals("synthetic-relay-key", request.header("X-Key"))
        assertEquals("video/mp4", request.body!!.contentType().toString())
        assertEquals(1234L, request.body!!.contentLength())
        val status = client.recordingStatusRequest("kitchen")
        assertEquals("GET", status.method)
        assertEquals("/recording", status.url.encodedPath)
        assertEquals(setOf("zone"), status.url.queryParameterNames)
        assertEquals("synthetic-relay-key", status.header("X-Key"))
        assertThrows(IllegalArgumentException::class.java) { client.recordingRequest("kitchen&fresh=1", body, "s") }
        assertThrows(IllegalArgumentException::class.java) { client.recordingRequest("kitchen", body, "a b") }
    }

    @Test fun aVideoFileIsStreamedInChunksWithProgressAndTheReplyIsRead() {
        runBlocking {
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setHeader("Content-Type", "application/json")
                    .setBody("""{"ok":true,"id":"rec-7","zone":"kitchen","seconds":42.5,"status":"queued","viewer":"/viewer?rec=rec-7"}"""))
                server.start()
                val bytes = ByteArray(700_000) { (it % 251).toByte() }
                val file = folder.newFile("room-kitchen-20261114-210509.mp4").apply { writeBytes(bytes) }
                val progress = mutableListOf<Pair<Long, Long>>()
                val client = RelayClient(server.url("/").toString(), "synthetic-relay-key", "kitchen", allowLocalHttp = true)
                val reply = client.uploadRecording("kitchen", FileUploadBody(file, RelayClient.VIDEO_MP4) { sent, total ->
                    synchronized(progress) { progress.add(sent to total) }
                }, RelayClient.sessionFor(file.name))
                assertEquals(RecordingReply("rec-7", "kitchen", 42.5, "queued", "/viewer?rec=rec-7"), reply)
                val request = server.takeRequest(5, TimeUnit.SECONDS)!!
                assertEquals("POST", request.method)
                assertEquals("/recording?zone=kitchen&session=room-kitchen-20261114-210509&seq=0&final=1", request.path)
                assertEquals("synthetic-relay-key", request.getHeader("X-Key"))
                assertEquals("video/mp4", request.getHeader("Content-Type"))
                assertEquals("700000", request.getHeader("Content-Length"))
                assertArrayEquals(bytes, request.body.readByteArray())
                val reported = synchronized(progress) { progress.toList() }
                assertEquals(700_000L to 700_000L, reported.last())
                assertTrue(reported.size >= 3) // sent a chunk at a time, never as one block
                assertEquals(reported.map { it.first }.sorted(), reported.map { it.first })
            }
        }
    }

    @Test fun aGlassesVideoIsStreamedFromItsContentStream() {
        runBlocking {
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setHeader("Content-Type", "application/json")
                    .setBody("""{"ok":true,"id":"rec-8","zone":"kitchen","status":"queued","viewer":"/viewer?rec=rec-8"}"""))
                server.start()
                val bytes = ByteArray(50_000) { (it % 7).toByte() }
                var opened = 0
                val body = StreamUploadBody({ opened++; ByteArrayInputStream(bytes) }, bytes.size.toLong(), RelayClient.VIDEO_MP4) { _, _ -> }
                assertEquals(50_000L, body.contentLength())
                val client = RelayClient(server.url("/").toString(), "synthetic-relay-key", "kitchen", allowLocalHttp = true)
                assertEquals("queued", client.uploadRecording("kitchen", body, RelayClient.sessionFor("20261115_001625_fe4b2e25.mp4")).status)
                val request = server.takeRequest(5, TimeUnit.SECONDS)!!
                assertEquals("video/mp4", request.getHeader("Content-Type"))
                assertArrayEquals(bytes, request.body.readByteArray())
                assertEquals(1, opened)
                val unknownLength = StreamUploadBody({ ByteArrayInputStream(bytes) }, 0L, RelayClient.VIDEO_MP4) { _, _ -> }
                assertEquals(-1L, unknownLength.contentLength())
            }
        }
    }

    @Test fun replyIsParsedWithTheViewerLinkAndErrorsBecomeFailures() {
        val reply = RecordingReply.parse("""{"ok":true,"id":17,"zone":"kitchen","seconds":12,"status":"processing","viewer":"/view/17"}""")
        assertEquals("17", reply.id)
        assertEquals("processing", reply.status)
        assertEquals(12.0, reply.seconds!!, 0.0)
        assertEquals("http://127.0.0.1:8090/view/17", reply.viewerUrl("http://127.0.0.1:8090/"))
        assertEquals("http://127.0.0.1:8090/view/17", reply.copy(viewer = "view/17").viewerUrl("http://127.0.0.1:8090"))
        val empty = RecordingReply.parse("""{"ok":true}""")
        assertEquals(RecordingReply(null, null, null, null, null), empty)
        assertNull(empty.viewerUrl("http://127.0.0.1:8090"))
        val failure = assertThrows(RelayFailure::class.java) { RecordingReply.parse("""{"ok":false,"error":"No relay worker is running."}""") }
        assertEquals("Relay refused the video: No relay worker is running.", failure.message)
        assertThrows(RelayFailure::class.java) { RecordingReply.parse("not json") }
    }

    @Test fun aSentVideoIsDeletedUnlessKeptAndAFailedOneStaysForARetry() {
        runBlocking {
            var failing = true
            val uploaded = mutableListOf<String>()
            val uploader = RecordingUploader("com.junction.glasses") { file, zone, progress ->
                if (failing) throw RelayFailure("Relay unreachable (ConnectException). Check the relay address, Wi-Fi or adb reverse.")
                uploaded.add("${file.name}@$zone")
                progress(file.length(), file.length())
                RecordingReply("1", zone, 3.0, "queued", "/v/1")
            }
            val first = folder.newFile("room-kitchen-20261114-210509.mp4").apply { writeBytes(ByteArray(10)) }
            val failed = uploader.send(first, keepOnPhone = false) { _, _ -> } as UploadOutcome.Failed
            assertTrue(first.isFile)
            assertTrue(failed.message.startsWith("Relay unreachable"))
            assertEquals("adb pull /sdcard/Android/data/com.junction.glasses/files/recordings/${first.name}", failed.adbPull)
            failing = false
            val retried = uploader.send(first, keepOnPhone = false) { _, _ -> } as UploadOutcome.Sent
            assertTrue(retried.deletedFromPhone)
            assertFalse(first.exists())
            assertEquals(listOf("${first.name}@kitchen"), uploaded)
            val kept = folder.newFile("room-hall-20261114-210600.mp4").apply { writeBytes(ByteArray(10)) }
            val keptOutcome = uploader.send(kept, keepOnPhone = true) { _, _ -> } as UploadOutcome.Sent
            assertFalse(keptOutcome.deletedFromPhone)
            assertTrue(kept.isFile)
            val stray = folder.newFile("notes.mp4")
            assertTrue(uploader.send(stray, keepOnPhone = false) { _, _ -> } is UploadOutcome.Failed)
            assertTrue(stray.isFile)
            // With a zone to fall back on, any video name is accepted.
            val anyName = RecordingUploader("com.junction.glasses", defaultZone = "kitchen") { file, zone, _ ->
                uploaded.add("${file.name}@$zone"); RecordingReply("2", zone, null, "final", null)
            }
            assertTrue(anyName.send(stray, keepOnPhone = true) { _, _ -> } is UploadOutcome.Sent)
            assertEquals("notes.mp4@kitchen", uploaded.last())
            assertTrue(RecordingUploader.deleteAfterUpload(uploaded = true, keepOnPhone = false))
            assertFalse(RecordingUploader.deleteAfterUpload(uploaded = true, keepOnPhone = true))
            assertFalse(RecordingUploader.deleteAfterUpload(uploaded = false, keepOnPhone = false))
        }
    }

    @Test fun sessionNamesComeFromAnyFileNameAndTheRelayReplyIsRead() {
        assertEquals("20261115_001625_fe4b2e25", RelayClient.sessionFor("20261115_001625_fe4b2e25.mp4"))
        assertEquals("My-video--1-", RelayClient.sessionFor("My video (1).mov"))
        assertEquals("video", RelayClient.sessionFor(".mp4"))
        assertEquals(64, RelayClient.sessionFor("x".repeat(100) + ".mp4").length)
        // What the relay answered for the 00:26:30 glasses video.
        val reply = RecordingReply.parse("""{"ok":true,"session":"glasses-20261115-001625","seq":0,"frames_total":50,"status":"final",""" +
            """"viewer":"/viewer?live=kitchen","duplicate":false,"missing":[],"map":"running","training":"running"}""")
        assertEquals("glasses-20261115-001625", reply.id)
        assertEquals("final", reply.status)
        assertEquals("http://127.0.0.1:8090/viewer?live=kitchen", reply.viewerUrl("http://127.0.0.1:8090"))
    }
}
