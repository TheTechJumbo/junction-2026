package com.junction.glasses.core

import org.junit.Assert.*
import org.junit.Test

/** The wearer screen's debug panel: key prints, the last answers and the report, never a secret. */
class DiagnosticsTest {
    @Test fun keyPrintsAreTheFirstSixHexOfTheSha256AndNeverTheSecret() {
        assertEquals("ba7816", KeyPrint.of("abc"))
        assertEquals("not set", KeyPrint.of(null))
        assertEquals("not set", KeyPrint.of("  "))
        assertEquals(6, KeyPrint.of("synthetic-relay-key").length)
        assertNotEquals("a different key shows", KeyPrint.of("relay-key-a"), KeyPrint.of("relay-key-b"))
    }

    @Test fun statusesSayWhatToCheck() {
        assertEquals("200", HttpTrace.describe(200))
        assertTrue(HttpTrace.describe(403).contains("key or token mismatch"))
        assertTrue(HttpTrace.describe(401).contains("key or token mismatch"))
        assertTrue(HttpTrace.describe(404).contains("not on this server"))
        assertTrue(HttpTrace.describe(503).contains("not running"))
        assertTrue(HttpTrace.describe(0).startsWith("no answer"))
        assertEquals("0s ago", HttpTrace.age(1_000, 5_000))
        assertEquals("5s ago", HttpTrace.age(5_000, 0))
        assertEquals("1m 5s ago", HttpTrace.age(65_000, 0))
        assertEquals("1h 2m ago", HttpTrace.age(3_725_000, 0))
    }

    @Test fun theTraceKeepsTheLatestAnswerOfEachCall() {
        HttpTrace.clear()
        HttpTrace.record("relay /locate · 127.0.0.1:8090", 200, 1_000, "zone kitchen: found")
        HttpTrace.record("backend /events · 127.0.0.1:8000", 403, 2_000)
        HttpTrace.record("relay /locate · 127.0.0.1:8090", 0, 3_000, "x".repeat(500))
        val trace = HttpTrace.snapshot()
        assertEquals(listOf("backend /events · 127.0.0.1:8000", "relay /locate · 127.0.0.1:8090"), trace.keys.toList())
        assertEquals(0, trace.getValue("relay /locate · 127.0.0.1:8090").status)
        assertEquals(160, trace.getValue("relay /locate · 127.0.0.1:8090").note!!.length)
        HttpTrace.clear()
        assertTrue(HttpTrace.snapshot().isEmpty())
    }

    @Test fun theReportShowsPrintsZonesAndAnswersButNoSecrets() {
        val key = "synthetic-relay-key-123"
        val token = "synthetic-backend-token-456"
        val facts = DiagnosticsFacts(
            build = "0.1 (abc1234) debug",
            relayUrl = "http://127.0.0.1:8090",
            relayLanUrl = "http://10.0.41.113:8090",
            relayKeyPrint = KeyPrint.of(key),
            backendUrl = "http://127.0.0.1:8000",
            backendTokenPrint = KeyPrint.of(token),
            zone = "always-on-test",
            venueZone = "kitchen",
            glasses = listOf("Last end or error: 02:31:04 · The glasses ended the session"),
            recording = listOf("Always-on: off", "Chunks waiting on the phone: 0 (0.0 MB)"),
            trace = linkedMapOf(
                "relay /navigate · 127.0.0.1:8090" to HttpTrace.Entry(404, 1_000, null),
                "relay /locate · 127.0.0.1:8090" to HttpTrace.Entry(403, 50_000, "Relay HTTP 403."),
            ),
            nowMs = 65_000,
        )
        val text = Diagnostics.report(facts)
        assertFalse(text.contains(key))
        assertFalse(text.contains(token))
        assertTrue(text.contains("Relay key print: ${KeyPrint.of(key)}"))
        assertTrue(text.contains("Backend token print: ${KeyPrint.of(token)}"))
        assertTrue(text.contains("Wi-Fi fallback http://10.0.41.113:8090"))
        assertTrue(text.contains("Recording zone: always-on-test · map and guidance zone: kitchen"))
        assertTrue(text.contains("relay /locate · 127.0.0.1:8090: 403 (key or token mismatch?), 15s ago · Relay HTTP 403."))
        assertTrue(text.contains("relay /navigate · 127.0.0.1:8090: 404 (not on this server yet?), 1m 4s ago"))
        assertTrue(text.contains("02:31:04 · The glasses ended the session"))
        val bare = Diagnostics.report(facts.copy(venueZone = null, trace = emptyMap()))
        assertTrue(bare.contains("map and guidance zone: always-on-test (no venue zone set)"))
        assertTrue(bare.contains("Last answers:\n  none yet"))
    }
}
