package com.junction.glasses.live

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/** The battery as the phone reports it; null fields when unknown. */
internal data class BatteryReading(val percent: Int?, val temperatureC: Double?)

/** What the always-on recording has done, for the card and the notification. Never contains the key. */
internal data class AlwaysOnStatus(
    val recording: Boolean = false,
    val stopping: Boolean = false,
    val source: CameraSource = CameraSource.GLASSES,
    val zone: String = "",
    val session: String? = null,
    val framesCaptured: Int = 0,
    val ticksWithoutFrame: Int = 0,
    val chunksSealed: Int = 0,
    val chunksSent: Int = 0,
    val chunksWaiting: Int = 0,
    val bytesWaiting: Long = 0,
    val chunksDropped: Int = 0,
    val lastUpload: String? = null,
    val uploadError: String? = null,
    val waiting: String? = null,
    val instantFix: String? = null,
    val viaLan: Boolean = false,
    val batteryPercent: Int? = null,
    val temperatureC: Double? = null,
    val heatPaused: Boolean = false,
)

/**
 * Always-on recording of the room for the laptop. On a fixed tick of [tickMs] (10 frames/s) it takes
 * the newest new frame of [camera] (FramePump: never repeated, never waited for) into chunks of 10 s or
 * 100 frames, stored in [queue] and uploaded strictly in order to {relay}/recording (final=1 at the
 * end), retried with backoff until they get through. The newest frame also goes to /locate for an
 * instant fix, one request at a time. Above 45 °C capture pauses until the phone is below 43 °C.
 * Runs in [scope] until [stop]. [glassesWaiting] says what the glasses still need (null: nothing).
 */
internal class AlwaysOnRecorder(
    private val scope: CoroutineScope,
    private val camera: FrameCamera<CameraFrame>,
    private val relay: FailoverRelay<RelayClient>,
    private val queue: ChunkQueue,
    private val zone: String,
    private val session: String,
    source: CameraSource,
    private val glassesWaiting: () -> String? = { null },
    private val battery: () -> BatteryReading? = { null },
    private val tickMs: Long = TICK_MS,
) {
    private val mutableStatus = MutableStateFlow(AlwaysOnStatus(recording = true, source = source, zone = zone, session = session))
    val status: StateFlow<AlwaysOnStatus> = mutableStatus.asStateFlow()
    private val pump = FramePump(scope, capture = { camera.capture() }, retryDelayMs = CAMERA_RETRY_MS)
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private val captureDone = CompletableDeferred<Unit>()
    @Volatile private var newestFrame: ByteArray? = null
    @Volatile private var stopRequested = false
    private var uploadJob: Job? = null
    private val jobs = mutableListOf<Job>()
    private var refusedByRelay = 0

    fun start() {
        jobs += scope.launch { captureLoop() }
        uploadJob = scope.launch { uploadLoop() }.also { jobs += it }
        jobs += scope.launch { locateLoop() }
        jobs += scope.launch { batteryLoop() }
        wake.trySend(Unit)
    }

    /**
     * Stops capture, stores what is left as the final chunk (final=1) and waits up to [drainMs] for the
     * waiting chunks to reach the relay; any still waiting stay in app storage for the next recording.
     */
    suspend fun stop(drainMs: Long = DRAIN_MS) {
        stopRequested = true
        mutableStatus.update { it.copy(stopping = true) }
        withTimeoutOrNull(FINAL_CHUNK_MS) { captureDone.await() }
        wake.trySend(Unit)
        withTimeoutOrNull(drainMs) { uploadJob?.join() }
        jobs.forEach { it.cancel() }
        mutableStatus.update { it.copy(recording = false, stopping = false) }
    }

    private suspend fun captureLoop() {
        var builder: ChunkBuilder? = null
        var nextIndex = 0L
        var seq = 0
        var frameEver = false
        pump.start()
        try {
            while (!stopRequested) {
                val tickStart = nowMs()
                if (mutableStatus.value.heatPaused) {
                    if (pump.running) { pump.stop(); camera.stop() }
                    delay(HEAT_CHECK_MS)
                    continue
                }
                if (!pump.running) pump.start()
                val frame = pump.takeNew()
                if (frame != null) {
                    frameEver = true
                    val chunk = builder ?: ChunkBuilder(nextIndex).also { builder = it }
                    chunk.add(frame.jpeg, System.currentTimeMillis(), nowMs())
                    newestFrame = frame.jpeg
                    mutableStatus.update { it.copy(framesCaptured = it.framesCaptured + 1, waiting = null) }
                } else {
                    val waiting = glassesWaiting() ?: pump.lastError?.let { LiveRelayLoop.describe(it) }
                    mutableStatus.update {
                        it.copy(waiting = waiting, ticksWithoutFrame = it.ticksWithoutFrame + if (frameEver && waiting == null) 1 else 0)
                    }
                }
                val due = builder
                if (due != null && due.due(nowMs())) {
                    store(due.finish(), seq++, final = false)
                    nextIndex = due.nextIndex
                    builder = null
                }
                delay((tickMs - (nowMs() - tickStart)).coerceAtLeast(0))
            }
        } finally {
            withContext(NonCancellable) {
                pump.stop()
                camera.stop()
                // The last chunk carries final=1, empty when nothing new came since the previous one.
                store(builder?.finish() ?: ByteArray(0), seq, final = true)
                captureDone.complete(Unit)
            }
        }
    }

    private suspend fun store(zip: ByteArray, seq: Int, final: Boolean) {
        try {
            withContext(Dispatchers.IO) { queue.add(session, seq, final, zone, zip) }
            mutableStatus.update { it.copy(chunksSealed = it.chunksSealed + 1) }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            mutableStatus.update { it.copy(uploadError = "A chunk could not be stored on the phone (storage full?).") }
        }
        refreshQueue()
        wake.trySend(Unit)
    }

    private suspend fun uploadLoop() {
        var failures = 0
        while (true) {
            val chunk = withContext(Dispatchers.IO) { queue.next() }
            if (chunk == null) {
                if (captureDone.isCompleted) return
                withTimeoutOrNull(IDLE_RESCAN_MS) { wake.receive() }
                continue
            }
            try {
                val reply = relay.call { it.uploadChunk(chunk.zone, chunk.session, chunk.seq, chunk.final, chunk.file) }
                withContext(Dispatchers.IO) { queue.remove(chunk) }
                failures = 0
                mutableStatus.update {
                    it.copy(chunksSent = it.chunksSent + 1, uploadError = null, viaLan = relay.usingLan,
                        lastUpload = "${clock()} · chunk ${chunk.seq}" + (if (chunk.final) " (last)" else "") +
                            (reply.status?.let { s -> " · relay: $s" } ?: ""))
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val status = (e as? RelayFailure)?.status
                if (UploadBackoff.dropsChunk(status)) {
                    withContext(Dispatchers.IO) { queue.remove(chunk) }
                    refusedByRelay++
                    mutableStatus.update { it.copy(uploadError = "${clock()} · the relay refused chunk ${chunk.seq}: ${LiveRelayLoop.describe(e)}") }
                } else {
                    failures++
                    val wait = UploadBackoff.delayMs(failures)
                    mutableStatus.update { it.copy(uploadError = "${clock()} · ${LiveRelayLoop.describe(e)} Retrying in ${wait / 1000} s; chunks stay on the phone.") }
                    refreshQueue()
                    delay(wait)
                }
            }
            refreshQueue()
        }
    }

    private suspend fun refreshQueue() {
        val (count, bytes) = withContext(Dispatchers.IO) { queue.pending().let { it.size to it.sumOf { c -> c.bytes } } }
        mutableStatus.update { it.copy(chunksWaiting = count, bytesWaiting = bytes, chunksDropped = queue.dropped + refusedByRelay) }
    }

    private suspend fun locateLoop() {
        var last: ByteArray? = null
        while (!stopRequested) {
            val jpeg = newestFrame
            if (jpeg == null || jpeg === last) { delay(LOCATE_IDLE_MS); continue }
            last = jpeg
            try {
                val reply = relay.call { it.locate(jpeg) }
                mutableStatus.update { it.copy(instantFix = reply.headline.ifBlank { reply.state }, viaLan = relay.usingLan) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                mutableStatus.update { it.copy(instantFix = "Instant fix unavailable: ${LiveRelayLoop.describe(e)}") }
                delay(LOCATE_RETRY_MS)
            }
        }
    }

    private suspend fun batteryLoop() {
        while (!stopRequested) {
            val reading = try { battery() } catch (_: Exception) { null }
            mutableStatus.update {
                it.copy(batteryPercent = reading?.percent, temperatureC = reading?.temperatureC,
                    heatPaused = AlwaysOnRules.heatPaused(reading?.temperatureC, it.heatPaused))
            }
            delay(BATTERY_CHECK_MS)
        }
    }

    private fun nowMs(): Long = System.nanoTime() / 1_000_000
    private fun clock(): String = LocalTime.now().format(CLOCK)

    companion object {
        const val TICK_MS = 100L
        const val CAMERA_RETRY_MS = 2_000L
        const val DRAIN_MS = 60_000L
        private const val FINAL_CHUNK_MS = 5_000L
        private const val IDLE_RESCAN_MS = 5_000L
        private const val LOCATE_IDLE_MS = 100L
        private const val LOCATE_RETRY_MS = 2_000L
        private const val HEAT_CHECK_MS = 1_000L
        private const val BATTERY_CHECK_MS = 10_000L
        private val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss")
    }
}

/** The process's always-on recording: one at a time, shared by the service, the card and auto-start. */
internal object AlwaysOn {
    /** The relay settings and camera for the next start; set by the Activity before it starts the service. */
    class Config(val relayUrl: String, val lanUrl: String?, val key: String, val zone: String, val source: CameraSource) {
        override fun toString() = "Config(relayUrl=$relayUrl, lanUrl=$lanUrl, key=redacted, zone=$zone, source=$source)"
    }

    @Volatile var pending: Config? = null
    @Volatile var recorder: AlwaysOnRecorder? = null
    /** Stop was tapped in this run of the app: no automatic start again until Start is tapped. */
    @Volatile var stoppedByUser = false
    /** The current or last recording's status (the service mirrors its recorder here). */
    val status = MutableStateFlow(AlwaysOnStatus())
    val isRecording: Boolean get() = recorder != null
}
