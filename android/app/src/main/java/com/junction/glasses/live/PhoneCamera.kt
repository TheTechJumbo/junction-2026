package com.junction.glasses.live

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Matrix
import android.util.Size
import androidx.activity.ComponentActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.max
import kotlin.math.roundToInt

/** One frame for the relay: an upright JPEG (long side at most 1280 px) and a small thumbnail. Memory only. */
internal class CameraFrame(val jpeg: ByteArray, val thumbnail: Bitmap)

/**
 * The phone's back camera through CameraX ImageCapture, bound to the Activity lifecycle. Created only
 * when the live camera is switched on. Frames stay in memory; nothing is written to storage.
 */
internal class PhoneCamera(private val activity: ComponentActivity) {
    private var provider: ProcessCameraProvider? = null
    private var useCase: ImageCapture? = null

    /** The newest frame: binds the camera on first use, then takes one picture. */
    suspend fun capture(): CameraFrame {
        val capture = useCase ?: bind()
        val image = suspendCancellableCoroutine<ImageProxy> { cont ->
            capture.takePicture(activity.mainExecutor, object : ImageCapture.OnImageCapturedCallback() {
                override fun onCaptureSuccess(image: ImageProxy) {
                    if (cont.isActive) cont.resume(image) else image.close()
                }
                override fun onError(exception: ImageCaptureException) {
                    if (cont.isActive) cont.resumeWithException(exception)
                }
            })
        }
        return withContext(Dispatchers.Default) { encode(image) }
    }

    fun stop() {
        val capture = useCase ?: return
        provider?.unbind(capture)
        useCase = null
        provider = null
    }

    // The caller only creates this class after the CAMERA permission is granted; checked again here.
    @SuppressLint("MissingPermission")
    private suspend fun bind(): ImageCapture {
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
        val capture = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
            .setJpegQuality(90)
            .setResolutionSelector(
                ResolutionSelector.Builder()
                    .setResolutionStrategy(ResolutionStrategy(Size(LONG_SIDE, LONG_SIDE * 3 / 4), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                    .build()
            )
            .build()
        cameraProvider.bindToLifecycle(activity, CameraSelector.DEFAULT_BACK_CAMERA, capture)
        provider = cameraProvider
        useCase = capture
        return capture
    }

    private fun encode(image: ImageProxy): CameraFrame = try {
        val full = image.toBitmap()
        val scale = minOf(1f, LONG_SIDE.toFloat() / max(full.width, full.height))
        val matrix = Matrix().apply {
            postScale(scale, scale)
            postRotate(image.imageInfo.rotationDegrees.toFloat())
        }
        val upright = Bitmap.createBitmap(full, 0, 0, full.width, full.height, matrix, true)
        val jpeg = ByteArrayOutputStream().use { out ->
            check(upright.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)) { "JPEG encoding failed" }
            out.toByteArray()
        }
        val thumbScale = THUMB_SIDE.toFloat() / max(upright.width, upright.height)
        val thumbnail = Bitmap.createScaledBitmap(upright,
            max(1, (upright.width * thumbScale).roundToInt()), max(1, (upright.height * thumbScale).roundToInt()), true)
        CameraFrame(jpeg, thumbnail)
    } finally {
        image.close()
    }

    companion object {
        const val LONG_SIDE = 1280
        const val JPEG_QUALITY = 85
        private const val THUMB_SIDE = 240
    }
}
