package com.junction.glasses.live

import android.os.SystemClock
import com.junction.glasses.meta.MetaGlassesAdapter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.yield

/**
 * The glasses camera as a live source, through the existing glasses session. Starting it uses the
 * adapter's own startCamera, which connects on demand (ensureStarted) and asks for the glasses camera
 * consent first. Frames are one photo per request from the running stream (DAT 1.0.1 exposes photo
 * capture), encoded like the phone's. While it streams the session is never idle. When the glasses
 * are not connected this fails with a message; it never switches to the phone camera.
 */
internal class GlassesCamera(
    private val glasses: MetaGlassesAdapter,
    private val encoder: FrameEncoder = FrameEncoder(),
) : FrameCamera<CameraFrame> {
    override suspend fun capture(): CameraFrame {
        if (glasses.state.value.camera != STREAMING) start()
        val picture = glasses.captureLiveBitmap() ?: throw CameraFailure(notReady())
        return withContext(Dispatchers.Default) { encoder.encode(picture, 0, SystemClock.elapsedRealtimeNanos(), 0) }
    }

    override fun stop() { glasses.stopCamera() }

    private suspend fun start() {
        glasses.startCamera()
        yield() // let startCamera begin connecting before the state is read
        val ready = withTimeoutOrNull(START_TIMEOUT_MS) {
            glasses.state.first { it.camera == STREAMING || it.connection == "DISCONNECTED" }
        }
        if (ready?.camera != STREAMING) throw CameraFailure(notReady())
    }

    private fun notReady(): String =
        if (glasses.state.value.connection != "STARTED") "Glasses not connected. Connect them on the glasses card; the live camera does not switch to the phone."
        else "Glasses camera not streaming. Check the glasses camera consent in Meta AI and try again."

    companion object {
        private const val STREAMING = "STREAMING"
        private const val START_TIMEOUT_MS = 45_000L
    }
}
