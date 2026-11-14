package com.junction.glasses.live

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.SystemClock
import android.util.Size
import androidx.activity.ComponentActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * One frame for the relay: an upright JPEG and, at most twice a second, a small thumbnail for the
 * screen. [shakySkipped] counts frames skipped so far because the phone was moving fast. Memory only.
 */
internal class CameraFrame(val jpeg: ByteArray, val thumbnail: Bitmap?, val shakySkipped: Int)

/**
 * The phone's back camera as a CameraX ImageAnalysis stream (keep only the latest frame), bound to
 * the Activity lifecycle. Created only when the live camera is switched on. A frame is encoded only
 * when the send loop asks for one, so the photo sent is always the newest; while the gyroscope shows
 * fast turning, frames are skipped for up to 1 s. Frames stay in memory; nothing is written to storage.
 */
internal class PhoneCamera(private val activity: ComponentActivity, longSide: Int = DEFAULT_LONG_SIDE) {
    private val longSide = longSide.coerceIn(MIN_LONG_SIDE, MAX_LONG_SIDE)
    private val slot = FrameSlot<CameraFrame>(SystemClock::elapsedRealtimeNanos)
    private val steady = SteadyGate()
    private val picker = FramePicker(slot, steady)
    private var lastThumbnailNs = 0L // analyzer thread only
    private var provider: ProcessCameraProvider? = null
    private var analysis: ImageAnalysis? = null
    private var analyzerThread: ExecutorService? = null
    private val sensors: SensorManager? = activity.getSystemService(SensorManager::class.java)
    private val gyroscope = object : SensorEventListener {
        override fun onSensorChanged(event: SensorEvent?) {
            val values = event?.values ?: return
            if (values.size >= 3) steady.record(SystemClock.elapsedRealtimeNanos(), values[0], values[1], values[2])
        }
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    }

    /** The next frame the camera delivers after this call (binds the camera on first use). */
    suspend fun capture(): CameraFrame {
        if (analysis == null) bind()
        return withTimeoutOrNull(FRAME_TIMEOUT_MS) { slot.take() }
            ?: throw CameraFailure("No camera frame for 5 s. Is another app using the camera?")
    }

    fun stop() {
        sensors?.unregisterListener(gyroscope)
        analysis?.let { stream ->
            stream.clearAnalyzer()
            provider?.unbind(stream)
        }
        analysis = null
        provider = null
        analyzerThread?.shutdown()
        analyzerThread = null
        slot.fail(CameraFailure("Live camera stopped."))
    }

    // The caller only creates this class after the CAMERA permission is granted; checked again here.
    @SuppressLint("MissingPermission")
    private suspend fun bind() {
        check(activity.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) { "Camera permission is needed" }
        val future = ProcessCameraProvider.getInstance(activity)
        val cameraProvider = suspendCancellableCoroutine<ProcessCameraProvider> { cont ->
            future.addListener({
                try {
                    val ready = future.get()
                    if (cont.isActive) cont.resume(ready)
                } catch (e: Exception) {
                    if (cont.isActive) cont.resumeWithException(e)
                }
            }, activity.mainExecutor)
        }
        val thread = Executors.newSingleThreadExecutor()
        val stream = ImageAnalysis.Builder()
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
            .setResolutionSelector(
                ResolutionSelector.Builder()
                    .setResolutionStrategy(ResolutionStrategy(Size(1280, 960), ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER))
                    .build()
            )
            .build()
        stream.setAnalyzer(thread) { image -> analyze(image) }
        cameraProvider.bindToLifecycle(activity, CameraSelector.DEFAULT_BACK_CAMERA, stream)
        val manager = sensors
        val gyro = manager?.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
        if (manager != null && gyro != null) manager.registerListener(gyroscope, gyro, SensorManager.SENSOR_DELAY_GAME)
        provider = cameraProvider
        analysis = stream
        analyzerThread = thread
    }

    /** Analyzer thread. Every ImageProxy is closed here; unwanted or shaky frames right away. */
    private fun analyze(image: ImageProxy) {
        val now = SystemClock.elapsedRealtimeNanos()
        if (!picker.wants(now)) { image.close(); return }
        val rotation = image.imageInfo.rotationDegrees
        val picture = try { readBitmap(image) } catch (_: Exception) { null } finally { image.close() }
        if (picture == null) { slot.fail(CameraFailure("A camera frame could not be read.")); return }
        try {
            slot.offer(encode(picture, rotation, now))
        } catch (_: Exception) {
            slot.fail(CameraFailure("A camera frame could not be encoded."))
        }
    }

    /** YUV to Bitmap with CameraX's converter; if that is unavailable, through NV21 and YuvImage. */
    private fun readBitmap(image: ImageProxy): Bitmap = try {
        image.toBitmap()
    } catch (_: Exception) {
        val jpeg = ByteArrayOutputStream().use { out ->
            check(YuvImage(nv21(image), ImageFormat.NV21, image.width, image.height, null)
                .compressToJpeg(Rect(0, 0, image.width, image.height), 90, out)) { "YUV to JPEG failed" }
            out.toByteArray()
        }
        checkNotNull(BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)) { "JPEG decode failed" }
    }

    private fun nv21(image: ImageProxy): ByteArray {
        val width = image.width
        val height = image.height
        val (yPlane, uPlane, vPlane) = image.planes
        val out = ByteArray(width * height + 2 * (width / 2) * (height / 2))
        var at = 0
        val luma = yPlane.buffer
        for (row in 0 until height) {
            luma.position(row * yPlane.rowStride)
            luma.get(out, at, width)
            at += width
        }
        val u = uPlane.buffer
        val v = vPlane.buffer
        for (row in 0 until height / 2) {
            for (col in 0 until width / 2) {
                out[at++] = v.get(row * vPlane.rowStride + col * vPlane.pixelStride)
                out[at++] = u.get(row * uPlane.rowStride + col * uPlane.pixelStride)
            }
        }
        return out
    }

    /** Upright, long side [longSide] at most, JPEG quality 80; a thumbnail at most twice a second. */
    private fun encode(picture: Bitmap, rotation: Int, nowNs: Long): CameraFrame {
        val scale = minOf(1f, longSide.toFloat() / max(picture.width, picture.height))
        val matrix = Matrix().apply {
            postScale(scale, scale)
            postRotate(rotation.toFloat())
        }
        val upright = Bitmap.createBitmap(picture, 0, 0, picture.width, picture.height, matrix, true)
        val jpeg = ByteArrayOutputStream().use { out ->
            check(upright.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)) { "JPEG encoding failed" }
            out.toByteArray()
        }
        var thumbnail: Bitmap? = null
        if (nowNs - lastThumbnailNs >= THUMBNAIL_EVERY_NS) {
            lastThumbnailNs = nowNs
            val thumbScale = THUMB_SIDE.toFloat() / max(upright.width, upright.height)
            thumbnail = Bitmap.createScaledBitmap(upright,
                max(1, (upright.width * thumbScale).roundToInt()), max(1, (upright.height * thumbScale).roundToInt()), true)
        }
        return CameraFrame(jpeg, thumbnail, picker.shakySkipped)
    }

    companion object {
        const val DEFAULT_LONG_SIDE = 960
        const val MIN_LONG_SIDE = 640
        const val MAX_LONG_SIDE = 1280
        const val JPEG_QUALITY = 80
        private const val THUMB_SIDE = 240
        private const val THUMBNAIL_EVERY_NS = 500_000_000L
        private const val FRAME_TIMEOUT_MS = 5_000L
    }
}
