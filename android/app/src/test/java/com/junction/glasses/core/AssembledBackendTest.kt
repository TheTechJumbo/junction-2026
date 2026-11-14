package com.junction.glasses.core

import com.google.gson.JsonObject
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Explicit integration gate. Run only against an isolated synthetic server with speech unconfigured. */
class AssembledBackendTest {
    private fun <T> await(start: ((Result<T>) -> Unit) -> Cancelable): Result<T> {
        val latch = CountDownLatch(1); var result: Result<T>? = null
        start { result = it; latch.countDown() }
        assertTrue("API callback timed out", latch.await(15, TimeUnit.SECONDS))
        return result!!
    }
    @Test fun realObservationToAnswerAndUnavailableSpeech() {
        val origin = System.getenv("JUNCTION_ANDROID_TEST_URL")
        val token = System.getenv("JUNCTION_ANDROID_TEST_TOKEN")
        assumeTrue("Explicit isolated integration server required", !origin.isNullOrBlank() && !token.isNullOrBlank())
        val client = HttpBackendClient(origin!!, token!!, true)
        assertTrue("Authenticated connection probe failed", await<Unit> { client.checkConnection(it) }.isSuccess)
        val id = "android-integration-${UUID.randomUUID()}"
        val event = JsonObject().apply {
            addProperty("id", id); addProperty("type", "pillbox_open"); addProperty("source", "sensor")
            addProperty("ts", OffsetDateTime.now(ZoneOffset.UTC).toString()); addProperty("zone", "kitchen")
            addProperty("object", "pillbox"); add("evidence_url", com.google.gson.JsonNull.INSTANCE)
            addProperty("confidence", 1.0)
        }
        OkHttpClient().newCall(Request.Builder().url(origin.trimEnd('/') + "/events")
            .header("Authorization", "Bearer $token")
            .post(event.toString().toRequestBody("application/json".toMediaType())).build()).execute().use {
                assertEquals("Synthetic event was rejected", 200, it.code)
            }
        val answer = await<Answer> { client.ask("Did I take my pills?", it) }.getOrThrow()
        assertTrue("Expected transferred observation evidence", answer.evidence.any { it.id == id })
        assertTrue("Must retain uncertainty about ingestion", answer.text.contains("cannot confirm", ignoreCase = true))
        val jpeg = java.util.Base64.getDecoder().decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCACAAIADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD1yiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigD//2Q==")
        val evidenceClient = EvidenceClient(origin, token, true)
        val ref = await<String> { evidenceClient.uploadJpeg(id, jpeg, it) }.getOrThrow()
        assertTrue(ref.startsWith("/evidence/"))
        val afterUpload = await<Answer> { client.ask("Did I take my pills?", it) }.getOrThrow()
        assertEquals(ref, afterUpload.evidence.first { it.id == id }.evidenceUrl)
        val speech = await<ByteArray> { client.speak(answer.text, it) }.exceptionOrNull() as? BackendFailure
        assertEquals("Test server must have speech unconfigured", 503, speech?.status)
        val unauthorized = HttpBackendClient(origin, "synthetic-invalid-session", true)
        assertEquals(401, (await<Answer> { unauthorized.ask("Did I take my pills?", it) }.exceptionOrNull() as? BackendFailure)?.status)
    }
}
