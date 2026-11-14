package com.junction.glasses.live

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.channels.Channel
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

    @Test fun unexpectedErrorsAreNamedWithoutTheirMessage() {
        assertEquals("Camera or connection error (IllegalStateException).",
            LiveRelayLoop.describe(IllegalStateException("details that are not shown")))
        assertEquals("Relay unreachable.", LiveRelayLoop.describe(RelayFailure("Relay unreachable.")))
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

    @Test fun onlyDebugLoopbackMayUseCleartext() {
        assertThrows(IllegalArgumentException::class.java) { RelayClient("http://192.168.1.20:8090", "key", "kitchen", allowLocalHttp = true) }
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
}
