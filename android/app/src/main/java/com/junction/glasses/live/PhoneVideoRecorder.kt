package com.junction.glasses.live

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.camera.core.CameraSelector
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FallbackStrategy
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.File
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * The phone's back camera recording a room video to a file with CameraX VideoCapture: FHD, falling
 * back to HD, at the camera's default frame rate, and no audio (audio is never enabled). Bound to the
 * Activity lifecycle; created only when "Record video" starts with the Phone source.
 */
internal class PhoneVideoRecorder(private val activity: ComponentActivity) {
    private var provider: ProcessCameraProvider? = null
    private var capture: VideoCapture<Recorder>? = null
    private var recording: Recording? = null

    /**
     * Starts recording into [file]. [onStatus] gets (elapsed ns, bytes) while recording; [onFinished]
     * gets the CameraX finalize error code (0 when none) once the file is complete. Both on Main.
     */
    @SuppressLint("MissingPermission") // CAMERA is checked here; audio is never enabled.
    suspend fun start(file: File, onStatus: (Long, Long) -> Unit, onFinished: (Int) -> Unit) {
        if (activity.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            throw CameraFailure("The phone camera needs the camera permission. Allow it, then record again.")
        }
        val cameras = cameraProvider(activity)
        val recorder = Recorder.Builder()
            .setQualitySelector(QualitySelector.fromOrderedList(listOf(Quality.FHD, Quality.HD), FallbackStrategy.lowerQualityOrHigherThan(Quality.HD)))
            .build()
        val videoCapture = VideoCapture.withOutput(recorder)
        cameras.bindToLifecycle(activity, CameraSelector.DEFAULT_BACK_CAMERA, videoCapture)
        provider = cameras
        capture = videoCapture
        recording = recorder.prepareRecording(activity, FileOutputOptions.Builder(file).build())
            .start(activity.mainExecutor) { event ->
                when (event) {
                    is VideoRecordEvent.Status -> onStatus(event.recordingStats.recordedDurationNanos, event.recordingStats.numBytesRecorded)
                    is VideoRecordEvent.Finalize -> {
                        release()
                        onFinished(event.error)
                    }
                    else -> Unit
                }
            }
    }

    /** Stops recording; the file is finalised and [start]'s onFinished follows. */
    fun stop() {
        recording?.stop()
        recording = null
    }

    fun release() {
        recording?.stop()
        recording = null
        capture?.let { provider?.unbind(it) }
        capture = null
        provider = null
    }
}

/** CameraX's process camera provider, awaited without blocking. */
internal suspend fun cameraProvider(activity: ComponentActivity): ProcessCameraProvider {
    val future = ProcessCameraProvider.getInstance(activity)
    return suspendCancellableCoroutine { cont ->
        future.addListener({
            try {
                val ready = future.get()
                if (cont.isActive) cont.resume(ready)
            } catch (e: Exception) {
                if (cont.isActive) cont.resumeWithException(e)
            }
        }, activity.mainExecutor)
    }
}
