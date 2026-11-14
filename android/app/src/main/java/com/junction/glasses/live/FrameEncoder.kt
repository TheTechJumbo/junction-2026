package com.junction.glasses.live

import android.graphics.Bitmap
import android.graphics.Matrix
import java.io.ByteArrayOutputStream
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * One frame for the relay: an upright JPEG and, at most twice a second, a small thumbnail for the
 * screen. [shakySkipped] counts frames skipped so far because the phone was moving fast. Memory only.
 */
internal class CameraFrame(val jpeg: ByteArray, val thumbnail: Bitmap?, val shakySkipped: Int)

/** Upright JPEG, long side [longSide] at most, quality [quality]; a thumbnail at most twice a second. */
internal class FrameEncoder(longSide: Int = DEFAULT_LONG_SIDE, private val quality: Int = JPEG_QUALITY) {
    private val longSide = longSide.coerceIn(MIN_LONG_SIDE, MAX_LONG_SIDE)
    private var lastThumbnailNs = 0L

    @Synchronized
    fun encode(picture: Bitmap, rotation: Int, nowNs: Long, shakySkipped: Int): CameraFrame {
        val source = if (picture.config == Bitmap.Config.HARDWARE) picture.copy(Bitmap.Config.ARGB_8888, false) else picture
        val scale = minOf(1f, longSide.toFloat() / max(source.width, source.height))
        val matrix = Matrix().apply {
            postScale(scale, scale)
            postRotate(rotation.toFloat())
        }
        val upright = Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true)
        val jpeg = ByteArrayOutputStream().use { out ->
            check(upright.compress(Bitmap.CompressFormat.JPEG, quality, out)) { "JPEG encoding failed" }
            out.toByteArray()
        }
        var thumbnail: Bitmap? = null
        if (nowNs - lastThumbnailNs >= THUMBNAIL_EVERY_NS) {
            lastThumbnailNs = nowNs
            val thumbScale = THUMB_SIDE.toFloat() / max(upright.width, upright.height)
            thumbnail = Bitmap.createScaledBitmap(upright,
                max(1, (upright.width * thumbScale).roundToInt()), max(1, (upright.height * thumbScale).roundToInt()), true)
        }
        return CameraFrame(jpeg, thumbnail, shakySkipped)
    }

    companion object {
        const val DEFAULT_LONG_SIDE = 960
        const val MIN_LONG_SIDE = 640
        const val MAX_LONG_SIDE = 1280
        const val JPEG_QUALITY = 80
        private const val THUMB_SIDE = 240
        private const val THUMBNAIL_EVERY_NS = 500_000_000L
    }
}
