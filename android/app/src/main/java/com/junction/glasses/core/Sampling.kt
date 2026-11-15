package com.junction.glasses.core

/**
 * The wearer screen's automatic position sampling: which camera it uses and the one line it shows.
 * Pure, so JVM tests cover the choices; the Activity runs the camera and the relay calls.
 */
object Sampling {
    enum class Camera(val word: String) { GLASSES("glasses"), PHONE("phone") }

    /**
     * The glasses while their session is up (their camera starts on demand and restarts after an idle
     * drop), else the phone camera when it is allowed; null when neither can be used yet.
     */
    fun camera(glassesConnection: String, phoneAllowed: Boolean): Camera? = when {
        glassesConnection == "STARTED" -> Camera.GLASSES
        phoneAllowed -> Camera.PHONE
        else -> null
    }

    data class Inputs(
        val keySet: Boolean,
        /** The camera the running loop uses, or null when the loop is not running. */
        val camera: Camera?,
        val glassesConnected: Boolean,
        /** The loop's last error (null after a reply). */
        val error: String? = null,
        val found: Boolean = false,
        /** Seconds since the relay last placed the wearer, or null before the first fix. */
        val lastFixAgoS: Long? = null,
        /** Why the loop is not running, when it is not ("Stopped in setup"…). */
        val idle: String? = null,
    )

    /** "Finding you · glasses · last fix 2 s ago", or the real problem first. */
    fun line(i: Inputs): String {
        if (!i.keySet) return "Can't find you: no relay key"
        val camera = i.camera ?: return i.idle ?: "Can't find you: connect the glasses or allow the phone camera"
        if (i.error != null && i.error.startsWith("Relay unreachable")) return "Can't find you: relay not reachable · retrying"
        val start = if (i.found) "Found you" else "Finding you"
        val via = if (camera == Camera.PHONE && !i.glassesConnected) "glasses not streaming, using phone camera" else camera.word
        val fix = i.lastFixAgoS?.let { "last fix ${ago(it)}" } ?: "no fix yet"
        val problem = i.error?.let { " · " + it.substringBefore(". ").take(60) } ?: ""
        return "$start · $via · $fix$problem"
    }

    fun ago(seconds: Long): String = when {
        seconds < 60 -> "$seconds s ago"
        seconds < 3600 -> "${seconds / 60} min ago"
        else -> "${seconds / 3600} h ago"
    }
}
