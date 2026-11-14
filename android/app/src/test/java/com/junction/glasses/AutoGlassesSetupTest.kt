package com.junction.glasses

import com.junction.glasses.AutoGlassesSetup.Step
import com.junction.glasses.meta.MetaState
import org.junit.Assert.*
import org.junit.Test

/** Automatic glasses decisions alone: no Meta SDK, Meta AI or Activity is involved. */
class AutoGlassesSetupTest {
    @Test fun unregisteredAppOpensMetaAiAtMostOncePerLaunch() {
        val setup = AutoGlassesSetup()
        assertEquals(Step.WAIT, setup.at("UNAVAILABLE", initialized = false))
        // The SDK may still be about to report an existing registration.
        assertEquals(Step.WAIT, setup.at("AVAILABLE", settled = false))
        assertEquals(Step.REGISTER, setup.at("AVAILABLE"))
        repeat(3) { setup.onForeground(); assertEquals(Step.WAIT, setup.at("AVAILABLE")) }
        assertEquals(Step.WAIT, setup.at("REGISTERING"))
        assertEquals(Step.WAIT, setup.at("UNAVAILABLE"))
        assertTrue(setup.registrationRequested)
    }

    @Test fun registrationInProgressBackgroundOrPendingFlowNeverOpensMetaAi() {
        val setup = AutoGlassesSetup()
        assertEquals(Step.WAIT, setup.at("REGISTERING"))
        assertEquals(Step.WAIT, setup.at("AVAILABLE", foreground = false))
        assertEquals(Step.WAIT, setup.at("AVAILABLE", pending = true))
        assertFalse(setup.registrationRequested)
    }

    @Test fun recreatedActivityKeepsTheSingleHandOffOfThisLaunch() {
        val setup = AutoGlassesSetup(registrationRequested = true, consentRequested = true)
        assertEquals(Step.WAIT, setup.at("AVAILABLE"))
        assertEquals(Step.CONNECT, setup.at("REGISTERED"))
    }

    @Test fun registeredAppAsksGlassesConsentOnceThenConnectsOnce() {
        val setup = AutoGlassesSetup()
        assertEquals(Step.REQUEST_CONSENT, setup.at("REGISTERED", settled = false))
        assertEquals(Step.WAIT, setup.at("REGISTERED"))
        setup.consentFinished()
        assertEquals(Step.WAIT, setup.at("REGISTERED", foreground = false))
        assertEquals(Step.CONNECT, setup.at("REGISTERED"))
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "CONNECTING"))
        assertEquals(Step.WAIT, setup.at("REGISTERED"))
        setup.onForeground()
        assertEquals(Step.CONNECT, setup.at("REGISTERED"))
        assertFalse(setup.registrationRequested)
    }

    @Test fun returningToForegroundReconnectsOncePerResume() {
        val setup = connectedSetup()
        assertEquals(Step.WAIT, setup.at("REGISTERED"))
        setup.onForeground()
        assertEquals(Step.WAIT, setup.at("REGISTERED", pending = true))
        assertEquals(Step.CONNECT, setup.at("REGISTERED"))
        assertEquals(Step.WAIT, setup.at("REGISTERED"))
        setup.onForeground()
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED"))
        // A later disconnect without a failure (for example the Disconnect button) is not undone.
        assertEquals(Step.WAIT, setup.at("REGISTERED"))
    }

    @Test fun failedSessionsRetryThreeTimesThenWaitForTheUser() {
        val setup = connectedSetup()
        for (failures in 1..AutoGlassesSetup.MAX_RETRIES) {
            assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", failures = failures, error = "Glasses connection timed out."))
            assertTrue(setup.retryPending)
            assertEquals(Step.WAIT, setup.at("REGISTERED", failures = failures))
            setup.retryDue()
            assertEquals(Step.CONNECT, setup.at("REGISTERED", failures = failures))
        }
        assertEquals(Step.WAIT, setup.at("REGISTERED", failures = 4, error = "Glasses session failed: lost"))
        assertEquals("Glasses session failed: lost", setup.lastFailure)
        assertEquals(Step.WAIT, setup.at("REGISTERED", failures = 4))
        setup.onForeground()
        assertEquals(Step.CONNECT, setup.at("REGISTERED", failures = 4))
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED", failures = 4))
        assertNull(setup.lastFailure)
    }

    @Test fun glassesEndingIdleSessionsReconnectQuietlyWithBackoffAndNoCap() {
        val setup = connectedSetup()
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED"))
        val delays = mutableListOf<Long>()
        for (ends in 1..5) {
            assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", ends = ends))
            assertTrue(setup.idleReconnect)
            delays += setup.retryDelayMs
            assertEquals(Step.WAIT, setup.at("REGISTERED", ends = ends))
            setup.retryDue()
            assertEquals(Step.CONNECT, setup.at("REGISTERED", ends = ends))
        }
        assertEquals(listOf(5_000L, 10_000L, 30_000L, 30_000L, 30_000L), delays)
        assertNull(setup.lastFailure)
        // After a session has started, timeouts while reconnecting are retried beyond the three-attempt cap.
        for (failures in 1..AutoGlassesSetup.MAX_RETRIES + 2) {
            assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", ends = 5, failures = failures, error = "Glasses connection timed out."))
            assertEquals(30_000L, setup.retryDelayMs)
            setup.retryDue()
            assertEquals(Step.CONNECT, setup.at("REGISTERED", ends = 5, failures = failures))
        }
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED", ends = 5, failures = 5))
        assertFalse(setup.idleReconnect)
        assertNull(setup.lastFailure)
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", ends = 6, failures = 5))
        assertEquals(5_000L, setup.retryDelayMs)
    }

    @Test fun idleReconnectWaitsForTheForeground() {
        val setup = connectedSetup()
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED"))
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", ends = 1, foreground = false))
        setup.retryDue()
        assertEquals(Step.WAIT, setup.at("REGISTERED", ends = 1, foreground = false))
        assertEquals(Step.CONNECT, setup.at("REGISTERED", ends = 1))
    }

    @Test fun withoutGlassesMetaAiIsNeverOpenedAndNothingConnects() {
        val setup = AutoGlassesSetup()
        repeat(3) { setup.onForeground(); assertEquals(Step.WAIT, setup.at("AVAILABLE", devices = 0)) }
        assertEquals(Step.WAIT, setup.at("REGISTERED", devices = 0))
        assertFalse(setup.registrationRequested)
        assertFalse(setup.consentRequested)
        // Glasses reported later in the same launch: the automatic steps continue.
        assertEquals(Step.REQUEST_CONSENT, setup.at("REGISTERED", devices = 1))
        setup.consentFinished()
        assertEquals(Step.CONNECT, setup.at("REGISTERED", devices = 1))
    }

    @Test fun disconnectButtonCancelsPendingRetry() {
        val setup = connectedSetup()
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", failures = 1))
        setup.userDisconnected()
        setup.retryDue()
        assertEquals(Step.WAIT, setup.at("REGISTERED", failures = 1))
    }

    private fun connectedSetup() = AutoGlassesSetup(consentRequested = true).also {
        assertEquals(Step.CONNECT, it.at("REGISTERED"))
    }

    private fun AutoGlassesSetup.at(
        registration: String,
        connection: String = "DISCONNECTED",
        failures: Int = 0,
        ends: Int = 0,
        error: String? = null,
        initialized: Boolean = true,
        settled: Boolean = true,
        foreground: Boolean = true,
        pending: Boolean = false,
        devices: Int = 1,
    ): Step = next(
        MetaState(initialized = initialized, registration = registration, connection = connection, error = error,
            sessionFailures = failures, sessionEnds = ends, deviceCount = devices),
        foreground, pending, settled,
    )
}
