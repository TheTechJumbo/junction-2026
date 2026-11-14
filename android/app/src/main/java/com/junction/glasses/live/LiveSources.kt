package com.junction.glasses.live

/** A camera the live stream and room recording can take frames from. */
internal interface FrameCamera<F> {
    suspend fun capture(): F
    fun stop()
}

internal enum class CameraSource(val label: String) { PHONE("Phone"), GLASSES("Glasses") }

/**
 * The selected camera for the live stream and room recording. A camera is created only when the
 * first frame is wanted, and only the selected one. When it fails (glasses disconnected, say) the
 * error reaches the caller; nothing ever switches to the other source by itself.
 */
internal class LiveSources<F>(initial: CameraSource, private val create: (CameraSource) -> FrameCamera<F>) {
    var selected: CameraSource = initial
        private set
    private var active: FrameCamera<F>? = null
    /** Whether a camera object exists (it is created by the first [capture]). */
    val started: Boolean get() = active != null

    /** Switches source; the old camera is stopped and the new one is created by the next [capture]. */
    fun select(source: CameraSource) {
        if (source == selected) return
        stop()
        selected = source
    }

    suspend fun capture(): F = (active ?: create(selected).also { active = it }).capture()

    fun stop() {
        active?.stop()
        active = null
    }
}
