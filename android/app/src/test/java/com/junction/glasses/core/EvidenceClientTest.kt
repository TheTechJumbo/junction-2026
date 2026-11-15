package com.junction.glasses.core

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class EvidenceClientTest {
    private val jpeg = byteArrayOf(0xff.toByte(), 0xd8.toByte(), 0xff.toByte(), 0xd9.toByte())
    private fun upload(client: EvidenceClient, id: String = "event", bytes: ByteArray = jpeg, clearOriginal: Boolean = false): Result<String> {
        val latch = CountDownLatch(1); var result: Result<String>? = null
        client.uploadJpeg(id, bytes) { result = it; latch.countDown() }
        if (clearOriginal) bytes.fill(0)
        assertTrue(latch.await(5, TimeUnit.SECONDS)); return result!!
    }
    @Test fun authenticatedRawUploadEncodesEventId() {
        MockWebServer().use { s ->
            s.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody("""{"evidence_url":"/evidence/image.jpg"}"""))
            val c = EvidenceClient(s.url("/").toString(), "synthetic-token", true)
            assertEquals("/evidence/image.jpg", upload(c, "sensor/a?b", jpeg.copyOf(), clearOriginal = true).getOrThrow())
            val req = s.takeRequest()
            assertEquals("/events/sensor%2Fa%3Fb/evidence", req.path)
            assertEquals("Bearer synthetic-token", req.getHeader("Authorization"))
            assertEquals("image/jpeg", req.getHeader("Content-Type"))
            assertArrayEquals(jpeg, req.body.readByteArray())
        }
    }
    @Test fun invalidAndOversizedInputsDoNotReachNetwork() {
        MockWebServer().use { s ->
            val c = EvidenceClient(s.url("/").toString(), "synthetic-token", true)
            assertEquals("image_too_large", (upload(c, bytes = ByteArray(5 * 1024 * 1024 + 1)).exceptionOrNull() as BackendFailure).kind)
            assertEquals("invalid_jpeg", (upload(c, bytes = byteArrayOf(1, 2, 3)).exceptionOrNull() as BackendFailure).kind)
            assertEquals("invalid_event_id", (upload(c, "..").exceptionOrNull() as BackendFailure).kind)
            assertEquals(0, s.requestCount)
        }
    }
    @Test fun errorsAndExternalReferencesAreRejectedWithoutBodyLeaks() {
        MockWebServer().use { s ->
            val c = EvidenceClient(s.url("/").toString(), "synthetic-token", true)
            s.enqueue(MockResponse().setResponseCode(503).setBody("private-token"))
            val error = upload(c).exceptionOrNull() as BackendFailure
            assertEquals(503, error.status); assertFalse(error.toString().contains("private-token"))
            listOf("https://example.com/photo.jpg", "//example.com/photo.jpg", "/evidence/../private", "/evidence/photo?key=secret").forEach { ref ->
                s.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody("""{"evidence_url":"$ref"}"""))
                assertEquals("invalid_reference", (upload(c).exceptionOrNull() as BackendFailure).kind)
            }
        }
    }
}
