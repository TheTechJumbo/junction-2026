package com.junction.glasses.live

import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.*
import org.junit.Test

/** The newest-frame pump behind fixed-tick recording: newest only, never repeated, never waited for. */
class FramePumpTest {
    @Test fun eachTickGetsTheNewestFrameOnceAndSkipsWhenNothingIsNew() {
        runBlocking {
            val camera = Channel<Int>(Channel.UNLIMITED)
            val pump = FramePump(this, capture = { camera.receive() })
            pump.start()
            assertNull(pump.takeNew())
            camera.send(1); yield(); yield()
            assertEquals(1, pump.takeNew())
            assertNull("no repeat of frame 1", pump.takeNew())
            camera.send(2); camera.send(3); yield(); yield(); yield(); yield()
            assertEquals("frame 2 was superseded", 3, pump.takeNew())
            assertNull(pump.takeNew())
            pump.stop()
            assertFalse(pump.running)
        }
    }

    @Test fun failedCapturesAreCountedAndRetried() {
        runBlocking {
            var calls = 0
            val pump = FramePump(this, capture = {
                calls++
                if (calls <= 2) throw CameraFailure("Glasses not connected.") else calls
            }, retryDelayMs = 1)
            pump.start()
            assertTrue(pump.awaitFirst(2_000))
            assertEquals(2, pump.failures)
            assertNull(pump.lastError)
            assertNotNull(pump.takeNew())
            pump.stop()
        }
    }

    @Test fun awaitFirstGivesUpWhenTheCameraNeverStarts() {
        runBlocking {
            val pump = FramePump(this, capture = { throw CameraFailure("Glasses not connected.") }, retryDelayMs = 5)
            pump.start()
            assertFalse(pump.awaitFirst(50))
            assertEquals("Glasses not connected.", pump.lastError?.message)
            pump.stop()
        }
    }
}
