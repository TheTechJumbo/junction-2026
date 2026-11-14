package com.junction.glasses.meta

import android.graphics.Bitmap
import com.junction.glasses.core.Card
import com.junction.glasses.core.Glyph
import com.junction.glasses.core.Way
import com.junction.glasses.wearer.Glyphs
import com.meta.wearable.dat.display.views.ButtonStyle
import com.meta.wearable.dat.display.views.Direction
import com.meta.wearable.dat.display.views.FlexBoxScope
import com.meta.wearable.dat.display.views.IconName
import com.meta.wearable.dat.display.views.ImageSize
import com.meta.wearable.dat.display.views.TextColor
import com.meta.wearable.dat.display.views.TextStyle
import java.time.LocalTime

/**
 * A wearer card on Meta's glasses display: Meta's own icons where it has one, our line drawings as
 * bitmaps where it does not (door, key, wallet, place, and the arrow turned to the way to go). Text is
 * a time, a distance or one or two words. An idle card sends nothing: black is "off" on the lenses.
 */
object GlassesCards {
    private const val ICON_PX = 96
    private val bitmaps = HashMap<String, Bitmap>()

    private fun native(glyph: Glyph): IconName? = when (glyph) {
        Glyph.PILL_BOX -> IconName.CONTAINER_WITH_LID
        Glyph.LOCK -> IconName.PADLOCK_CLOSED
        Glyph.HOUSE -> IconName.HOUSE
        Glyph.MOON -> IconName.CLOUD_CRESCENT_MOON
        Glyph.BED -> IconName.BED_SIDE
        Glyph.CHECK -> IconName.CHECKMARK_CIRCLE
        Glyph.CROSS -> IconName.X
        Glyph.BELL -> IconName.BELL
        Glyph.EYE -> IconName.EYE
        Glyph.CLOCK -> IconName.CLOCK
        Glyph.PHONE -> IconName.PHONE
        Glyph.GLASSES -> IconName.SMART_GLASSES
        Glyph.QUESTION -> IconName.I_CIRCLE
        Glyph.DOOR, Glyph.KEY, Glyph.WALLET, Glyph.PLACE -> null
    }

    private fun drawn(data: String, degrees: Int = 0): Bitmap {
        val turn = ((degrees % 360) + 360) % 360 / 5 * 5 // 5-degree steps keep the cache small
        return bitmaps.getOrPut("$data@$turn") { Glyphs.bitmap(data, ICON_PX, turn.toFloat()) }
    }

    private fun FlexBoxScope.symbol(glyph: Glyph) {
        val icon = native(glyph)
        if (icon != null) icon(icon) else image(glyph.name.lowercase(), drawn(Glyphs.dataFor(glyph)), ImageSize.ICON)
    }
    private fun FlexBoxScope.way(way: Way?) {
        if (way == null) return
        flexBox(direction = Direction.ROW, gap = 8) {
            image("way", drawn(Glyphs.arrowData, way.bearingDeg), ImageSize.ICON)
            text(way.metres, TextStyle.HEADING)
        }
    }
    private fun clock(t: LocalTime?) = if (t == null) "–" else "%02d:%02d".format(t.hour, t.minute)

    /** True when the card has something to show on the glasses. */
    fun shows(card: Card) = card !is Card.Idle

    fun FlexBoxScope.render(card: Card, onDone: (String) -> Unit) {
        when (card) {
            is Card.Idle -> Unit
            is Card.Due -> {
                flexBox(direction = Direction.ROW, gap = 8) { icon(IconName.BELL); text(card.reminder.time, TextStyle.META, TextColor.SECONDARY) }
                flexBox(direction = Direction.ROW, gap = 8) { symbol(card.glyph); text(word(card.glyph), TextStyle.HEADING) }
                way(card.way)
                button("Done", ButtonStyle.PRIMARY, IconName.CHECKMARK, { onDone(card.reminder.id) })
            }
            is Card.Checked -> {
                flexBox(direction = Direction.ROW, gap = 8) {
                    symbol(card.glyph)
                    icon(when (card.ok) { true -> IconName.CHECKMARK_CIRCLE; false -> IconName.X; null -> IconName.I_CIRCLE })
                    card.at?.let { text(clock(it), TextStyle.HEADING) }
                }
                way(card.way)
            }
            is Card.GoingOut -> {
                flexBox(direction = Direction.ROW, gap = 8) { icon(IconName.HOUSE); text("Going out?", TextStyle.HEADING) }
                for (item in card.items) flexBox(direction = Direction.ROW, gap = 8) {
                    symbol(item.glyph); text(item.near ?: "?", TextStyle.BODY, if (item.near == null) TextColor.SECONDARY else TextColor.PRIMARY)
                }
                flexBox(direction = Direction.ROW, gap = 8) { icon(IconName.PADLOCK_CLOSED); text("Lock", TextStyle.BODY) }
            }
            is Card.Night -> {
                flexBox(direction = Direction.ROW, gap = 8) { icon(IconName.CLOUD_CRESCENT_MOON); text(clock(card.at), TextStyle.HEADING) }
                flexBox(direction = Direction.ROW, gap = 8) { icon(IconName.BED_SIDE); text("Bed?", TextStyle.HEADING) }
            }
            is Card.Guide -> {
                image("way", drawn(Glyphs.arrowData, card.way.bearingDeg), ImageSize.ICON)
                flexBox(direction = Direction.ROW, gap = 8) { symbol(card.glyph); text(card.way.metres, TextStyle.HEADING) }
            }
            is Card.Found -> {
                flexBox(direction = Direction.ROW, gap = 8) { symbol(card.glyph); text(card.near ?: "Seen", TextStyle.HEADING) }
                card.time?.let { t -> flexBox(direction = Direction.ROW, gap = 8) { icon(IconName.CLOCK); text(t, TextStyle.META, TextColor.SECONDARY) } }
                way(card.way)
            }
            is Card.Said -> text(card.text, TextStyle.BODY)
        }
    }

    private fun word(glyph: Glyph) = when (glyph) {
        Glyph.PILL_BOX -> "Pills"; Glyph.DOOR -> "Door"; Glyph.KEY -> "Keys"; Glyph.WALLET -> "Wallet"
        Glyph.PHONE -> "Phone"; Glyph.GLASSES -> "Glasses"; else -> "Now"
    }
}
