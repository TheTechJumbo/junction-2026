package com.junction.glasses.live

import com.junction.glasses.core.HttpTrace
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.TimeUnit

/** The live camera loop and relay client, without a camera, Android or a real relay. */
class LiveRelayTest {
    private val found = LocateReply("found", "The pill box is about 2 m to your left", emptyList())

    @Test fun onlyOneRequestIsInFlightAndTheNextFrameWaitsForTheReply() {
        runBlocking {
            val replies = Channel<LocateReply>()
            val posted = Channel<Int>(Channel.UNLIMITED)
            var captures = 0
            var inFlight = 0
            var maxInFlight = 0
            val loop = LiveRelayLoop<Int>(this, capture = { ++captures }, post = { frame ->
                inFlight++
                maxInFlight = maxOf(maxInFlight, inFlight)
                posted.send(frame)
                try { replies.receive() } finally { inFlight-- }
            }, onUpdate = {})
            loop.start()
            loop.start() // already running: no second loop
            assertEquals(1, posted.receive())
            repeat(5) { yield() }
            assertEquals(1, captures)
            replies.send(found)
            assertEquals(2, posted.receive())
            assertEquals(2, captures)
            assertEquals(1, maxInFlight)
            loop.stop()
        }
    }

    @Test fun switchingOffStopsTheLoopAndNoFurtherFrameIsTaken() {
        runBlocking {
            var captures = 0
            val posted = Channel<Int>(Channel.UNLIMITED)
            val loop = LiveRelayLoop<Int>(this, capture = { ++captures }, post = { frame ->
                posted.send(frame)
                awaitCancellation()
            }, onUpdate = {})
            loop.start()
            assertEquals(1, posted.receive())
            assertTrue(loop.running)
            loop.stop()
            assertFalse(loop.running)
            repeat(5) { yield() }
            assertEquals(1, captures)
        }
    }

    @Test fun aFailedRequestIsShownAndRetriedAfterTwoSeconds() {
        runBlocking {
            val updates = mutableListOf<LiveSnapshot<Int>>()
            val pauses = mutableListOf<Long>()
            val third = CompletableDeferred<Unit>()
            var posts = 0
            val loop = LiveRelayLoop<Int>(this, capture = { posts + 1 }, post = {
                posts++
                when (posts) {
                    1 -> throw RelayFailure("Relay HTTP 502: the vision worker is not running.")
                    2 -> found
                    else -> { third.complete(Unit); awaitCancellation() }
                }
            }, onUpdate = { updates += it }, pause = { pauses += it })
            loop.start()
            third.await()
            loop.stop()
            assertEquals(2_000L, LiveRelayLoop.RETRY_DELAY_MS)
            assertEquals(listOf(LiveRelayLoop.RETRY_DELAY_MS), pauses)
            assertEquals("Relay HTTP 502: the vision worker is not running.", updates.first { it.error != null }.error)
            val replied = updates.last { it.reply != null }
            assertEquals(found, replied.reply)
            assertNull(replied.error)
            assertNotNull(replied.roundTripSeconds)
            assertEquals(3, updates.last().framesSent)
        }
    }

    @Test fun repliesAreFollowedByTheNextRequestWithoutAnyDelay() {
        runBlocking {
            var now = 0L
            val step = 80_000_000L // the relay's quick match: 80 ms
            val captureTimes = mutableListOf<Long>()
            val replyTimes = mutableListOf<Long>()
            val pauses = mutableListOf<Long>()
            val updates = mutableListOf<LiveSnapshot<Int>>()
            val done = CompletableDeferred<Unit>()
            val total = 150 // 12 s of replies, more than the 10 s rate window
            val loop = LiveRelayLoop<Int>(this, capture = { captureTimes += now; captureTimes.size }, post = {
                now += step
                replyTimes += now
                if (replyTimes.size > total) { done.complete(Unit); awaitCancellation() }
                found
            }, onUpdate = { updates += it }, pause = { pauses += it }, clock = { now })
            loop.start()
            done.await()
            loop.stop()
            assertTrue(pauses.isEmpty())
            // Every capture after the first happens at the very moment of the previous reply.
            assertEquals(replyTimes.take(total), captureTimes.drop(1).take(total))
            val last = updates.last { it.reply != null }
            assertEquals(0.08, last.roundTripSeconds!!, 1e-9)
            assertEquals(12.5, last.framesPerSecond!!, 1e-9)
            assertEquals(12.5, updates.first { it.reply != null }.framesPerSecond!!, 1e-9)
        }
    }

    @Test fun framesOfferedWhileNobodyWaitsAreDroppedSoNothingQueues() {
        runBlocking {
            var now = 0L
            val slot = FrameSlot<Int> { now }
            assertNull(slot.wantedSince())
            repeat(100) { assertFalse(slot.offer(it)) }
            now = 42L
            val taken = async { slot.take() }
            yield()
            assertEquals(42L, slot.wantedSince())
            assertTrue(slot.offer(100))
            assertFalse(slot.offer(101))
            assertEquals(100, taken.await())
            assertNull(slot.wantedSince())
            val cancelled = launch { slot.take() }
            yield()
            assertNotNull(slot.wantedSince())
            cancelled.cancel()
            yield()
            assertNull(slot.wantedSince())
            val failing = async { runCatching { slot.take() } }
            yield()
            slot.fail(CameraFailure("A camera frame could not be read."))
            assertEquals("A camera frame could not be read.", failing.await().exceptionOrNull()?.message)
        }
    }

    @Test fun theLoopOnlySendsFramesTheCameraDeliveredAfterEachReply() {
        runBlocking {
            val slot = FrameSlot<Int>()
            var camera = 0
            val posted = mutableListOf<Int>()
            val done = CompletableDeferred<Unit>()
            val loop = LiveRelayLoop<Int>(this, capture = { slot.take() }, post = { frame ->
                posted += frame
                repeat(5) { slot.offer(++camera) } // delivered while the request is in flight: dropped
                if (posted.size == 3) { done.complete(Unit); awaitCancellation() }
                found
            }, onUpdate = {})
            loop.start()
            while (!done.isCompleted) {
                yield()
                slot.offer(++camera) // the camera keeps delivering frames
            }
            loop.stop()
            assertEquals(listOf(1, 7, 13), posted)
        }
    }

    @Test fun shakyFramesWaitForASteadierOneButNeverMoreThanOneSecond() {
        val ms = 1_000_000L
        val slot = FrameSlot<Int> { 1_000 * ms }
        val picker = FramePicker(slot, SteadyGate())
        assertFalse(picker.wants(1_000 * ms)) // nobody waits: not even worth encoding
        runBlocking {
            val taken = async { slot.take() }
            yield()
            assertTrue(picker.wants(1_000 * ms)) // no gyroscope readings: never blocks
            val gate = SteadyGate()
            val shaky = FramePicker(slot, gate)
            for (t in 1_000L..1_500L step 10L) gate.record(t * ms, 2.0f, 0.5f, 0f) // turning at about 2 rad/s
            assertFalse(shaky.wants(1_500 * ms))
            for (t in 1_510L..1_990L step 10L) gate.record(t * ms, 2.0f, 0.5f, 0f)
            assertFalse(shaky.wants(1_990 * ms))
            assertEquals(2, shaky.shakySkipped)
            gate.record(2_000 * ms, 2.0f, 0.5f, 0f)
            assertTrue(shaky.wants(2_000 * ms)) // waited 1 s: send anyway
            for (t in 2_010L..2_200L step 10L) gate.record(t * ms, 0.3f, 0f, 0.2f) // held still
            assertTrue(shaky.wants(2_200 * ms))
            assertEquals(2, shaky.shakySkipped)
            assertTrue(slot.offer(7))
            assertEquals(7, taken.await())
        }
    }

    @Test fun quickMatchIsSummarisedIncludingMissingFields() {
        fun quick(json: String) = LocateReply.parse("""{"ok":true,"state":"found","headline":"h","waypoints":[],"quick":$json}""").quick
        assertEquals("quick match 0.87 · turned 12°", quick("""{"similarity":0.873,"turn_deg":12.4,"skipped":false}""")?.summary())
        assertEquals("skipped (repeat)", quick("""{"similarity":0.2,"turn_deg":3,"skipped":true}""")?.summary())
        assertEquals("quick match 0.50", quick("""{"similarity":0.5}""")?.summary())
        assertEquals(QuickMatch(null, null, false), quick("{}"))
        assertNull(quick("{}")?.summary())
        assertNull(quick("\"not an object\""))
        assertNull(LocateReply.parse("""{"ok":true,"state":"found","headline":"h","waypoints":[]}""").quick)
    }

    @Test fun unexpectedErrorsAreNamedWithoutTheirMessage() {
        assertEquals("Camera or connection error (IllegalStateException).",
            LiveRelayLoop.describe(IllegalStateException("details that are not shown")))
        assertEquals("Relay unreachable.", LiveRelayLoop.describe(RelayFailure("Relay unreachable.")))
        assertEquals("No camera frame for 5 s.", LiveRelayLoop.describe(CameraFailure("No camera frame for 5 s.")))
    }

    @Test fun replyIsParsedForTheScreen() {
        val reply = LocateReply.parse("""{"ok":true,"zone":"kitchen","state":"unsure","reused":true,
            "headline":"The pill box is about 2 m to your left","detail":"This fix is shaky.",
            "waypoints":[{"name":"pill box","distance_m":2.4,"bearing_deg":-63,"position":[1.0,2.0]},{"name":"no distance"},"x"],
            "map":{"points":[[0.1,0.2,0.3]],"cameras":[]}}""")
        assertEquals("unsure", reply.state)
        assertEquals("The pill box is about 2 m to your left", reply.headline)
        assertEquals(listOf(Waypoint("pill box", 2.4, -63)), reply.waypoints)
        val lost = LocateReply.parse("""{"ok":true,"state":"lost","headline":"Not sure where you are","waypoints":[]}""")
        assertEquals("lost", lost.state)
        assertTrue(lost.waypoints.isEmpty())
        assertThrows(RelayFailure::class.java) { LocateReply.parse("""{"ok":false,"error":"worker busy"}""") }
        assertThrows(RelayFailure::class.java) { LocateReply.parse("<html>") }
    }

    @Test fun requestCarriesTheKeyOnlyInTheHeaderAndNeverAsksForFreshOrView() {
        val client = RelayClient("http://127.0.0.1:8090", "synthetic-relay-key", "kitchen", allowLocalHttp = true)
        val request = client.request(byteArrayOf(1, 2, 3))
        assertEquals("POST", request.method)
        assertEquals("/locate", request.url.encodedPath)
        assertEquals(setOf("zone"), request.url.queryParameterNames)
        assertEquals("kitchen", request.url.queryParameter("zone"))
        val url = request.url.toString()
        assertFalse(url.contains("fresh"))
        assertFalse(url.contains("view"))
        assertFalse(url.contains("synthetic-relay-key"))
        assertEquals("synthetic-relay-key", request.header("X-Key"))
        assertEquals("image/jpeg", request.body!!.contentType().toString())
    }

    @Test fun onlyDebugLoopbackOrLanMayUseCleartext() {
        // Debug builds: loopback (adb reverse) and private LAN (the laptop on the same Wi-Fi); never a public address.
        RelayClient("http://192.168.1.20:8090", "key", "kitchen", allowLocalHttp = true)
        assertThrows(IllegalArgumentException::class.java) { RelayClient("http://192.168.1.20:8090", "key", "kitchen", allowLocalHttp = false) }
        assertThrows(IllegalArgumentException::class.java) { RelayClient("http://203.0.113.5:8090", "key", "kitchen", allowLocalHttp = true) }
        assertThrows(IllegalArgumentException::class.java) { RelayClient("http://127.0.0.1:8090", "key", "kitchen", allowLocalHttp = false) }
        assertThrows(IllegalArgumentException::class.java) { RelayClient("https://relay.example/path", "key", "kitchen", allowLocalHttp = false) }
        assertThrows(IllegalArgumentException::class.java) { RelayClient("https://relay.example", " ", "kitchen", allowLocalHttp = false) }
        assertThrows(IllegalArgumentException::class.java) { RelayClient("https://relay.example", "key", "kitchen&fresh=1", allowLocalHttp = false) }
        RelayClient("https://relay.example", "key", "kitchen", allowLocalHttp = false)
    }

    @Test fun locatePostsTheJpegWithTheKeyHeaderAndReadsReplyOrError() {
        runBlocking {
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setHeader("Content-Type", "application/json")
                    .setBody("""{"ok":true,"state":"found","headline":"Found you in the room","waypoints":[]}"""))
                server.enqueue(MockResponse().setResponseCode(403).setHeader("Content-Type", "application/json")
                    .setBody("""{"ok":false,"error":"Open the link that includes the access key (?k=...)."}"""))
                server.start()
                val client = RelayClient(server.url("/").toString(), "synthetic-relay-key", "kitchen", allowLocalHttp = true)
                val jpeg = byteArrayOf(-1, -40, -1, 1)
                assertEquals("found", client.locate(jpeg).state)
                val request = server.takeRequest(1, TimeUnit.SECONDS)!!
                assertEquals("POST", request.method)
                assertEquals("/locate?zone=kitchen", request.path)
                assertEquals("synthetic-relay-key", request.getHeader("X-Key"))
                assertEquals("image/jpeg", request.getHeader("Content-Type"))
                assertArrayEquals(jpeg, request.body.readByteArray())
                val failure = try { client.locate(jpeg); null } catch (e: RelayFailure) { e }
                assertEquals("Relay HTTP 403: Open the link that includes the access key (?k=...).", failure?.message)
                assertFalse(failure!!.message!!.contains("synthetic-relay-key"))
            }
        }
    }

    @Test fun everyRelayAnswerIsNotedForTheDebugPanelWithoutTheKey() {
        runBlocking {
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setResponseCode(403).setHeader("Content-Type", "application/json")
                    .setBody("""{"ok":false,"error":"wrong key"}"""))
                server.enqueue(MockResponse().setHeader("Content-Type", "application/json")
                    .setBody("""{"ok":true,"to":"pill box","instruction":"pill box, 2 metres ahead","places":[]}"""))
                server.start()
                HttpTrace.clear()
                val client = RelayClient(server.url("/").toString(), "synthetic-relay-key", "kitchen", allowLocalHttp = true)
                try { client.locate(byteArrayOf(1)); fail("403 should fail") } catch (_: RelayFailure) { }
                assertEquals("pill box, 2 metres ahead", client.navigate("pill box").instruction)
                val away = RelayClient("http://127.0.0.1:1", "synthetic-relay-key", "kitchen", allowLocalHttp = true)
                try { away.placeNames(); fail("nothing listens on port 1") } catch (_: RelayFailure) { }
                val trace = HttpTrace.snapshot()
                val locate = trace.entries.single { it.key.startsWith("relay /locate · ") }
                assertEquals(403, locate.value.status)
                assertTrue(locate.value.note!!.contains("wrong key"))
                val navigate = trace.entries.single { it.key.startsWith("relay /navigate · ") }
                assertEquals(200, navigate.value.status)
                assertEquals("zone kitchen to pill box: pill box, 2 metres ahead", navigate.value.note)
                assertEquals(0, trace.getValue("relay /waypoints · 127.0.0.1:1").status)
                for ((label, entry) in trace) {
                    assertFalse(label.contains("synthetic-relay-key") || label.contains("?"))
                    assertFalse(entry.note.orEmpty().contains("synthetic-relay-key"))
                }
                HttpTrace.clear()
            }
        }
    }
}
