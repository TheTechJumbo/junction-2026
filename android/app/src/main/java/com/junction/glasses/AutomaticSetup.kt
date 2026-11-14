package com.junction.glasses

import android.content.Intent
import com.junction.glasses.meta.MetaState

/**
 * Decides the automatic glasses steps for one app launch from the adapter's state. It never polls
 * Meta AI: the registration and consent hand-offs happen at most once per launch, and a connection
 * is attempted at launch and once per return to the foreground. The glasses end a session after a
 * few idle minutes; that is routine, so once a session has started, every later end or failure is
 * followed by a quiet reconnect after [IDLE_BACKOFF_MS] (5 s, 10 s, then every 30 s, no cap) while in
 * the foreground. Before any session started, a failure is retried [MAX_RETRIES] times, 10 s apart.
 */
internal class AutoGlassesSetup(registrationRequested: Boolean = false, consentRequested: Boolean = false) {
    enum class Step { WAIT, REGISTER, REQUEST_CONSENT, CONNECT, RETRY_LATER }

    var registrationRequested = registrationRequested
        private set
    var consentRequested = consentRequested
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

    /** Each return to the foreground may reconnect once, with a fresh retry budget. */
    fun onForeground() { connectWanted = true; retryPending = false; retriesUsed = 0; idleAttempts = 0 }
    fun consentFinished() { consentRunning = false }
    fun retryDue() { if (retryPending) { retryPending = false; connectWanted = true } }
    /** The user chose Disconnect: no automatic reconnect until the next return to the foreground. */
    fun userDisconnected() { connectWanted = false; retryPending = false; idleReconnect = false }

    fun next(state: MetaState, foreground: Boolean, externalFlowPending: Boolean, registrationSettled: Boolean): Step {
        val newFailure = failuresSeen >= 0 && state.sessionFailures > failuresSeen
        val newEnd = endsSeen >= 0 && state.sessionEnds > endsSeen
        failuresSeen = state.sessionFailures
        endsSeen = state.sessionEnds
        if (state.connection == STARTED) {
            keepAlive = true; idleReconnect = false; retriesUsed = 0; idleAttempts = 0; lastFailure = null
        }
        if (newFailure) lastFailure = state.error
        if (newEnd || (newFailure && keepAlive)) {
            keepAlive = true; idleReconnect = true
            retryDelayMs = IDLE_BACKOFF_MS[minOf(idleAttempts, IDLE_BACKOFF_MS.size - 1)]
            idleAttempts++
            retryPending = true; connectWanted = false
            return Step.RETRY_LATER
        }
        if (newFailure && retriesUsed < MAX_RETRIES) {
            retriesUsed++; retryDelayMs = FAILURE_RETRY_MS
            retryPending = true; connectWanted = false
            return Step.RETRY_LATER
        }
        if (!state.initialized || !foreground || externalFlowPending || consentRunning) return Step.WAIT
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
        private val IN_PROGRESS = setOf("REGISTERING", "UNREGISTERING")
        private val NOT_CONNECTED = setOf("DISCONNECTED", "STOPPED")
    }
}

/** DEBUG-only adb launch values (see README). Never logged, persisted or shown; [toString] hides the token. */
internal class DebugBackendLaunch private constructor(val backendUrl: String?, val apiToken: String?) {
    override fun toString() = "DebugBackendLaunch(backendUrl=$backendUrl, apiToken=${if (apiToken == null) "absent" else "redacted"})"

    companion object {
        const val EXTRA_BACKEND_URL = "backend_url"
        const val EXTRA_API_TOKEN = "api_token"

        /** Null in release builds, which ignore these extras entirely, or when neither extra was sent. */
        fun read(intent: Intent?, debug: Boolean): DebugBackendLaunch? {
            if (!debug || intent == null) return null
            return try {
                if (!intent.hasExtra(EXTRA_BACKEND_URL) && !intent.hasExtra(EXTRA_API_TOKEN)) null
                else DebugBackendLaunch(
                    intent.getStringExtra(EXTRA_BACKEND_URL)?.trim()?.takeIf { it.isNotEmpty() },
                    intent.getStringExtra(EXTRA_API_TOKEN)?.takeIf { it.isNotBlank() },
                )
            } catch (_: RuntimeException) { null }
        }

        /** A copy without the debug extras, so an Activity recreation cannot reuse them. */
        fun withoutExtras(intent: Intent): Intent = Intent(intent).apply {
            removeExtra(EXTRA_BACKEND_URL); removeExtra(EXTRA_API_TOKEN)
        }
    }
}
