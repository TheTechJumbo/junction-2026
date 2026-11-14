package com.junction.glasses.core

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class BackendConnectionTest {
    private val event = """{"id":"one","type":"pillbox_open","source":"sensor","ts":"2026-11-14T08:00:00+02:00","zone":"kitchen","object":null,"confidence":1,"evidence_url":null}"""
    private fun server(block: (MockWebServer, HttpBackendClient) -> Unit) {
        MockWebServer().use { s -> s.start(); block(s, HttpBackendClient(s.url("/").toString(), "private-test-token", true)) }
    }
    private fun probe(client: HttpBackendClient): Result<Unit> {
        val latch = CountDownLatch(1)
        var result: Result<Unit>? = null
        client.checkConnection { result = it; latch.countDown() }
        assertTrue("probe timed out", latch.await(5, TimeUnit.SECONDS))
        return result!!
    }
    private fun json(body: String) = MockResponse().setHeader("Content-Type", "application/json").setBody(body)
    @Test fun authenticatedReadOnlyProbeAcceptsEmptyAndPopulatedPages() = server { s, c ->
        for (body in listOf("""{"events":[],"next_cursor":null}""", """{"events":[$event],"next_cursor":"1"}""")) {
            s.enqueue(json(body)); probe(c).getOrThrow()
            val request = s.takeRequest()
            assertEquals("GET", request.method)
            assertEquals("/events?limit=1", request.path)
            assertEquals("Bearer private-test-token", request.getHeader("Authorization"))
            assertEquals(0L, request.bodySize)
        }
    }
    @Test fun authAndDatabaseFailuresRetainStatusWithoutServerDetails() = server { s, c ->
        for (status in listOf(401, 503)) {
            s.enqueue(MockResponse().setResponseCode(status).setBody("secret database credentials"))
            val error = probe(c).exceptionOrNull() as BackendFailure
            assertEquals("http", error.kind); assertEquals(status, error.status)
            assertFalse(error.toString().contains("secret"))
        }
    }
    @Test fun rejectsMalformedOrWrongContractRatherThanReportingConnected() = server { s, c ->
        val badPages = listOf("{}", "not JSON", """{"events":[],"next_cursor":12}""",
            """{"events":{},"next_cursor":null}""", """{"events":[],"next_cursor":null,"other":true}""",
            """{"events":[$event,$event],"next_cursor":null}""",
            """{"events":[${event.replace("\"confidence\":1", "\"confidence\":\"1\"")}],"next_cursor":null}""",
            """{"events":[${event.replace("+02:00", "")}],"next_cursor":null}""",
            """{events:[],next_cursor:null}""")
        for (body in badPages) {
            s.enqueue(json(body))
            assertEquals(body, "invalid_response", (probe(c).exceptionOrNull() as BackendFailure).kind)
        }
    }
    @Test fun boundedBodyAndContentTypeAreRequired() = server { s, c ->
        s.enqueue(MockResponse().setHeader("Content-Type", "text/html").setBody("connected"))
        assertEquals("content_type", (probe(c).exceptionOrNull() as BackendFailure).kind)
        s.enqueue(json("").setChunkedBody("x".repeat(262145), 1024))
        assertEquals("response_too_large", (probe(c).exceptionOrNull() as BackendFailure).kind)
    }
    @Test fun redirectDoesNotSendTokenToAnotherEndpoint() = server { s, c ->
        s.enqueue(MockResponse().setResponseCode(302).setHeader("Location", s.url("/wrong")))
        assertEquals(302, (probe(c).exceptionOrNull() as BackendFailure).status)
        assertEquals(1, s.requestCount)
    }
    @Test fun cancelCompletesWithSafeFailure() = server { s, c ->
        s.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        val latch = CountDownLatch(1); var result: Result<Unit>? = null
        val call = c.checkConnection { result = it; latch.countDown() }
        assertNotNull(s.takeRequest(2, TimeUnit.SECONDS)); call.cancel()
        assertTrue(latch.await(2, TimeUnit.SECONDS))
        val error = result!!.exceptionOrNull() as BackendFailure
        assertEquals("cancelled", error.kind)
        assertFalse(error.toString().contains("private-test-token"))
    }
}
