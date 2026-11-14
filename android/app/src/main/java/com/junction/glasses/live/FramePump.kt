package com.junction.glasses.live

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.yield

/**
 * Keeps the newest encoded frame of a camera ready, so a fixed-tick recorder never waits on the
 * camera or on encoding: [start] captures back to back in [scope] (each capture is a new frame, as
 * the cameras deliver them), and [takeNew] hands out the newest one only if it is newer than the last
 * one taken, else null (a tick with no new frame is skipped, never repeated). A failed capture is
 * counted and retried after [retryDelayMs]. Frames stay in memory.
 */
internal class FramePump<F>(
    private val scope: CoroutineScope,
    private val capture: suspend () -> F,
    private val retryDelayMs: Long = RETRY_DELAY_MS,
) {
    private class Numbered<F>(val number: Long, val frame: F)

    private val latest = MutableStateFlow<Numbered<F>?>(null)
    private var takenNumber = 0L
    private var job: Job? = null
    /** Captures that failed so far (glasses not connected, camera error). */
    @Volatile var failures = 0
        private set
    /** The last capture error, for the screen; null after a frame arrives. */
    @Volatile var lastError: Exception? = null
        private set

    val running: Boolean get() = job?.isActive == true

    fun start() {
        if (running) return
        job = scope.launch {
            var number = latest.value?.number ?: 0L
            while (isActive) {
                try {
                    val frame = capture()
                    lastError = null
                    latest.value = Numbered(++number, frame)
                    yield() // a camera that answers without suspending must not starve the rest of the scope

                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    failures++
                    lastError = e
                    delay(retryDelayMs)
                }
            }
        }
    }

    /** The newest frame if it is newer than the last one taken; null otherwise. Never waits. */
    @Synchronized
    fun takeNew(): F? {
        val newest = latest.value ?: return null
        if (newest.number <= takenNumber) return null
        takenNumber = newest.number
        return newest.frame
    }

    /** Waits up to [timeoutMs] for any frame (the camera has started); false if none came. */
    suspend fun awaitFirst(timeoutMs: Long): Boolean = withTimeoutOrNull(timeoutMs) { latest.first { it != null } } != null

    fun stop() {
        job?.cancel()
        job = null
    }

    companion object {
        const val RETRY_DELAY_MS = 500L
    }
}
