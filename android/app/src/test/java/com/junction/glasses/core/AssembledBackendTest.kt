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
        val id = "android-integration-${UUID.randomUUID()}"
        val event = JsonObject().apply {
            addProperty("id", id); addProperty("type", "pillbox_open"); addProperty("source", "sensor")
            addProperty("ts", OffsetDateTime.now(ZoneOffset.UTC).toString()); addProperty("zone", "kitchen")
            add("object", com.google.gson.JsonNull.INSTANCE); add("evidence_url", com.google.gson.JsonNull.INSTANCE)
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
        val speech = await<ByteArray> { client.speak(answer.text, it) }.exceptionOrNull() as? BackendFailure
        assertEquals("Test server must have speech unconfigured", 503, speech?.status)
        val unauthorized = HttpBackendClient(origin, "synthetic-invalid-session", true)
        assertEquals(401, (await<Answer> { unauthorized.ask("Did I take my pills?", it) }.exceptionOrNull() as? BackendFailure)?.status)
    }
}
