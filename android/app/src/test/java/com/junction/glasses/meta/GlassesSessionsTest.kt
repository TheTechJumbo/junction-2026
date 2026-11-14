package com.junction.glasses.meta

import org.junit.Assert.*
import org.junit.Test

/** How a glasses session ended, from the SDK error name and how long it ran; no SDK or device involved. */
class GlassesSessionsTest {
    @Test fun missingDeveloperModeGlassesAppIsBlockingWhateverTheTiming() {
        // Seen on RB Meta 0076, 14 Nov 23:01:51: START_ERROR_DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED 0.3 s after connect.
        for (startedFor in listOf(null, 0L, 400L, 60_000L)) {
            val outcome = GlassesSessions.classify("DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED", startedFor)
            assertEquals(SessionOutcome.GLASSES_APP_UPDATE, outcome)
            assertTrue(outcome.needsUser)
        }
        val text = GlassesSessions.message(SessionOutcome.GLASSES_APP_UPDATE, "DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED",
            "Your glasses need an update.", 0L)
        assertTrue(text, text.startsWith("Enable Developer Mode for these glasses in Meta AI, then tap the glasses"))
        assertTrue(text, text.contains("no glasses reset is needed"))
        assertFalse("not an idle end", text.contains("idle", ignoreCase = true))
    }

    @Test fun glassesThatAreOffFoldedOrSilentAreAsleepAndNeverDrops() {
        // 23:17 to 23:20: START_ERROR_DEVICE_UNAVAILABLE on every connect while the glasses were off.
        for (name in listOf("DEVICE_UNAVAILABLE", "DWA_UNAVAILABLE", "DEVICE_DISCONNECTED", "NO_ELIGIBLE_DEVICE", GlassesSessions.NO_ANSWER)) {
            for (startedFor in listOf(null, 300L, 600_000L)) {
                assertEquals("$name after $startedFor", SessionOutcome.ASLEEP, GlassesSessions.classify(name, startedFor))
            }
        }
        assertFalse(SessionOutcome.ASLEEP.needsUser)
        assertEquals("Glasses not awake: unfold or put them on, or open Meta AI. Reconnecting automatically (no answer in 20 s).",
            GlassesSessions.message(SessionOutcome.ASLEEP, GlassesSessions.NO_ANSWER, null, null))
        assertEquals("Glasses not awake: unfold or put them on, or open Meta AI. Reconnecting automatically (DEVICE_UNAVAILABLE).",
            GlassesSessions.message(SessionOutcome.ASLEEP, "DEVICE_UNAVAILABLE", null, null))
    }

    @Test fun sessionsEndedWithinSecondsAreDropsNotIdleEnds() {
        assertEquals(SessionOutcome.DROPPED, GlassesSessions.classify("SESSION_ENDED_BY_DEVICE", 300L))
        assertEquals(SessionOutcome.DROPPED, GlassesSessions.classify(null, GlassesSessions.STABLE_MS - 1))
        assertEquals(SessionOutcome.DROPPED, GlassesSessions.classify("UNEXPECTED_ERROR", 2_000L))
        // Ended before STARTED with no specific reason: the glasses refused it.
        assertEquals(SessionOutcome.DROPPED, GlassesSessions.classify("SESSION_ENDED_BY_DEVICE", null))
        assertEquals(SessionOutcome.DROPPED, GlassesSessions.classify(null, null))
        assertEquals("Glasses dropped the session 2 s after it started: Session ended by device · SESSION_ENDED_BY_DEVICE.",
            GlassesSessions.message(SessionOutcome.DROPPED, "SESSION_ENDED_BY_DEVICE", "Session ended by device", 2_400L))
        assertEquals("Glasses refused the session before it started: no reason given.",
            GlassesSessions.message(SessionOutcome.DROPPED, null, null, null))
    }

    @Test fun onlyLongSessionsEndedByTheGlassesAreIdleEnds() {
        val minutes = 210_000L
        assertEquals(SessionOutcome.IDLE_END, GlassesSessions.classify("SESSION_ENDED_BY_DEVICE", minutes))
        assertEquals(SessionOutcome.IDLE_END, GlassesSessions.classify("SESSION_IDLE", minutes))
        assertEquals(SessionOutcome.IDLE_END, GlassesSessions.classify(null, GlassesSessions.STABLE_MS))
        assertFalse(SessionOutcome.IDLE_END.needsUser)
        // Any other error after a long session is a failure the reconnect loop handles.
        assertEquals(SessionOutcome.FAILED, GlassesSessions.classify("UNEXPECTED_ERROR", minutes))
        assertEquals(SessionOutcome.FAILED, GlassesSessions.classify("THERMAL_CRITICAL", null))
    }

    @Test fun sdkAndFirmwareMismatchesNeedTheUser() {
        assertEquals(SessionOutcome.SDK_UPDATE, GlassesSessions.classify("INSUFFICIENT_SDK_VERSION", null))
        assertEquals(SessionOutcome.SDK_UPDATE, GlassesSessions.classify(null, 100L, "SDK_UPDATE_REQUIRED"))
        assertEquals(SessionOutcome.FIRMWARE_UPDATE, GlassesSessions.classify(null, null, "DEVICE_UPDATE_REQUIRED"))
        assertEquals(SessionOutcome.DROPPED, GlassesSessions.classify(null, 100L, "COMPATIBLE"))
        assertTrue(SessionOutcome.FIRMWARE_UPDATE.needsUser && SessionOutcome.SDK_UPDATE.needsUser)
        assertFalse(SessionOutcome.DROPPED.needsUser || SessionOutcome.FAILED.needsUser)
    }

    @Test fun capabilityWarningsDoNotEndTheSession() {
        for (name in listOf("DWA_OUT_OF_STU_RANGE", "CAPABILITY_DENIED", "CAPABILITY_ALREADY_ADDED", "CAPABILITY_NOT_FOUND")) {
            assertFalse(name, GlassesSessions.endsSession(name))
        }
        for (name in listOf("DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED", "SESSION_ENDED_BY_DEVICE", "DWA_UNAVAILABLE", "UNEXPECTED_ERROR")) {
            assertTrue(name, GlassesSessions.endsSession(name))
        }
    }
}
