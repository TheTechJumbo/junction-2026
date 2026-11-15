package com.junction.glasses.wearer

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import com.junction.glasses.core.Card
import com.junction.glasses.core.Glyph
import com.junction.glasses.core.WearerBrain
import com.junction.glasses.core.Way
import java.time.LocalTime

/**
 * The wearer's phone screen: one big card (the same one the glasses show), three one-tap questions and a
 * microphone. Symbols carry the meaning; words are one or two at a time. A preview of the glasses card
 * can be shown underneath for demos and setup.
 */
class WearerPanel(context: Context) : LinearLayout(context) {
    var onAsk: (String) -> Unit = {}
    var onMic: () -> Unit = {}
    var onDone: (String) -> Unit = {}
    var onDismiss: () -> Unit = {}
    /** Start walking the wearer to a place or item (its relay name). */
    var onGo: (String) -> Unit = {}
    var onStopGuide: () -> Unit = {}
    var onSetup: () -> Unit = {}

    private val clock = text("--:--", 22f, AMBER, bold = true).apply { gravity = Gravity.START or Gravity.CENTER_VERTICAL }
    private val cardArea = FrameLayout(context)
    private val glassesFrame = FrameLayout(context).apply {
        background = GradientDrawable().apply { setColor(Color.BLACK); cornerRadius = dp(22).toFloat() }
        setPadding(dp(18), dp(18), dp(18), dp(18)); visibility = GONE
        contentDescription = "Glasses preview"
    }
    private val status = text("", 15f, SOFT)
    private var last: Card? = null

    init {
        orientation = VERTICAL
        setBackgroundColor(NIGHT)
        setPadding(dp(20), dp(16), dp(20), dp(20))
        // Top row: clock, then glasses preview and setup toggles.
        addView(LinearLayout(context).apply {
            gravity = Gravity.CENTER_VERTICAL
            addView(clock, LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            addView(iconButton(Glyphs.dataFor(Glyph.GLASSES), "Show the glasses view") {
                glassesFrame.visibility = if (glassesFrame.visibility == VISIBLE) GONE else VISIBLE
            })
            addView(iconButton(Glyphs.GEAR, "Setup") { onSetup() })
        })
        addView(cardArea, LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(380)).apply { topMargin = dp(8) })
        addView(glassesFrame, LayoutParams(dp(300), dp(300)).apply { gravity = Gravity.CENTER_HORIZONTAL; topMargin = dp(8) })
        addView(status, LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(4) })
        // One-tap questions: the three the person asks most.
        addView(LinearLayout(context).apply {
            addView(askTile(Glyph.PILL_BOX, "Pills?", "Did I take my pills?"), tileParams())
            addView(askTile(Glyph.DOOR, "Door?", "Did I lock the door?"), tileParams())
            addView(askTile(Glyph.KEY, "Keys?", "Where are my keys?"), tileParams())
        }, LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(112)).apply { topMargin = dp(12) })
        addView(FrameLayout(context).apply {
            addView(GlyphView(context).apply {
                data = Glyphs.MIC; color = NIGHT; stroke = 2.2f
                background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(AMBER) }
                setPadding(dp(22), dp(22), dp(22), dp(22))
                contentDescription = "Ask with your voice"; isClickable = true; setOnClickListener { onMic() }
            }, FrameLayout.LayoutParams(dp(88), dp(88), Gravity.CENTER))
        }, LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(104)).apply { topMargin = dp(12) })
    }

    fun setClock(now: LocalTime) { clock.text = "%02d:%02d".format(now.hour, now.minute) }
    fun setStatus(message: String) { status.text = message }

    fun show(card: Card) {
        if (card == last) return
        last = card
        clock.visibility = if (card is Card.Idle) INVISIBLE else VISIBLE // the idle card is the clock
        cardArea.removeAllViews(); cardArea.addView(build(card, glasses = false), FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        glassesFrame.removeAllViews(); glassesFrame.addView(build(card, glasses = true))
    }

    /** Builds a card. The glasses version is smaller, white and amber on black, with no buttons. */
    private fun build(card: Card, glasses: Boolean): View {
        val k = if (glasses) 0.55f else 1f
        val fg = if (glasses) Color.WHITE else SNOW
        val col = LinearLayout(context).apply { orientation = VERTICAL; gravity = Gravity.CENTER }
        fun glyph(data: String, size: Int, color: Int = fg, degrees: Float = 0f) = GlyphView(context).apply {
            this.data = data; this.color = color; this.degrees = degrees; stroke = 2.2f
        }.also { col.addView(it, LayoutParams((size * k).toDp(), (size * k).toDp()).apply { gravity = Gravity.CENTER_HORIZONTAL }) }
        fun row(vararg views: View) = LinearLayout(context).apply {
            gravity = Gravity.CENTER; views.forEach { addView(it) }
        }.also { col.addView(it, LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.CENTER_HORIZONTAL; topMargin = (10 * k).toDp() }) }
        fun small(data: String, size: Int = 40, color: Int = fg, degrees: Float = 0f) = GlyphView(context).apply {
            this.data = data; this.color = color; this.degrees = degrees; stroke = 2.2f
            layoutParams = LayoutParams((size * k).toDp(), (size * k).toDp()).apply { marginEnd = (10 * k).toDp() }
        }
        fun words(value: String, sp: Float, color: Int = fg, bold: Boolean = false) = text(value, sp * k, color, bold)
        fun turns(w: Way) = if (w.turns > 0) words("  ${w.turns} ${if (w.turns == 1) "turn" else "turns"}", 22f, SOFT) else null
        fun way(w: Way?) { if (w != null) row(*listOfNotNull(small(Glyphs.arrowData, 56, AMBER, w.bearingDeg.toFloat()), words(w.metres, 30f, AMBER, true), turns(w)).toTypedArray()) }
        // Phone only: the buttons under a card (Done, Go, Stop, Yes).
        fun buttons(vararg views: View?) {
            if (glasses) return
            val shown = views.filterNotNull(); if (shown.isEmpty()) return
            col.addView(LinearLayout(context).apply {
                gravity = Gravity.CENTER
                shown.forEach { addView(it, LayoutParams(dp(150), dp(64)).apply { marginStart = dp(6); marginEnd = dp(6) }) }
            }, LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.CENTER_HORIZONTAL; topMargin = dp(14) })
        }
        fun go(target: String?, w: Way?) = if (target != null && w != null) pill(Glyphs.arrowData, "Go") { onGo(target) } else null
        fun time(t: LocalTime?) = if (t == null) "–" else "%02d:%02d".format(t.hour, t.minute)

        when (card) {
            is Card.Idle -> {
                col.addView(words(time(card.now), 84f, fg, true))
                card.next?.let { r -> row(small(Glyphs.dataFor(Glyph.BELL), 36, SOFT), words(r.time, 28f, SOFT), small(Glyphs.dataFor(WearerBrain.glyphFor(r.place ?: r.text)), 36, SOFT).apply { (layoutParams as LayoutParams).marginStart = (10 * k).toDp() }) }
            }
            is Card.Due -> {
                row(small(Glyphs.dataFor(Glyph.BELL), 34, AMBER), words(card.reminder.time, 26f, AMBER, true))
                glyph(Glyphs.dataFor(card.glyph), 132)
                col.addView(words(label(card.glyph, card.reminder.text), 40f, fg, true))
                way(card.way)
                buttons(go(card.target, card.way), pill(Glyphs.dataFor(Glyph.CHECK), "Done") { onDone(card.reminder.id) })
            }
            is Card.Checked -> {
                val mark = when (card.ok) { true -> Glyph.CHECK; false -> Glyph.CROSS; null -> Glyph.QUESTION }
                val markColor = when (card.ok) { true -> GREEN; false -> RED; null -> AMBER }
                row(small(Glyphs.dataFor(card.glyph), 120), small(Glyphs.dataFor(mark), 96, markColor))
                card.at?.let { col.addView(words(time(it), 64f, fg, true)) }
                way(card.way)
                buttons(go(card.target, card.way))
            }
            is Card.GoingOut -> {
                row(small(Glyphs.dataFor(Glyph.HOUSE), 60, AMBER), words("Going out?", 34f, fg, true))
                for (item in card.items) row(small(Glyphs.dataFor(item.glyph), 52), words(item.near ?: "?", 26f, if (item.near == null) SOFT else fg))
                row(small(Glyphs.dataFor(Glyph.LOCK), 52, AMBER), words("Lock", 26f, AMBER))
            }
            is Card.Night -> {
                glyph(Glyphs.dataFor(Glyph.MOON), 110, AMBER)
                col.addView(words(time(card.at), 56f, fg, true))
                row(small(Glyphs.dataFor(Glyph.BED), 60), words("Bed?", 36f, fg, true))
                way(card.way)
                // Yes walks her to bed when the relay knows the way; otherwise it just quietens the card.
                buttons(pill(Glyphs.dataFor(Glyph.BED), "Yes") { if (card.way != null) onGo("bed") else onDismiss() })
            }
            is Card.Guide -> {
                val w = card.way
                when {
                    card.arrived -> { glyph(Glyphs.dataFor(Glyph.CHECK), 150, GREEN); row(small(Glyphs.dataFor(card.glyph), 64), words("Here", 44f, fg, true)) }
                    w == null -> { glyph(Glyphs.dataFor(Glyph.EYE), 130, SOFT); row(small(Glyphs.dataFor(card.glyph), 64), words("…", 44f, SOFT, true)) }
                    else -> {
                        glyph(Glyphs.arrowData, 170, AMBER, w.bearingDeg.toFloat())
                        row(*listOfNotNull(small(Glyphs.dataFor(card.glyph), 64), words(w.metres, 44f, fg, true), turns(w)).toTypedArray())
                    }
                }
                buttons(pill(Glyphs.dataFor(Glyph.CROSS), "Stop") { onStopGuide() })
            }
            is Card.Found -> {
                glyph(Glyphs.dataFor(card.glyph), 110)
                col.addView(words(card.near ?: "Seen", 40f, fg, true))
                card.time?.let { row(small(Glyphs.dataFor(Glyph.CLOCK), 32, SOFT), words(it, 26f, SOFT)) }
                way(card.way)
                buttons(go(card.target, card.way))
            }
            is Card.Said -> col.addView(words(card.text, 30f, fg).apply { gravity = Gravity.CENTER })
        }
        return col
    }

    private fun label(glyph: Glyph, fallback: String) = when (glyph) {
        Glyph.PILL_BOX -> "Pills"; Glyph.DOOR -> "Door"; Glyph.KEY -> "Keys"; Glyph.WALLET -> "Wallet"
        Glyph.PHONE -> "Phone"; Glyph.GLASSES -> "Glasses"; else -> WearerBrain.shorten(fallback)
    }

    private fun askTile(glyph: Glyph, word: String, question: String) = LinearLayout(context).apply {
        orientation = VERTICAL; gravity = Gravity.CENTER
        background = GradientDrawable().apply { setColor(PANEL); cornerRadius = dp(18).toFloat() }
        isClickable = true; contentDescription = question; setOnClickListener { onAsk(question) }
        addView(GlyphView(context).apply { data = Glyphs.dataFor(glyph); color = SNOW; stroke = 2.2f }, LayoutParams(dp(48), dp(48)))
        addView(text(word, 18f, SNOW, bold = true).apply { gravity = Gravity.CENTER })
    }
    private fun pill(data: String, word: String, action: () -> Unit) = LinearLayout(context).apply {
        gravity = Gravity.CENTER; isClickable = true; contentDescription = word; setOnClickListener { action() }
        background = GradientDrawable().apply { setColor(AMBER); cornerRadius = dp(32).toFloat() }
        addView(GlyphView(context).apply { this.data = data; color = NIGHT; stroke = 2.6f }, LayoutParams(dp(30), dp(30)).apply { marginEnd = dp(8) })
        addView(text(word, 22f, NIGHT, bold = true))
    }
    private fun iconButton(data: String, description: String, action: () -> Unit) = GlyphView(context).apply {
        this.data = data; color = SOFT; stroke = 1.8f; contentDescription = description
        isClickable = true; setOnClickListener { action() }; setPadding(dp(10), dp(10), dp(10), dp(10))
        layoutParams = LayoutParams(dp(48), dp(48))
    }
    private fun tileParams() = LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f).apply { marginStart = dp(5); marginEnd = dp(5) }
    private fun text(value: String, sp: Float, color: Int, bold: Boolean = false) = TextView(context).apply {
        text = value; setTextColor(color); setTextSize(TypedValue.COMPLEX_UNIT_SP, sp); gravity = Gravity.CENTER
        if (bold) typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        includeFontPadding = false
    }
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun Float.toDp() = (this * resources.displayMetrics.density).toInt()

    companion object {
        // The family app's Kaamos night palette: blue-hour indigo, snow, Anna's lamp amber.
        val NIGHT = Color.rgb(15, 24, 48)
        val PANEL = Color.rgb(29, 42, 74)
        val SNOW = Color.rgb(238, 242, 246)
        val SOFT = Color.rgb(160, 176, 204)
        val AMBER = Color.rgb(242, 176, 74)
        val GREEN = Color.rgb(127, 214, 160)
        val RED = Color.rgb(232, 112, 104)
    }
}
