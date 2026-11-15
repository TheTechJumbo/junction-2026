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

    @Test fun missingGlassesAppOpensMetaAiOnceAndNeverLoops() {
        val setup = connectedSetup()
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED"))
        // The glasses refuse: open Meta AI's glasses app page instead of the 30 s reconnect loop.
        assertEquals(Step.OPEN_GLASSES_APP_UPDATE, setup.at("REGISTERED", blocks = 1, blocker = GLASSES_APP, error = "missing app"))
        assertTrue(setup.glassesAppPageOpened)
        assertEquals(GLASSES_APP, setup.blockedBy)
        assertFalse(setup.retryPending || setup.idleReconnect)
        assertEquals("missing app", setup.lastFailure)
        assertEquals(Step.WAIT, setup.at("REGISTERED", blocks = 1, blocker = GLASSES_APP, pending = true))
        assertEquals(Step.WAIT, setup.at("REGISTERED", blocks = 1, blocker = GLASSES_APP))
        // Back from Meta AI: reconnect, and check twice more while its install finishes.
        setup.onForeground()
        assertEquals(Step.CONNECT, setup.at("REGISTERED", blocks = 1, blocker = GLASSES_APP))
        for (blocks in 2..1 + AutoGlassesSetup.MAX_BLOCK_RETRIES) {
            assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", blocks = blocks, blocker = GLASSES_APP))
            assertEquals(AutoGlassesSetup.BLOCK_RETRY_MS, setup.retryDelayMs)
            setup.retryDue()
            assertEquals(Step.CONNECT, setup.at("REGISTERED", blocks = blocks, blocker = GLASSES_APP))
        }
        // Still refused: Meta AI is not opened again by itself; the button stays.
        assertEquals(Step.WAIT, setup.at("REGISTERED", blocks = 4, blocker = GLASSES_APP))
        assertEquals(Step.WAIT, setup.at("REGISTERED", blocks = 4, blocker = GLASSES_APP))
        setup.onForeground()
        assertEquals(Step.CONNECT, setup.at("REGISTERED", blocks = 4, blocker = GLASSES_APP))
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED", blocks = 4))
        assertNull(setup.blockedBy)
    }

    @Test fun glassesAppPageWaitsForConsentAndForeground() {
        val setup = AutoGlassesSetup(consentRequested = true)
        assertEquals(Step.CONNECT, setup.at("REGISTERED"))
        assertEquals(Step.WAIT, setup.at("REGISTERED", blocks = 1, blocker = GLASSES_APP, foreground = false))
        assertEquals(Step.WAIT, setup.at("REGISTERED", blocks = 1, blocker = GLASSES_APP, pending = true))
        assertEquals(Step.OPEN_GLASSES_APP_UPDATE, setup.at("REGISTERED", blocks = 1, blocker = GLASSES_APP))
        // A recreated Activity keeps the single hand-off of this launch.
        val recreated = AutoGlassesSetup(consentRequested = true, glassesAppPageOpened = true)
        assertEquals(Step.CONNECT, recreated.at("REGISTERED"))
        assertEquals(Step.RETRY_LATER, recreated.at("REGISTERED", blocks = 1, blocker = GLASSES_APP))
    }

    @Test fun firmwareAndSdkMismatchesNeverRetry() {
        val firmware = connectedSetup()
        assertEquals(Step.OPEN_FIRMWARE_UPDATE, firmware.at("REGISTERED", blocks = 1, blocker = AutoGlassesSetup.FIRMWARE_UPDATE))
        assertEquals(Step.WAIT, firmware.at("REGISTERED", blocks = 2, blocker = AutoGlassesSetup.FIRMWARE_UPDATE))
        val sdk = connectedSetup()
        assertEquals(Step.WAIT, sdk.at("REGISTERED", blocks = 1, blocker = AutoGlassesSetup.SDK_UPDATE))
        assertEquals(AutoGlassesSetup.SDK_UPDATE, sdk.blockedBy)
        assertFalse(sdk.retryPending)
        assertFalse(sdk.glassesAppPageOpened)
    }

    @Test fun instantDropsKeepReconnectingWithAGrowingBackoffThatStartedDoesNotReset() {
        val setup = connectedSetup()
        val delays = mutableListOf<Long>()
        for (drops in 1..5) {
            // Each dropped session reached STARTED for a moment first.
            assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED", drops = drops - 1))
            assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", drops = drops, error = "dropped"))
            delays += setup.retryDelayMs
            assertFalse(setup.idleReconnect)
            assertEquals(drops, setup.dropStreak)
            setup.retryDue()
            assertEquals(Step.CONNECT, setup.at("REGISTERED", drops = drops))
        }
        assertEquals(listOf(5_000L, 10_000L, 30_000L, 30_000L, 30_000L), delays)
        assertEquals("dropped", setup.lastFailure)
        // A session that ran and then ended idle proves the glasses work again.
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", drops = 5, ends = 1))
        assertEquals(0, setup.dropStreak)
        setup.retryDue()
        assertEquals(Step.CONNECT, setup.at("REGISTERED", drops = 5, ends = 1))
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", drops = 6, ends = 1))
        assertEquals(5_000L, setup.retryDelayMs)
    }

    @Test fun glassesNotAwakeReconnectAutomaticallyWithBackoffAndNoCap() {
        // 23:17 to 23:20 on RB Meta 0076: taken off, then START_ERROR_DEVICE_UNAVAILABLE on every connect.
        val setup = connectedSetup()
        val delays = mutableListOf<Long>()
        for (asleep in 1..6) {
            assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", asleep = asleep, error = "not awake"))
            assertTrue(setup.asleep)
            delays += setup.retryDelayMs
            setup.retryDue()
            assertEquals(Step.CONNECT, setup.at("REGISTERED", asleep = asleep))
        }
        assertEquals(listOf(5_000L, 10_000L, 30_000L, 30_000L, 30_000L, 30_000L), delays)
        // Put back on: the next attempt starts, and the backoff starts over.
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STARTED", asleep = 6))
        assertFalse(setup.asleep)
        assertNull(setup.lastFailure)
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", asleep = 7))
        assertEquals(5_000L, setup.retryDelayMs)
    }

    @Test fun glassesNotAwakeWaitForTheForegroundToReconnect() {
        val setup = connectedSetup()
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", asleep = 1, foreground = false))
        setup.retryDue()
        assertEquals(Step.WAIT, setup.at("REGISTERED", asleep = 1, foreground = false))
        assertEquals(Step.CONNECT, setup.at("REGISTERED", asleep = 1))
    }

    @Test fun connectButtonStartsCleanWithoutAPendingRetryOrRefusal() {
        val setup = connectedSetup()
        assertEquals(Step.OPEN_GLASSES_APP_UPDATE, setup.at("REGISTERED", blocks = 1, blocker = GLASSES_APP))
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", blocks = 1, drops = 1))
        setup.userReconnected()
        assertNull(setup.blockedBy)
        assertFalse(setup.retryPending || setup.asleep)
        assertEquals(0, setup.dropStreak)
        // The button connects by itself; the automatic steps add no second session.
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "CONNECTING", blocks = 1, drops = 1))
        assertEquals(Step.WAIT, setup.at("REGISTERED", blocks = 1, drops = 1))
        // Its outcome is handled like any other: here the glasses are asleep.
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", blocks = 1, drops = 1, asleep = 1))
        assertEquals(5_000L, setup.retryDelayMs)
    }

    @Test fun aStoppingSessionIsNotReconnectedOverItsOutcomeDecides() {
        val setup = AutoGlassesSetup(consentRequested = true)
        // STOPPED while the adapter waits for the reason: no second session on top of it.
        assertEquals(Step.WAIT, setup.at("REGISTERED", connection = "STOPPED"))
        assertEquals(Step.RETRY_LATER, setup.at("REGISTERED", drops = 1))
        setup.retryDue()
        assertEquals(Step.CONNECT, setup.at("REGISTERED", drops = 1))
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
        drops: Int = 0,
        asleep: Int = 0,
        blocks: Int = 0,
        blocker: String? = null,
    ): Step = next(
        MetaState(initialized = initialized, registration = registration, connection = connection, error = error,
            sessionFailures = failures, sessionEnds = ends, deviceCount = devices,
            sessionDrops = drops, sessionAsleep = asleep, sessionBlocks = blocks, blocker = blocker),
        foreground, pending, settled,
    )

    private companion object { const val GLASSES_APP = AutoGlassesSetup.GLASSES_APP_UPDATE }
}
