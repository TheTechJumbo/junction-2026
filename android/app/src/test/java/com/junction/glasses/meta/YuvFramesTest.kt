package com.junction.glasses.meta

import org.junit.Assert.*
import org.junit.Test

/** Decoded glasses video frames to NV21: layout detection and repacking, without Android or the SDK. */
class YuvFramesTest {
    private fun bytes(vararg values: Int) = ByteArray(values.size) { values[it].toByte() }

    @Test fun interleavedChromaIsReadAsNv12AndRepackedVFirst() {
        val width = 8; val height = 4
        val y = IntArray(width * height) { it }
        // Two chroma rows of U/V pairs: U near 10, V near 200.
        val chroma = intArrayOf(10, 200, 12, 202, 14, 204, 16, 206, 11, 201, 13, 203, 15, 205, 17, 207)
        val frame = bytes(*y, *chroma)
        val layout = YuvFrames.layout(frame, width, height)!!
        assertEquals(true, layout.semiPlanar)
        assertEquals(width, layout.stride)
        val nv21 = YuvFrames.toNv21(frame, width, height, layout.stride, layout.semiPlanar!!)
        assertArrayEquals(bytes(*y), nv21.copyOfRange(0, width * height))
        assertArrayEquals(bytes(200, 10, 202, 12, 204, 14, 206, 16, 201, 11, 203, 13, 205, 15, 207, 17),
            nv21.copyOfRange(width * height, nv21.size))
    }

    @Test fun planarChromaWithPaddedRowsIsReadAsI420() {
        val width = 8; val height = 4; val stride = 10
        val y = IntArray(stride * height) { if (it % stride < width) it else 0 }
        val u = intArrayOf(50, 60, 70, 80, 90, 55, 65, 75, 85, 95)
        val v = intArrayOf(150, 160, 170, 180, 190, 155, 165, 175, 185, 195)
        val frame = bytes(*y, *u, *v)
        val layout = YuvFrames.layout(frame, width, height)!!
        assertEquals(false, layout.semiPlanar)
        assertEquals(stride, layout.stride)
        val nv21 = YuvFrames.toNv21(frame, width, height, layout.stride, false)
        assertEquals(width * height * 3 / 2, nv21.size)
        // Rows lose their padding.
        for (row in 0 until height) for (col in 0 until width) assertEquals((row * stride + col).toByte(), nv21[row * width + col])
        // Chroma row 0 takes the first 4 of each plane row (stride / 2 = 5 per row), V before U.
        assertArrayEquals(bytes(150, 50, 160, 60, 170, 70, 180, 80, 155, 55, 165, 65, 175, 75, 185, 85),
            nv21.copyOfRange(width * height, nv21.size))
        assertEquals("I420, stride 10", YuvFrames.describe(layout))
    }

    @Test fun greyFramesLeaveTheLayoutUndecidedAndShortBuffersAreRejected() {
        val width = 8; val height = 4
        val grey = ByteArray(width * height * 3 / 2) { 128.toByte() }
        assertNull(YuvFrames.layout(grey, width, height)!!.semiPlanar)
        assertNull(YuvFrames.layout(ByteArray(width * height), width, height))
        assertNull(YuvFrames.layout(grey, 7, height))
    }
}
