package com.junction.glasses.core

import java.time.LocalTime
import kotlin.math.abs
import kotlin.math.roundToInt

/*
 * What the wearer hears. Gen 2 glasses have no display, so the cards that guide, remind and check
 * the door are also said, in a few plain words. Each line comes with a key: the same key means
 * "already said", so a card is spoken once and again only when what it says changes (a new
 * direction, the next distance step, arriving). Answers to questions are spoken by the turn
 * itself, so their cards are not repeated here.
 */
object WearerVoice {
    data class Cue(val line: String, val key: String)

    fun cue(card: Card): Cue? = when (card) {
        is Card.Guide -> {
            val place = spoken(card.target); val way = card.way
            when {
                card.arrived -> Cue("You're at ${place}.", "here:${card.target}")
                way == null -> Cue("Finding where you are.", "lost:${card.target}")
                else -> Cue("${card.target.trim().lowercase().cap()}: ${direction(way.bearingDeg)}, ${distance(way.distanceM)}.",
                    "go:${card.target}:${direction(way.bearingDeg)}:${step(way.distanceM)}")
            }
        }
        is Card.Due -> {
            val where = card.target?.let(::spoken) ?: "it"
            val way = card.way?.let { ", $where: ${direction(it.bearingDeg)}, ${distance(it.distanceM)}" }.orEmpty()
            Cue("Time for ${card.reminder.text.trim().trimEnd('.').lowercase()}$way.", "due:${card.reminder.id}")
        }
        is Card.Night -> Cue("It's ${clock(card.at)}. Back to bed?" + (card.way?.let { " Your bed is ${side(it.bearingDeg)}." } ?: ""), "night:${card.at}")
        is Card.GoingOut -> Cue("Going out? " + card.items.joinToString(" ") { item ->
            val name = ITEM_WORDS[item.glyph] ?: "it"
            if (item.near != null) "${name.cap()}: ${item.near.lowercase()}." else "${name.cap()}: not seen."
        } + " Lock the door.", "out")
        else -> null
    }

    /** Degrees from straight ahead (+ right) in words. */
    fun direction(bearing: Int): String {
        val b = abs(bearing)
        return when {
            b <= 20 -> "straight ahead"
            b <= 60 -> if (bearing > 0) "slightly right" else "slightly left"
            b <= 135 -> if (bearing > 0) "turn right" else "turn left"
            else -> "turn around"
        }
    }
    /** Where something is, from where the wearer faces. */
    fun side(bearing: Int): String {
        val b = abs(bearing)
        return when { b <= 45 -> "ahead of you"; b <= 135 -> if (bearing > 0) "to your right" else "to your left"; else -> "behind you" }
    }
    fun distance(metres: Double) = if (metres < 1.0) "almost there" else "${metres.roundToInt()} ${if (metres.roundToInt() == 1) "metre" else "metres"}"
    /** Distance steps that are worth saying again: 10, 5, 3, 2 and 1 metre. */
    fun step(metres: Double) = STEPS.firstOrNull { metres >= it } ?: 0

    private val STEPS = listOf(10, 5, 3, 2, 1)
    private val ITEM_WORDS = mapOf(Glyph.KEY to "keys", Glyph.WALLET to "wallet", Glyph.PHONE to "phone", Glyph.GLASSES to "glasses")
    private fun spoken(name: String): String {
        val n = name.trim().lowercase()
        return if (n in setOf("keys", "wallet", "phone", "glasses")) "your $n" else "the $n"
    }
    private fun clock(t: LocalTime) = "%d:%02d".format(t.hour, t.minute)
    private fun String.cap() = replaceFirstChar { it.uppercase() }
}
