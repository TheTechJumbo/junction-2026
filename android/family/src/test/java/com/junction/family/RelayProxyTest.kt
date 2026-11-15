package com.junction.family

import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class RelayProxyTest {
    private lateinit var server: MockWebServer
    private val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).build()

    @Before fun start() { server = MockWebServer().apply { start() } }
    @After fun stop() { server.shutdown() }

    private fun proxy(settings: RelaySettings? = RelaySettings(server.url("/"), "relay-key-123", "kitchen")) =
        RelayProxy(client) { settings }

    @Test fun forwardsThePageWithTheKeyInAHeaderNotTheAddress() {
        server.enqueue(MockResponse().setBody("<!doctype html>").setHeader("Content-Type", "text/html; charset=utf-8"))
        val result = proxy().handle("GET", "/relay/care", "zone=kitchen")
        assertEquals(200, result.status)
        assertEquals("text/html", result.mimeType)
        assertEquals("utf-8", result.encoding)
        assertEquals("no-store", result.headers["Cache-Control"])
        val sent = server.takeRequest()
        assertEquals("/care?zone=kitchen", sent.path)
        assertEquals("relay-key-123", sent.getHeader("X-Key"))
        assertNull(sent.getHeader("Authorization"))
    }

    @Test fun theSplatKeepsItsTypeAndLength() {
        server.enqueue(MockResponse().setBody("0123456789abcdef0123456789abcdef").setHeader("Content-Type", "application/octet-stream"))
        val result = proxy().handle("GET", "/relay/evidence/train-kitchen-glasses-20261115-001625-1/trained.splat", null)
        assertEquals(200, result.status)
        assertEquals("application/octet-stream", result.mimeType)
        assertEquals("32", result.headers["Content-Length"])
        assertEquals("/evidence/train-kitchen-glasses-20261115-001625-1/trained.splat", server.takeRequest().path)
    }

    @Test fun relayErrorsPassThroughForThePageToExplain() {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"ok":false}"""))
        assertEquals(404, proxy().handle("GET", "/relay/where", "zone=kitchen").status)
    }

    @Test fun redirectsAreRefusedNotFollowed() {
        server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "https://elsewhere.test/"))
        assertEquals(502, proxy().handle("GET", "/relay/day", null).status)
        assertEquals(1, server.requestCount)
    }

    @Test fun withoutSettingsNothingIsSent() {
        assertEquals(503, proxy(null).handle("GET", "/relay/where", "zone=kitchen").status)
        assertEquals(0, server.requestCount)
    }

    @Test fun onlyReadsAndOnlyThePagesRoutes() {
        assertEquals(405, proxy().handle("POST", "/relay/locate", "zone=kitchen").status)
        assertEquals(404, proxy().handle("GET", "/relay/places", null).status)
        assertEquals(404, proxy().handle("GET", "/where", "zone=kitchen").status) // not under /relay
        assertEquals(404, proxy().handle("GET", "/relayx/where", "zone=kitchen").status)
        assertEquals(0, server.requestCount)
    }

    @Test fun theAllowedRoutes() {
        for (path in listOf("/care", "/care/space", "/where", "/waypoints", "/frame", "/changes")) {
            assertTrue(path, RelayProxy.isAllowed(path, "zone=kitchen"))
            assertFalse(path, RelayProxy.isAllowed(path, null))
            assertFalse(path, RelayProxy.isAllowed(path, "zone=kitchen&k=stolen"))
            assertFalse(path, RelayProxy.isAllowed(path, "zone=../etc"))
        }
        assertTrue(RelayProxy.isAllowed("/day", null))
        assertTrue(RelayProxy.isAllowed("/day", "date=2026-11-15"))
        assertFalse(RelayProxy.isAllowed("/day", "date=today"))
        assertTrue(RelayProxy.isAllowed("/find", "zone=kitchen&what=orange%20box"))
        assertFalse(RelayProxy.isAllowed("/find", "zone=kitchen"))
        assertFalse(RelayProxy.isAllowed("/find", "zone=kitchen&what=box&k=stolen"))
        assertTrue(RelayProxy.isAllowed("/reminders", null))
        assertFalse(RelayProxy.isAllowed("/reminders", "id=1&delete=1"))
        assertTrue(RelayProxy.isAllowed("/evidence/231107-e89691/room.splat", null))
        assertTrue(RelayProxy.isAllowed("/evidence/job1/moved_before.jpg", null))
        assertTrue(RelayProxy.isAllowed("/evidence/train-x-1/labels.json", null))
        assertFalse(RelayProxy.isAllowed("/evidence/job1/room.splat", "k=x"))
        assertFalse(RelayProxy.isAllowed("/evidence/../rooms.json", null))
        assertFalse(RelayProxy.isAllowed("/evidence/job1/video.mp4", null))
        for (path in listOf("/places", "/status", "/worker_log", "/enrolled", "/baseline/kitchen/0.jpg", "/history", "/dashboard"))
            assertFalse(path, RelayProxy.isAllowed(path, null))
    }

    @Test fun theStartPageIsOnTheAppsOrigin() {
        assertEquals("https://appassets.androidplatform.net/relay/care?zone=kitchen",
            RelayProxy.startUrl("https://appassets.androidplatform.net", "kitchen"))
    }

    @Test fun theKeyIsNeverPrinted() {
        val settings = RelaySettings(server.url("/"), "relay-key-123", "kitchen")
        assertFalse(settings.toString().contains("relay-key-123"))
    }
}
