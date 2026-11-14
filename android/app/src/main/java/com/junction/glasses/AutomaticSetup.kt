package com.junction.glasses

import android.content.Intent
import com.junction.glasses.meta.MetaState

/**
 * Decides the automatic glasses steps for one app launch from the adapter's state. It never polls
 * Meta AI: the registration, consent and glasses-app hand-offs happen at most once per launch, and a
 * connection is attempted at launch and once per return to the foreground. While in the foreground,
 * these reconnect by themselves after [IDLE_BACKOFF_MS] (5 s, 10 s, then every 30 s, no cap): an idle
 * end (the glasses end a session after a few idle minutes), glasses that are not awake (off, folded, in
 * the case), a session dropped within seconds of starting (the backoff grows with each drop in a row),
 * and, once a session has started, any other failure. Before any session started, other failures are
 * retried [MAX_RETRIES] times, 10 s apart. A session refused because the glasses lack this app's
 * Developer Mode glasses app never loops: it opens Meta AI's install page once per launch (no glasses
 * reset needed), then reconnects on return and re-checks [MAX_BLOCK_RETRIES] times while Meta AI installs.
 * While the SDK reports no glasses at all, nothing is opened or connected automatically.
 */
internal class AutoGlassesSetup(
    registrationRequested: Boolean = false,
    consentRequested: Boolean = false,
    glassesAppPageOpened: Boolean = false,
) {
    enum class Step { WAIT, REGISTER, REQUEST_CONSENT, CONNECT, RETRY_LATER, OPEN_GLASSES_APP_UPDATE, OPEN_FIRMWARE_UPDATE }

    var registrationRequested = registrationRequested
        private set
    var consentRequested = consentRequested
        private set
    /** Meta AI's glasses app or firmware page was opened automatically in this launch; never twice. */
    var glassesAppPageOpened = glassesAppPageOpened
        private set
    /** The latest refusal that needs the user (a [com.junction.glasses.meta.SessionOutcome] name); cleared by a started session. */
    var blockedBy: String? = null
        private set
    /** Sessions dropped right after starting, in a row; reset by an idle end, Connect or the foreground. */
    var dropStreak = 0
        private set
    /** The last attempt found the glasses not awake (off, folded, in the case); reconnecting by itself. */
    var asleep = false
        private set
    var consentRunning = false
        private set
    var retryPending = false
        private set
    var retriesUsed = 0
        private set
    /** True while reconnecting after the glasses ended a started session; shown as idle, not failed. */
    var idleReconnect = false
        private set
    /** Delay the caller waits before [retryDue] after a [Step.RETRY_LATER]. */
    var retryDelayMs = FAILURE_RETRY_MS
        private set
    /** Last session failure, kept until a session starts so it stays visible while retrying. */
    var lastFailure: String? = null
        private set
    private var keepAlive = false
    private var idleAttempts = 0
    private var connectWanted = true
    private var failuresSeen = -1
    private var endsSeen = -1
    private var dropsSeen = -1
    private var asleepSeen = -1
    private var blocksSeen = -1
    private var blockRetries = 0
    /** A Meta AI page to open as soon as the app is in the foreground with no other hand-off running. */
    private var pageToOpen: Step? = null

    /** Each return to the foreground may reconnect once, with a fresh retry budget. */
    fun onForeground() {
        connectWanted = true; retryPending = false; retriesUsed = 0; idleAttempts = 0
        dropStreak = 0; blockRetries = 0
    }
    fun consentFinished() { consentRunning = false }
    fun retryDue() { if (retryPending) { retryPending = false; connectWanted = true } }
    /** The user chose Disconnect: no automatic reconnect until the next return to the foreground. */
    fun userDisconnected() { connectWanted = false; retryPending = false; idleReconnect = false; asleep = false }
    /** The user tapped Connect / reconnect, which connects by itself: fresh budgets, no pending retry, no refusal. */
    fun userReconnected() {
        connectWanted = false; retryPending = false; retriesUsed = 0; idleAttempts = 0
        dropStreak = 0; blockRetries = 0; blockedBy = null; asleep = false; idleReconnect = false
    }

    fun next(state: MetaState, foreground: Boolean, externalFlowPending: Boolean, registrationSettled: Boolean): Step {
        val newFailure = failuresSeen >= 0 && state.sessionFailures > failuresSeen
        val newEnd = endsSeen >= 0 && state.sessionEnds > endsSeen
        val newDrop = dropsSeen >= 0 && state.sessionDrops > dropsSeen
        val newAsleep = asleepSeen >= 0 && state.sessionAsleep > asleepSeen
        val newBlock = blocksSeen >= 0 && state.sessionBlocks > blocksSeen
        failuresSeen = state.sessionFailures
        endsSeen = state.sessionEnds
        dropsSeen = state.sessionDrops
        asleepSeen = state.sessionAsleep
        blocksSeen = state.sessionBlocks
        if (state.connection == STARTED) {
            keepAlive = true; idleReconnect = false; retriesUsed = 0; idleAttempts = 0; lastFailure = null
            blockedBy = null; asleep = false
        }
        if (newFailure || newDrop || newAsleep || newBlock) lastFailure = state.error
        if (newBlock) {
            // Retrying cannot fix this: open Meta AI's page once, and never feed the quiet reconnect loop.
            blockedBy = state.blocker; idleReconnect = false; asleep = false; retryPending = false; connectWanted = false
            val page = when (state.blocker) {
                GLASSES_APP_UPDATE -> Step.OPEN_GLASSES_APP_UPDATE
                FIRMWARE_UPDATE -> Step.OPEN_FIRMWARE_UPDATE
                else -> null
            }
            if (page != null && !glassesAppPageOpened) pageToOpen = page
            else if (state.blocker == GLASSES_APP_UPDATE && blockRetries < MAX_BLOCK_RETRIES) {
                // Back from Meta AI: its install takes a few seconds, so check again shortly.
                blockRetries++; retryDelayMs = BLOCK_RETRY_MS; retryPending = true
                return Step.RETRY_LATER
            }
        } else if (newDrop) {
            // Drops reach STARTED, which resets the idle backoff, so they keep their own count.
            idleReconnect = false; asleep = false; connectWanted = false
            dropStreak++
            retryDelayMs = IDLE_BACKOFF_MS[minOf(dropStreak - 1, IDLE_BACKOFF_MS.size - 1)]
            retryPending = true
            return Step.RETRY_LATER
        } else if (newAsleep) {
            idleReconnect = false; asleep = true
            retryDelayMs = IDLE_BACKOFF_MS[minOf(idleAttempts, IDLE_BACKOFF_MS.size - 1)]
            idleAttempts++
            retryPending = true; connectWanted = false
            return Step.RETRY_LATER
        } else if (newEnd || (newFailure && keepAlive)) {
            if (newEnd) dropStreak = 0
            keepAlive = true; idleReconnect = true; asleep = false
            retryDelayMs = IDLE_BACKOFF_MS[minOf(idleAttempts, IDLE_BACKOFF_MS.size - 1)]
            idleAttempts++
            retryPending = true; connectWanted = false
            return Step.RETRY_LATER
        } else if (newFailure && retriesUsed < MAX_RETRIES) {
            retriesUsed++; retryDelayMs = FAILURE_RETRY_MS
            retryPending = true; connectWanted = false
            return Step.RETRY_LATER
        }
        if (!state.initialized || !foreground || externalFlowPending || consentRunning) return Step.WAIT
        pageToOpen?.let { page -> pageToOpen = null; glassesAppPageOpened = true; return page }
        // No glasses reported: never open Meta AI or connect by ourselves; the glasses buttons stay manual.
        if (state.deviceCount == 0) return Step.WAIT
        if (state.registration != REGISTERED) {
            // registrationSettled gives the SDK time to report an existing registration first.
            if (registrationRequested || !registrationSettled || state.registration in IN_PROGRESS) return Step.WAIT
            registrationRequested = true
            return Step.REGISTER
        }
        if (!consentRequested) {
            consentRequested = true; consentRunning = true
            return Step.REQUEST_CONSENT
        }
        if (!connectWanted || retryPending) return Step.WAIT
        connectWanted = false
        return if (state.connection in NOT_CONNECTED) Step.CONNECT else Step.WAIT
    }

    companion object {
        const val REGISTERED = "REGISTERED"
        const val STARTED = "STARTED"
        const val MAX_RETRIES = 3
        const val FAILURE_RETRY_MS = 10_000L
        val IDLE_BACKOFF_MS = longArrayOf(5_000L, 10_000L, 30_000L)
        /** Drops in a row after which the card suggests reinstalling the glasses app in Meta AI. */
        const val DROP_HINT_AFTER = 3
        /** Reconnects after returning from Meta AI's glasses app page, while its install finishes. */
        const val MAX_BLOCK_RETRIES = 2
        const val BLOCK_RETRY_MS = 15_000L
        const val GLASSES_APP_UPDATE = "GLASSES_APP_UPDATE"
        const val FIRMWARE_UPDATE = "FIRMWARE_UPDATE"
        const val SDK_UPDATE = "SDK_UPDATE"
        private val IN_PROGRESS = setOf("REGISTERING", "UNREGISTERING")
        /** STOPPED is left out: the adapter reports DISCONNECTED once a stopped session is finished. */
        private val NOT_CONNECTED = setOf("DISCONNECTED")
    }
}

/**
 * DEBUG-only adb launch values (see README): the backend address and token, and the live camera's
 * relay address, key and zone. Never logged, persisted unencrypted or shown; [toString] hides secrets.
 */
internal class DebugBackendLaunch private constructor(
    val backendUrl: String?,
    val apiToken: String?,
    val relayUrl: String?,
    val relayKey: String?,
    val relayZone: String?,
    /** The laptop's address on the same Wi-Fi (http://<LAN IP>:8090), used when 127.0.0.1 does not answer. */
    val relayLanUrl: String? = null,
    /** `--ez always_on false` switches the always-on recording off (and stops it); true opts in. Null: unchanged. */
    val alwaysOn: Boolean? = null,
) {
    val hasBackend: Boolean get() = backendUrl != null || apiToken != null
    val hasRelay: Boolean get() = relayUrl != null || relayKey != null || relayZone != null || relayLanUrl != null

    override fun toString() = "DebugBackendLaunch(backendUrl=$backendUrl, apiToken=${secret(apiToken)}, " +
        "relayUrl=$relayUrl, relayKey=${secret(relayKey)}, relayZone=$relayZone, relayLanUrl=$relayLanUrl, alwaysOn=$alwaysOn)"

    companion object {
        const val EXTRA_BACKEND_URL = "backend_url"
        const val EXTRA_API_TOKEN = "api_token"
        const val EXTRA_RELAY_URL = "relay_url"
        const val EXTRA_RELAY_KEY = "relay_key"
        const val EXTRA_RELAY_ZONE = "relay_zone"
        const val EXTRA_RELAY_LAN_URL = "relay_lan_url"
        const val EXTRA_ALWAYS_ON = "always_on"
        private val EXTRAS = listOf(EXTRA_BACKEND_URL, EXTRA_API_TOKEN, EXTRA_RELAY_URL, EXTRA_RELAY_KEY, EXTRA_RELAY_ZONE, EXTRA_RELAY_LAN_URL, EXTRA_ALWAYS_ON)

        private fun secret(value: String?) = if (value == null) "absent" else "redacted"

        /** Null in release builds, which ignore these extras entirely, or when none of them was sent. */
        fun read(intent: Intent?, debug: Boolean): DebugBackendLaunch? {
            if (!debug || intent == null) return null
            return try {
                if (EXTRAS.none { intent.hasExtra(it) }) null
                else DebugBackendLaunch(
                    backendUrl = intent.getStringExtra(EXTRA_BACKEND_URL)?.trim()?.takeIf { it.isNotEmpty() },
                    apiToken = intent.getStringExtra(EXTRA_API_TOKEN)?.takeIf { it.isNotBlank() },
                    relayUrl = intent.getStringExtra(EXTRA_RELAY_URL)?.trim()?.takeIf { it.isNotEmpty() },
                    relayKey = intent.getStringExtra(EXTRA_RELAY_KEY)?.trim()?.takeIf { it.isNotEmpty() },
                    relayZone = intent.getStringExtra(EXTRA_RELAY_ZONE)?.trim()?.takeIf { it.isNotEmpty() },
                    relayLanUrl = intent.getStringExtra(EXTRA_RELAY_LAN_URL)?.trim()?.takeIf { it.isNotEmpty() },
                    alwaysOn = if (intent.hasExtra(EXTRA_ALWAYS_ON)) intent.getBooleanExtra(EXTRA_ALWAYS_ON, false) else null,
                )
            } catch (_: RuntimeException) { null }
        }

        /** A copy without the debug extras, so an Activity recreation cannot reuse them. */
        fun withoutExtras(intent: Intent): Intent = Intent(intent).apply {
            EXTRAS.forEach { removeExtra(it) }
        }
    }
}
