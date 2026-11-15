package com.junction.glasses.wearer

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.view.MotionEvent
import android.view.View
import com.junction.glasses.core.FloorPoint
import com.junction.glasses.core.NavPlace
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.sin

/**
 * The wearer's map, in the wearer screen's style: a bird's-eye view of the zone, north up. The room
 * is a faint scatter of floor points, places are labelled dots (the one being walked to in amber),
 * the wearer is a green dot with an arrow for the way they face, and while guiding the route is an
 * amber line ending in an arrow. Big and plain on purpose. Tap a place to be taken there; while
 * guiding, a Stop chip in the corner ends it.
 */
class WearerMap(context: Context) : View(context) {
    var onPlaceTap: (String) -> Unit = {}
    var onStop: () -> Unit = {}

    private var plan: List<FloorPoint> = emptyList()
    private var places: List<NavPlace> = emptyList()
    private var wearer: FloorPoint? = null
    private var headingDeg: Double? = null
    private var route: List<FloorPoint> = emptyList()
    private var target: String? = null
    /** A thing found by "find me X": drawn as an amber ring, not a place to be walked to yet. */
    private var found: String? = null
    private var note: String = "No map yet"

    private val density = resources.displayMetrics.density
    private val scaled = resources.displayMetrics.scaledDensity
    private val box = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = MAP_BACK }
    private val floor = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.SOFT; alpha = 70 }
    private val dot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.SOFT }
    private val goal = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.AMBER }
    private val me = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.GREEN }
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = WearerPanel.AMBER; style = Paint.Style.STROKE; strokeWidth = 5 * density; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND
    }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.SOFT; textSize = 17 * scaled }
    private val goalLabel = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.AMBER; textSize = 20 * scaled; isFakeBoldText = true }
    private val quiet = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.SOFT; textSize = 18 * scaled; textAlign = Paint.Align.CENTER }
    private val chip = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.AMBER }
    private val chipText = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.NIGHT; textSize = 18 * scaled; isFakeBoldText = true; textAlign = Paint.Align.CENTER }
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WearerPanel.AMBER; style = Paint.Style.STROKE; strokeWidth = 4 * density }
    private val stopChip = RectF()

    /** Where everything is drawn: screen position of each place, for taps. */
    private val placeAt = mutableMapOf<String, Pair<Float, Float>>()

    init {
        contentDescription = "Map of the room"
    }

    fun show(
        plan: List<FloorPoint>, places: List<NavPlace>, wearer: FloorPoint?, headingDeg: Double?,
        route: List<FloorPoint>, target: String?, note: String?, found: String? = null,
    ) {
        this.plan = plan; this.places = places; this.wearer = wearer; this.headingDeg = headingDeg
        this.route = route; this.target = target; this.found = found
        this.note = note ?: if (places.isEmpty() && wearer == null) "No map yet" else ""
        contentDescription = listOfNotNull(
            "Map of the room", target?.let { "Going to $it" }, found?.let { "Found: $it" }, if (places.isNotEmpty()) "Places: " + places.joinToString { it.name } else null,
        ).joinToString(". ")
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat(); val h = height.toFloat()
        val corner = 22 * density
        canvas.drawRoundRect(0f, 0f, w, h, corner, corner, box)
        placeAt.clear()
        val all = plan + places.map { it.at } + listOfNotNull(wearer) + route
        if (all.isEmpty()) {
            canvas.drawText(note.ifEmpty { "No map yet" }, w / 2, h / 2, quiet)
            return
        }
        // Fit everything, north up, with room for labels; at least 4 m across so a close-up is not huge.
        val pad = 36 * density
        var minE = all.minOf { it.east }; var maxE = all.maxOf { it.east }
        var minN = all.minOf { it.north }; var maxN = all.maxOf { it.north }
        val spanE = max(maxE - minE, MIN_SPAN_M); val spanN = max(maxN - minN, MIN_SPAN_M)
        val midE = (minE + maxE) / 2; val midN = (minN + maxN) / 2
        minE = midE - spanE / 2; maxE = midE + spanE / 2; minN = midN - spanN / 2; maxN = midN + spanN / 2
        val scale = minOf((w - 2 * pad) / spanE, (h - 2 * pad) / spanN).toFloat()
        val offX = (w - spanE.toFloat() * scale) / 2; val offY = (h - spanN.toFloat() * scale) / 2
        fun x(p: FloorPoint) = offX + ((p.east - minE) * scale).toFloat()
        fun y(p: FloorPoint) = offY + ((maxN - p.north) * scale).toFloat()

        for (p in plan) canvas.drawCircle(x(p), y(p), 2.2f * density, floor)
        if (route.size >= 2) {
            val path = Path().apply { moveTo(x(route[0]), y(route[0])); for (p in route.drop(1)) lineTo(x(p), y(p)) }
            canvas.drawPath(path, line)
            val a = route[route.size - 2]; val b = route.last()
            arrowHead(canvas, x(a), y(a), x(b), y(b))
        }
        for (place in places) {
            val px = x(place.at); val py = y(place.at)
            val isGoal = place.name.equals(target, ignoreCase = true)
            val isFound = !isGoal && place.name.equals(found, ignoreCase = true)
            if (isFound) {
                canvas.drawCircle(px, py, 13f * density, ring)
                canvas.drawCircle(px, py, 5f * density, goal)
            } else canvas.drawCircle(px, py, (if (isGoal) 11f else 7f) * density, if (isGoal) goal else dot)
            val text = if (isGoal || isFound) goalLabel else label
            val tw = text.measureText(place.name)
            val tx = (px + 10 * density).coerceAtMost(w - tw - 8 * density).coerceAtLeast(8 * density)
            canvas.drawText(place.name, tx, py - 10 * density, text)
            placeAt[place.name] = px to py
        }
        wearer?.let { p ->
            val px = x(p); val py = y(p)
            headingDeg?.let { heading ->
                val r = Math.toRadians(heading)
                val tip = 26 * density
                arrowHead(canvas, px, py, px + (sin(r) * tip).toFloat(), py - (cos(r) * tip).toFloat(), me)
            }
            canvas.drawCircle(px, py, 10 * density, me)
        }
        if (target != null) {
            val cw = 86 * density; val ch = 40 * density; val m = 10 * density
            stopChip.set(w - cw - m, m, w - m, m + ch)
            canvas.drawRoundRect(stopChip, ch / 2, ch / 2, chip)
            canvas.drawText("Stop", stopChip.centerX(), stopChip.centerY() + chipText.textSize / 3, chipText)
        } else stopChip.setEmpty()
        if (note.isNotEmpty()) canvas.drawText(note, w / 2, h - 14 * density, quiet)
    }

    private fun arrowHead(canvas: Canvas, fromX: Float, fromY: Float, toX: Float, toY: Float, paint: Paint = goal) {
        val len = hypot(toX - fromX, toY - fromY)
        if (len < 1f) return
        val ux = (toX - fromX) / len; val uy = (toY - fromY) / len
        val size = 14 * density
        val path = Path().apply {
            moveTo(toX + ux * size * 0.6f, toY + uy * size * 0.6f)
            lineTo(toX - ux * size + -uy * size * 0.7f, toY - uy * size + ux * size * 0.7f)
            lineTo(toX - ux * size - -uy * size * 0.7f, toY - uy * size - ux * size * 0.7f)
            close()
        }
        canvas.drawPath(path, paint)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.action == MotionEvent.ACTION_DOWN) return true
        if (event.action != MotionEvent.ACTION_UP) return super.onTouchEvent(event)
        if (!stopChip.isEmpty && stopChip.contains(event.x, event.y)) { onStop(); return true }
        val near = placeAt.minByOrNull { (_, at) -> hypot(at.first - event.x, at.second - event.y) }
        if (near != null && hypot(near.value.first - event.x, near.value.second - event.y) <= TAP_RADIUS_DP * density) {
            onPlaceTap(near.key)
        }
        return true
    }

    companion object {
        val MAP_BACK = android.graphics.Color.rgb(24, 36, 66)
        private const val MIN_SPAN_M = 4.0
        private const val TAP_RADIUS_DP = 40
    }
}
