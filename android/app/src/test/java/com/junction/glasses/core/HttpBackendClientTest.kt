package com.junction.glasses.core

import com.google.gson.JsonParser
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class HttpBackendClientTest {
    private fun <T> await(start: ((Result<T>) -> Unit) -> Cancelable): Result<T> {
        val latch = CountDownLatch(1); var result: Result<T>? = null
        start { result = it; latch.countDown() }
        assertTrue("callback did not arrive", latch.await(5, TimeUnit.SECONDS))
        return result!!
    }
    private fun server(block: (MockWebServer, HttpBackendClient) -> Unit) {
        MockWebServer().use { s -> s.start(); block(s, HttpBackendClient(s.url("/").toString(), "test-session", true)) }
    }
    @Test fun askUsesCanonicalContractAndBearerHeader() = server { s, c ->
        s.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody("""{"answer":"Opening does not confirm ingestion.","evidence":[{"id":"one","type":"pillbox_open","source":"sensor","ts":"2026-11-14T08:00:00Z","zone":"kitchen","object":null,"confidence":1,"evidence_url":null}]}"""))
        val answer = await<Answer> { c.ask("Did I take \"pills\"?", it) }.getOrThrow()
        val request = s.takeRequest()
        assertEquals("/ask", request.path); assertEquals("Bearer test-session", request.getHeader("Authorization"))
        val payload = JsonParser.parseString(request.body.readUtf8()).asJsonObject
        assertEquals(setOf("question"), payload.keySet()); assertEquals("Did I take \"pills\"?", payload["question"].asString)
        assertEquals("pillbox_open", answer.evidence.single().type); assertNull(answer.evidence.single().evidenceUrl)
    }
    @Test fun redirectIsNotFollowedAndProviderBodyIsNotExposed() = server { s, c ->
        s.enqueue(MockResponse().setResponseCode(302).setHeader("Location", s.url("/leak")).setBody("private-token"))
        val error = await<Answer> { c.ask("q", it) }.exceptionOrNull() as BackendFailure
        assertEquals(302, error.status); assertEquals(1, s.requestCount); assertFalse(error.toString().contains("private-token"))
    }
    @Test fun oversizedAndMalformedAnswersAreRejected() = server { s, c ->
        s.enqueue(MockResponse().setHeader("Content-Type", "application/json").setChunkedBody("x".repeat(262145), 1000))
        assertEquals("response_too_large", (await<Answer> { c.ask("q", it) }.exceptionOrNull() as BackendFailure).kind)
        s.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody("{}"))
        assertEquals("invalid_response", (await<Answer> { c.ask("q", it) }.exceptionOrNull() as BackendFailure).kind)
    }
    @Test fun speechRequiresAudioTypeAndMp3Header() = server { s, c ->
        s.enqueue(MockResponse().setHeader("Content-Type", "audio/mpeg").setBody(Buffer().write(byteArrayOf(0x49, 0x44, 0x33, 0))))
        assertEquals(4, await<ByteArray> { c.speak("observation", it) }.getOrThrow().size)
        val req = s.takeRequest(); assertEquals("/speak", req.path)
        assertEquals("observation", JsonParser.parseString(req.body.readUtf8()).asJsonObject["text"].asString)
        s.enqueue(MockResponse().setHeader("Content-Type", "audio/mpeg").setBody("html error"))
        assertTrue(await<ByteArray> { c.speak("q", it) }.isFailure)
        s.enqueue(MockResponse().setHeader("Content-Type", "text/html").setBody("ID3fake"))
        assertEquals("content_type", (await<ByteArray> { c.speak("q", it) }.exceptionOrNull() as BackendFailure).kind)
    }
    @Test fun dangerousOriginsRejected() {
        listOf("http://example.com", "http://192.168.1.2", "https://user:password@example.com", "https://example.com?token=secret", "https://example.com/api").forEach {
            try { HttpBackendClient(it, "token", true); fail("accepted unsafe origin") } catch (_: IllegalArgumentException) { }
        }
        try { HttpBackendClient("http://localhost:8000", "token"); fail("accepted implicit HTTP") } catch (_: IllegalArgumentException) { }
    }
    @Test fun cancellationCompletesWithoutLeakingTransportDetails() = server { s, c ->
        s.enqueue(MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.NO_RESPONSE))
        val latch = CountDownLatch(1); var failure: Throwable? = null
        val operation = c.ask("q") { failure = it.exceptionOrNull(); latch.countDown() }
        s.takeRequest(2, TimeUnit.SECONDS); operation.cancel()
        assertTrue(latch.await(2, TimeUnit.SECONDS))
        assertEquals("cancelled", (failure as BackendFailure).kind)
        assertFalse(failure.toString().contains("test-session"))
    }
    @Test fun authenticationErrorNeverIncludesServerBody() = server { s, c ->
        s.enqueue(MockResponse().setResponseCode(401).setBody("private session-token details"))
        val error = await<Answer> { c.ask("q", it) }.exceptionOrNull() as BackendFailure
        assertEquals(401, error.status); assertFalse(error.toString().contains("session-token"))
    }

}
