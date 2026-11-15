package com.junction.glasses.wearer

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.view.View
import androidx.core.graphics.PathParser
import com.junction.glasses.core.Glyph

/**
 * Line drawings for the wearer's symbols, on a 24-unit grid with rounded strokes. One set of drawings
 * serves the phone (as views) and the glasses (as bitmaps, where Meta's display has no native icon).
 */
object Glyphs {
    private val DATA = mapOf(
        Glyph.PILL_BOX to "M3 5h18v4H3z M4.5 9h15v10.5a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1z M10 14h4",
        Glyph.DOOR to "M6 21V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17 M3.5 21h17 M14.5 12.5v.01",
        Glyph.KEY to "M7.5 16a4 4 0 1 1 0-8a4 4 0 0 1 0 8z M11.5 12h9.5 M18 12v3.5 M21 12v2.5",
        Glyph.WALLET to "M3 8h16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z M3 8l12-4v4 M16.5 14v.01",
        Glyph.PHONE to "M8 2.5h8a1 1 0 0 1 1 1v17a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1v-17a1 1 0 0 1 1-1z M11 18h2",
        Glyph.GLASSES to "M2 13a4 4 0 1 0 8 0a4 4 0 1 0-8 0z M14 13a4 4 0 1 0 8 0a4 4 0 1 0-8 0z M10 13h4 M2 13L4 7 M22 13l-2-6",
        Glyph.LOCK to "M5.5 11h13v10h-13z M8 11V7.5a4 4 0 0 1 8 0V11 M12 15v2",
        Glyph.HOUSE to "M3 11.5L12 4l9 7.5 M5.5 9.5V20.5h13V9.5 M10 20.5v-6h4v6",
        Glyph.MOON to "M20 14.5A8.5 8.5 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",
        Glyph.BED to "M3 19V6 M3 15h18v4 M3 12h7V9a1 1 0 0 1 1-1h7.5a2.5 2.5 0 0 1 2.5 2.5V15",
        Glyph.CHECK to "M4.5 12.5l5 5L19.5 7",
        Glyph.CROSS to "M6 6l12 12 M18 6L6 18",
        Glyph.QUESTION to "M9 9a3 3 0 1 1 4.2 2.75c-.8.35-1.2 1-1.2 1.85V15 M12 19v.01",
        Glyph.BELL to "M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z M10 21h4",
        Glyph.EYE to "M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z M12 15a3 3 0 1 0 0-6a3 3 0 0 0 0 6z",
        Glyph.CLOCK to "M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18z M12 7v5l3.5 2",
        Glyph.PLACE to "M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z M12 12.5a2.5 2.5 0 1 0 0-5a2.5 2.5 0 0 0 0 5z",
    )
    /** Straight ahead; rotate it by the bearing. */
    private const val ARROW = "M12 21V4 M5 10.5L12 3.5l7 7"
    const val MIC = "M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z M5.5 11a6.5 6.5 0 0 0 13 0 M12 17.5V21"
    const val GEAR = "M12 15a3 3 0 1 0 0-6a3 3 0 0 0 0 6z M12 2.5v3 M12 18.5v3 M2.5 12h3 M18.5 12h3 M5.3 5.3l2.1 2.1 M16.6 16.6l2.1 2.1 M5.3 18.7l2.1-2.1 M16.6 7.4l2.1-2.1"

    private val cache = HashMap<String, Path>()
    fun path(data: String): Path = cache.getOrPut(data) { PathParser.createPathFromPathData(data) }
    fun dataFor(glyph: Glyph): String = DATA.getValue(glyph)
    val arrowData: String get() = ARROW

    /** Draws [data] centred in [size] px, rotated by [degrees], in [color]. */
    fun draw(canvas: Canvas, data: String, size: Float, color: Int, degrees: Float = 0f, stroke: Float = 2f) {
        val scale = size / 24f
        val m = Matrix().apply { postRotate(degrees, 12f, 12f); postScale(scale, scale) }
        val p = Path(path(data)).apply { transform(m) }
        canvas.drawPath(p, Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE; strokeWidth = stroke * scale; strokeCap = Paint.Cap.ROUND
            strokeJoin = Paint.Join.ROUND; this.color = color
        })
    }

    /** A bitmap for the glasses display: white on transparent (black is "off" on see-through lenses). */
    fun bitmap(data: String, px: Int, degrees: Float = 0f, color: Int = 0xFFFFFFFF.toInt(), stroke: Float = 2.4f): Bitmap =
        Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888).also { draw(Canvas(it), data, px.toFloat(), color, degrees, stroke) }
}

/** A symbol as a view. [degrees] turns it (for the direction arrow). */
class GlyphView(context: Context) : View(context) {
    var data: String = Glyphs.dataFor(Glyph.CLOCK); set(value) { field = value; invalidate() }
    var color: Int = 0xFFFFFFFF.toInt(); set(value) { field = value; invalidate() }
    var degrees: Float = 0f; set(value) { field = value; invalidate() }
    var stroke: Float = 2f; set(value) { field = value; invalidate() }
    override fun onDraw(canvas: Canvas) {
        val side = minOf(width - paddingLeft - paddingRight, height - paddingTop - paddingBottom).toFloat()
        canvas.save(); canvas.translate(paddingLeft + (width - paddingLeft - paddingRight - side) / 2f, paddingTop + (height - paddingTop - paddingBottom - side) / 2f)
        Glyphs.draw(canvas, data, side, color, degrees, stroke); canvas.restore()
    }
}
