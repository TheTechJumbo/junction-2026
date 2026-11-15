package com.junction.glasses.live

import kotlinx.coroutines.CompletableDeferred
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.sqrt

/**
 * Hands the newest camera frame to the one taker that is waiting. A frame offered while nobody
 * waits is dropped, so nothing ever queues: after a reply the next photo is the next frame the
 * camera delivers, never an old one. Producer calls may come from any thread.
 */
internal class FrameSlot<F>(private val clock: () -> Long = System::nanoTime) {
    private class Waiter<T>(val sinceNs: Long, val result: CompletableDeferred<T>)
    private val waiter = AtomicReference<Waiter<F>?>(null)

    /** When the current taker started waiting, or null when no frame is wanted (drop the frame). */
    fun wantedSince(): Long? = waiter.get()?.sinceNs

    /** Gives [frame] to the waiting taker; false (frame dropped) when nobody waits. */
    fun offer(frame: F): Boolean = waiter.getAndSet(null)?.result?.complete(frame) ?: false

    /** Fails the waiting taker, for example when a frame could not be read. */
    fun fail(error: Throwable) { waiter.getAndSet(null)?.result?.completeExceptionally(error) }

    /** Waits for the next frame offered after this call. One taker at a time. */
    suspend fun take(): F {
        val mine = Waiter(clock(), CompletableDeferred<F>())
        check(waiter.compareAndSet(null, mine)) { "Only one frame request at a time" }
        try {
            return mine.result.await()
        } finally {
            waiter.compareAndSet(mine, null)
        }
    }
}

/**
 * Gyroscope steadiness: a frame is sent when the mean angular speed over the last [windowNs] is at
 * most [limitRadPerS], when there are no recent gyroscope readings (no sensor), or once the frame
 * has been wanted for [maxWaitNs], so shaking never stalls the stream.
 */
internal class SteadyGate(
    private val limitRadPerS: Double = 1.5,
    private val windowNs: Long = 150_000_000L,
    private val maxWaitNs: Long = 1_000_000_000L,
) {
    private val samples = ArrayDeque<Pair<Long, Double>>()

    @Synchronized fun record(atNs: Long, x: Float, y: Float, z: Float) {
        samples.addLast(atNs to sqrt((x * x + y * y + z * z).toDouble()))
        while (samples.isNotEmpty() && samples.first().first < atNs - windowNs) samples.removeFirst()
    }

    /** Mean angular speed (rad/s) over the window ending at [nowNs], or null without recent readings. */
    @Synchronized fun speed(nowNs: Long): Double? {
        val recent = samples.filter { it.first in (nowNs - windowNs)..nowNs }
        return if (recent.isEmpty()) null else recent.sumOf { it.second } / recent.size
    }

    fun accept(nowNs: Long, wantedSinceNs: Long): Boolean {
        val speed = speed(nowNs) ?: return true
        return speed <= limitRadPerS || nowNs - wantedSinceNs >= maxWaitNs
    }
}

/** Decides, per camera frame, whether to encode it: only when a frame is wanted and the phone is steady. */
internal class FramePicker<F>(val slot: FrameSlot<F>, private val gate: SteadyGate) {
    private val skipped = AtomicInteger()
    /** Frames skipped so far because the phone was turning fast. */
    val shakySkipped: Int get() = skipped.get()

    /** True when the frame arriving at [nowNs] should be encoded and offered to [slot]. */
    fun wants(nowNs: Long): Boolean {
        val since = slot.wantedSince() ?: return false
        if (gate.accept(nowNs, since)) return true
        skipped.incrementAndGet()
        return false
    }
}
