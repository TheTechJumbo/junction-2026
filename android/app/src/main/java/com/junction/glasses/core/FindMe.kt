package com.junction.glasses.core

import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlin.coroutines.cancellation.CancellationException

/** What the wearer asked: to find a thing, or to be taken to the thing just found. */
sealed interface FindCommand {
    data class Find(val what: String) : FindCommand
    object TakeMeThere : FindCommand
}

/**
 * Recognises "find me the orange box", "where is my keys", "where did I put the orange box", "have you
 * seen my glasses" (spoken or typed) and "take me there". The thing is returned without articles ("the",
 * "my") and polite words; a bare pronoun ("where is it") is not a thing. Known place names are checked
 * first by the caller (NavPhrases), so "where is the pill box" stays guidance.
 */
object FindPhrases {
    private val FILLERS = listOf("hey meta", "hey", "ok", "okay", "please", "could you", "can you", "would you", "will you")
    private val ENDINGS = listOf("for me", "please", "now", "again", "today")
    private val LEADS = listOf("where did i put", "where did i leave", "where have i put", "where have i left", "where did i last see",
        "have you seen", "did you see", "help me find", "find me", "look for", "search for", "where is", "where are", "wheres",
        "where s", "find")
    private val THERE = setOf("take me there", "take me to it", "take me to them", "go there", "lead me there", "guide me there",
        "walk me there", "bring me there", "show me the way", "show me the way there", "how do i get there")
    private val ARTICLES = setOf("the", "my", "a", "an", "our", "your", "his", "her", "their")
    private val NOT_A_THING = setOf("it", "them", "that", "this", "those", "these", "he", "she", "they", "i", "you", "we")

    fun parse(text: String): FindCommand? {
        var said = NavPhrases.normalize(text)
        var changed = true
        while (changed) {
            changed = false
            for (f in FILLERS) if (said.startsWith("$f ")) { said = said.removePrefix("$f "); changed = true }
            for (e in ENDINGS) if (said.endsWith(" $e")) { said = said.removeSuffix(" $e"); changed = true }
        }
        if (said in THERE) return FindCommand.TakeMeThere
        val lead = LEADS.firstOrNull { said.startsWith("$it ") } ?: return null
        val words = said.removePrefix("$lead ").split(' ').filter { it.isNotEmpty() }.dropWhile { it in ARTICLES }
        if (words.isEmpty() || words.joinToString(" ") in NOT_A_THING || words.size > MAX_WORDS) return null
        return FindCommand.Find(words.joinToString(" "))
    }

    private const val MAX_WORDS = 5
}

/**
 * The relay's answer to `GET /find?zone=Z&what=X`: whether and where the thing was seen ([place] is the
 * named place it is by, [position] on the map), when ([at]), where it was before when it moved
 * ([movedFrom]), the way to it from the wearer (relative bearing: 0 ahead, + right), the words to say
 * ([answer]), and whether the relay is still [looking]. Read leniently: a changed relay never crashes.
 */
data class FindReply(
    val ok: Boolean,
    val what: String? = null,
    val found: Boolean = false,
    val place: String? = null,
    val position: FloorPoint? = null,
    val at: String? = null,
    val movedFrom: String? = null,
    val distanceM: Double? = null,
    val bearingDeg: Double? = null,
    val answer: String? = null,
    val looking: Boolean = false,
    val stale: Boolean = false,
    val error: String? = null,
) {
    companion object {
        fun parse(json: String): FindReply {
            val root = try { JsonParser.parseString(json).asJsonObject } catch (_: Exception) {
                return FindReply(ok = false, error = "The relay's reply was not JSON.")
            }
            fun flag(key: String): Boolean? =
                root.get(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean }?.asBoolean
            fun num(key: String): Double? =
                root.get(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asDouble?.takeIf { it.isFinite() }
            fun text(e: JsonElement?): String? =
                e?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString?.trim()?.takeIf { it.isNotEmpty() }
            fun text(key: String): String? = text(root.get(key))
            fun point(e: JsonElement?): FloorPoint? {
                val a = e?.takeIf { it.isJsonArray }?.asJsonArray ?: return null
                if (a.size() < 2) return null
                val east = a[0].takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asDouble ?: return null
                val north = a[1].takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asDouble ?: return null
                return if (east.isFinite() && north.isFinite()) FloorPoint(east, north) else null
            }
            // moved_from may be a place name, or an object with one.
            val moved = root.get("moved_from")
            val movedFrom = text(moved) ?: moved?.takeIf { it.isJsonObject }?.asJsonObject?.let { o: JsonObject -> text(o.get("place")) ?: text(o.get("name")) }
            return FindReply(
                ok = flag("ok") ?: true,
                what = text("what")?.take(60),
                found = flag("found") ?: false,
                place = text("place")?.take(60),
                position = point(root.get("position")),
                at = text("at")?.take(40),
                movedFrom = movedFrom?.take(60),
                distanceM = num("distance_m"),
                bearingDeg = num("bearing_deg"),
                answer = text("answer")?.take(300),
                looking = flag("looking") ?: false,
                stale = flag("stale") ?: false,
                error = text("error")?.take(200),
            )
        }
    }
}

/** What is said while finding: the relay's own words when it gives them, short ones otherwise. */
object FindWords {
    fun looking(what: String) = "I'll look for the $what."
    fun gaveUp(what: String) = "I couldn't find the $what yet."

    fun answer(reply: FindReply, what: String): String {
        reply.answer?.let { return it }
        if (!reply.ok) return reply.error ?: "I couldn't look for the $what."
        if (!reply.found) return "I haven't seen the $what."
        val way = if (reply.distanceM != null && reply.bearingDeg != null)
            NavWords.distance(reply.distanceM) + ", " + NavWords.direction(reply.bearingDeg) else null
        return when {
            reply.place != null -> "The $what is by the ${reply.place}" + (way?.let { ", $it" } ?: "") + "."
            way != null -> "The $what: $way."
            else -> "I've seen the $what."
        }
    }
}

/**
 * One "find me X": asks [fetch] (the relay's /find) once. Found, or not found and not looking: say the
 * answer and stop. Looking: say "I'll look for the X" and ask again every [pollMs] for up to [maxMs],
 * then say the answer when it is found, or that it was not found. A failure on the first ask is said
 * ([failure]); while polling, failures are retried until the time is up. Every reply goes to [shown].
 */
class FindSearch(
    val what: String,
    private val fetch: suspend () -> FindReply,
    private val say: (String) -> Unit,
    private val shown: (FindReply) -> Unit,
    private val now: () -> Long,
    private val sleep: suspend (Long) -> Unit,
    private val failure: (Exception) -> String = { "I can't reach the laptop to look for the $what." },
    private val pollMs: Long = POLL_MS,
    private val maxMs: Long = MAX_MS,
) {
    /** The newest reply, or null before the first. */
    var last: FindReply? = null
        private set

    /** Runs the search; returns the last reply (found or not), or null when the first ask failed. */
    suspend fun run(): FindReply? {
        val start = now()
        var announced = false
        while (true) {
            val reply = try {
                fetch()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (!announced) { say(failure(e)); return null }
                null
            }
            if (reply != null) {
                last = reply
                shown(reply)
                if (reply.found || !reply.looking) { say(FindWords.answer(reply, what)); return reply }
                if (!announced) { say(FindWords.looking(what)); announced = true }
            }
            if (now() - start + pollMs > maxMs) { say(FindWords.gaveUp(what)); return last }
            sleep(pollMs)
        }
    }

    companion object {
        const val POLL_MS = 3_000L
        const val MAX_MS = 60_000L
    }
}
