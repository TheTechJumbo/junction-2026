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

class ApiProxyTest {
    private lateinit var server: MockWebServer
    private val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).build()

    @Before fun start() { server = MockWebServer().apply { start() } }
    @After fun stop() { server.shutdown() }

    private fun proxy() = ApiProxy(client) { server.url("/") }

    @Test fun forwardsTheDayWithTheBearerTokenAndNoCaching() {
        server.enqueue(MockResponse().setBody("""{"events":[]}""").setHeader("Content-Type", "application/json; charset=utf-8"))
        val result = proxy().handle("GET", "/timeline", "day=2026-11-14", mapOf("authorization" to "Bearer private-session"))
        assertEquals(200, result.status)
        assertEquals("application/json", result.mimeType)
        assertEquals("utf-8", result.encoding)
        assertEquals("no-store", result.headers["Cache-Control"])
        assertEquals("""{"events":[]}""", result.body.readBytes().decodeToString())
        val sent = server.takeRequest()
        assertEquals("/timeline?day=2026-11-14", sent.path)
        assertEquals("Bearer private-session", sent.getHeader("Authorization"))
    }

    @Test fun passesErrorStatusesThroughForThePageToExplain() {
        server.enqueue(MockResponse().setResponseCode(401).setBody("{}"))
        assertEquals(401, proxy().handle("GET", "/summary", "day=2026-11-14", emptyMap()).status)
    }

    @Test fun evidenceKeepsItsImageTypeAndLength() {
        server.enqueue(MockResponse().setBody("png-bytes").setHeader("Content-Type", "image/png"))
        val result = proxy().handle("GET", "/evidence/abc123", null, mapOf("Authorization" to "Bearer t"))
        assertEquals("image/png", result.mimeType)
        assertNull(result.encoding)
        assertEquals("9", result.headers["Content-Length"])
    }

    @Test fun redirectsAreRefusedNotFollowed() {
        server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "https://elsewhere.test/"))
        assertEquals(502, proxy().handle("GET", "/timeline", "day=2026-11-14", emptyMap()).status)
        assertEquals(1, server.requestCount)
    }

    @Test fun onlyTheReadRoutesAreForwarded() {
        assertTrue(ApiProxy.isAllowed("/timeline", "day=2026-11-14"))
        assertTrue(ApiProxy.isAllowed("/evidence/a-b_c.1", null))
        assertTrue(ApiProxy.isAllowed("/mesh", null))
        assertFalse(ApiProxy.isAllowed("/mesh", "zone=kitchen"))
        assertTrue(ApiProxy.isAllowed("/hazards", null))
        assertTrue(ApiProxy.isAllowed("/night", "day=2026-11-15"))
        assertFalse(ApiProxy.isAllowed("/night", "day=x&token=y"))
        assertFalse(ApiProxy.isAllowed("/timeline", null))
        assertFalse(ApiProxy.isAllowed("/timeline", "day=2026-11-14&token=x"))
        assertFalse(ApiProxy.isAllowed("/evidence/abc", "token=x"))
        assertFalse(ApiProxy.isAllowed("/evidence/../events", null))
        assertFalse(ApiProxy.isAllowed("/events", null))
        assertFalse(ApiProxy.isAllowed("/ask", null))
        val p = proxy()
        assertEquals(405, p.handle("POST", "/timeline", "day=2026-11-14", emptyMap()).status)
        assertEquals(404, p.handle("GET", "/events", null, emptyMap()).status)
        assertEquals(0, server.requestCount)
    }

    @Test fun noAddressOrNoNetworkReadsAsServiceUnavailable() {
        assertEquals(503, ApiProxy(client) { null }.handle("GET", "/timeline", "day=2026-11-14", emptyMap()).status)
        val closed = MockWebServer().apply { start() }
        val unreachable = closed.url("/"); closed.shutdown()
        assertEquals(503, ApiProxy(client) { unreachable }.handle("GET", "/timeline", "day=2026-11-14", emptyMap()).status)
    }
}
