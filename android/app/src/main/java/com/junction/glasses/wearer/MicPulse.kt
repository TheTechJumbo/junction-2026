package com.junction.glasses.wearer

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.os.SystemClock
import android.view.View

/**
 * Drawn behind the microphone button: a ring that bursts out when it is pressed, and, while the phone is
 * listening, rings rippling out with a glow that swells with the voice. [micRadius] is the button's radius.
 * With animations turned off in the phone's settings it shows one still ring instead.
 */
class MicPulse(context: Context) : View(context) {
    var color: Int = WearerPanel.AMBER
    var micRadius = 0f
    /** True while the phone's speech service is listening. */
    var listening = false
        private set
    private var since = 0L
    private var burstAt = -1L
    private var level = 0f
    private var target = 0f
    private val density = resources.displayMetrics.density
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }

    init { isClickable = false; importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO }

    /** One ring outwards: the press. */
    fun burst() { burstAt = SystemClock.uptimeMillis(); postInvalidateOnAnimation() }

    fun setListening(on: Boolean) {
        if (on == listening) return
        listening = on
        if (on) since = SystemClock.uptimeMillis() else { target = 0f }
        postInvalidateOnAnimation()
    }

    /** The speech service's loudness (dB, about -2 quiet to 10 loud). */
    fun setLevel(rmsDb: Float) { target = ((rmsDb + 2f) / 12f).coerceIn(0f, 1f) }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f; val cy = height / 2f
        val r0 = if (micRadius > 0f) micRadius else minOf(width, height) / 4f
        val reach = minOf(width, height) / 2f - 2f * density
        val now = SystemClock.uptimeMillis()
        val moving = ValueAnimator.areAnimatorsEnabled()
        level += (target - level) * 0.25f
        var again = false
        if (listening) {
            // The glow: a soft disc just outside the button, bigger when the voice is louder.
            glow.color = color; glow.alpha = (60 + 70 * level).toInt()
            canvas.drawCircle(cx, cy, r0 + (6f + 16f * level) * density, glow)
            ring.strokeWidth = 3f * density
            if (moving) {
                for (i in 0 until 3) {
                    val phase = (((now - since) / 1500f) + i / 3f) % 1f
                    ring.color = color; ring.alpha = ((1f - phase) * (150 + 100 * level)).toInt().coerceIn(0, 255)
                    canvas.drawCircle(cx, cy, r0 + (reach - r0) * phase, ring)
                }
                again = true
            } else {
                ring.color = color; ring.alpha = 200
                canvas.drawCircle(cx, cy, r0 + 10f * density, ring)
            }
        }
        val burst = (now - burstAt) / 550f
        if (burstAt >= 0 && burst < 1f && moving) {
            val eased = 1f - (1f - burst) * (1f - burst)
            ring.strokeWidth = (5f - 3f * burst) * density
            ring.color = color; ring.alpha = ((1f - burst) * 220).toInt()
            canvas.drawCircle(cx, cy, r0 + (reach - r0) * eased, ring)
            again = true
        }
        if (again || level > 0.01f) postInvalidateOnAnimation()
    }
}
