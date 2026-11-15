package com.junction.glasses.core

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

/** A point on the zone's floor, in metres: [east] to the right, [north] up on the map. */
data class FloorPoint(val east: Double, val north: Double)

/** A named place, or with [kind] "object" a thing the relay has seen ("orange box"): walkable, but asked about with /find. */
data class NavPlace(val name: String, val at: FloorPoint, val kind: String? = null) {
    val isThing: Boolean get() = kind == "object"
}

/**
 * The relay's answer to `GET /navigate?zone=Z&to=NAME`: where the wearer is and faces, the way to the
 * place, and the room for the map. [bearingDeg] is relative to the wearer: 0 ahead, + to the right.
 * [headingDeg] is the wearer's facing on the map: 0 north, + towards east.
 */
data class NavReply(
    val ok: Boolean,
    val to: String? = null,
    val arrived: Boolean = false,
    val distanceM: Double? = null,
    val bearingDeg: Double? = null,
    val instruction: String? = null,
    val route: List<FloorPoint> = emptyList(),
    val position: FloorPoint? = null,
    val headingDeg: Double? = null,
    val at: String? = null,
    val places: List<NavPlace> = emptyList(),
    val plan: List<FloorPoint> = emptyList(),
    val error: String? = null,
) {
    companion object {
        /** Lenient: missing or malformed fields stay empty, so a changed relay never crashes the map. */
        fun parse(json: String): NavReply {
            val root = try { JsonParser.parseString(json).asJsonObject } catch (_: Exception) {
                return NavReply(ok = false, error = "The relay's reply was not JSON.")
            }
            fun num(o: JsonObject, key: String): Double? =
                o.get(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asDouble?.takeIf { it.isFinite() }
            fun text(o: JsonObject, key: String): String? =
                o.get(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString?.takeIf { it.isNotBlank() }
            fun point(e: JsonElement?): FloorPoint? {
                val a = e?.takeIf { it.isJsonArray }?.asJsonArray ?: return null
                if (a.size() < 2) return null
                val east = a[0].takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asDouble ?: return null
                val north = a[1].takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asDouble ?: return null
                return if (east.isFinite() && north.isFinite()) FloorPoint(east, north) else null
            }
            fun points(a: JsonArray?): List<FloorPoint> = a?.mapNotNull { point(it) }.orEmpty()
            val places = root.get("places")?.takeIf { it.isJsonArray }?.asJsonArray?.mapNotNull { e ->
                val o = e.takeIf { it.isJsonObject }?.asJsonObject ?: return@mapNotNull null
                NavPlace(text(o, "name") ?: return@mapNotNull null, point(o.get("position")) ?: return@mapNotNull null, text(o, "kind"))
            }.orEmpty()
            val plan = root.get("plan")?.takeIf { it.isJsonObject }?.asJsonObject?.get("points")?.takeIf { it.isJsonArray }?.asJsonArray
            return NavReply(
                ok = root.get("ok")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean }?.asBoolean ?: true,
                to = text(root, "to"),
                arrived = root.get("arrived")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean }?.asBoolean ?: false,
                distanceM = num(root, "distance_m"),
                bearingDeg = num(root, "bearing_deg"),
                instruction = text(root, "instruction")?.take(200),
                route = points(root.get("route")?.takeIf { it.isJsonArray }?.asJsonArray),
                position = point(root.get("position")),
                headingDeg = num(root, "heading_deg"),
                at = text(root, "at"),
                places = places,
                plan = points(plan),
                error = text(root, "error")?.take(200),
            )
        }
    }
}

/** What the wearer asked for: to be taken to a place, or to stop being guided. */
sealed interface NavCommand {
    data class GoTo(val place: String) : NavCommand
    object Stop : NavCommand
}

/**
 * Recognises "take me to / where is / how do I get to the pill box" and "stop" in what the wearer said
 * (or typed), and matches the place to the nearest known place name, forgiving small differences
 * ("pillbox", "the pill boxes", "front door please"). A bare place name is enough when it matches
 * well. Anything else is not navigation and goes on to the normal question.
 */
object NavPhrases {
    private val STOP = setOf("stop", "cancel", "stop navigation", "stop guiding", "stop directions", "stop it",
        "never mind", "nevermind", "i am here", "im here")
    private val FILLERS = listOf("hey meta", "ok", "okay", "please", "could you", "can you", "would you", "will you")
    private val LEADS = listOf("take me back to", "take me to", "guide me to", "bring me to", "lead me to", "walk me to", "show me the way to",
        "how do i get to", "how can i get to", "how to get to", "how do i find", "navigate to", "directions to", "the way to",
        "i want to go to", "i need to go to", "go to", "where is", "wheres", "where s", "find")
    private val ARTICLES = setOf("the", "my", "a", "an", "our", "please")

    fun normalize(text: String): String =
        text.lowercase().replace('’', '\'').replace("'", " ").replace(Regex("[^a-z0-9 ]"), " ").replace(Regex("\\s+"), " ").trim()

    fun parse(text: String, places: Collection<String>): NavCommand? {
        var said = normalize(text)
        for (filler in FILLERS) said = said.removePrefix("$filler ").removeSuffix(" $filler")
        if (said in STOP) return NavCommand.Stop
        val lead = LEADS.firstOrNull { said.startsWith("$it ") || said.contains(" $it ") }
        val target = if (lead != null) said.substringAfter("$lead ") else said
        val wanted = target.split(' ').filter { it.isNotEmpty() && it !in ARTICLES }.joinToString(" ")
        if (wanted.isEmpty()) return null
        val (place, score) = best(wanted, places) ?: return null
        val needed = if (lead != null) LEAD_MATCH else BARE_MATCH
        return if (score >= needed) NavCommand.GoTo(place) else null
    }

    /** The known place most like [wanted] and how alike (0 to 1). */
    fun best(wanted: String, places: Collection<String>): Pair<String, Double>? =
        places.map { it to similarity(wanted, withoutArticles(normalize(it))) }.maxByOrNull { it.second }

    /** "our table" is matched as "table", the same way the wearer's words are. */
    private fun withoutArticles(name: String): String =
        name.split(' ').filter { it !in ARTICLES }.joinToString(" ").ifEmpty { name }

    fun similarity(a: String, b: String): Double {
        if (a.isEmpty() || b.isEmpty()) return 0.0
        val x = a.replace(" ", ""); val y = b.replace(" ", "")
        if (x == y) return 1.0
        val wordsA = a.split(' ').map(::stem).toSet(); val wordsB = b.split(' ').map(::stem).toSet()
        val overlap = wordsA.intersect(wordsB).size.toDouble() / max(wordsA.size, wordsB.size)
        val letters = 1.0 - levenshtein(x, y).toDouble() / max(x.length, y.length)
        // A place said inside a longer phrase ("the pill box in the kitchen") still counts.
        val inside = if (wordsB.all { it in wordsA }) 0.9 else 0.0
        return maxOf(overlap, letters, inside)
    }

    private fun stem(word: String) = word.removeSuffix("es").removeSuffix("s").ifEmpty { word }

    private fun levenshtein(a: String, b: String): Int {
        var previous = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val current = IntArray(b.length + 1)
            current[0] = i
            for (j in 1..b.length) {
                current[j] = minOf(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
            }
            previous = current
        }
        return previous[b.length]
    }

    const val LEAD_MATCH = 0.6
    const val BARE_MATCH = 0.85
}

/** Short spoken directions: "pill box, 3 metres ahead, slightly left". */
object NavWords {
    fun direction(bearingDeg: Double): String {
        val b = ((bearingDeg % 360) + 540) % 360 - 180 // -180..180, + right
        val side = if (b > 0) "right" else "left"
        return when {
            abs(b) <= 15 -> "ahead"
            abs(b) <= 45 -> "ahead, slightly $side"
            abs(b) <= 135 -> "turn $side"
            else -> "behind you, turn around"
        }
    }

    fun distance(metres: Double): String = when {
        metres < 1.0 -> "less than a metre"
        else -> metres.roundToInt().let { if (it == 1) "1 metre" else "$it metres" }
    }

    /** "pill box, 3 metres ahead, slightly left"; a turn gets its own clause: "front door, 1 metre, turn right". */
    fun instruction(place: String, metres: Double, bearingDeg: Double): String {
        val way = direction(bearingDeg)
        return if (way.startsWith("ahead")) "$place, ${distance(metres)} $way" else "$place, ${distance(metres)}, $way"
    }
}

/**
 * Paces the spoken guidance to one place: it speaks when the direction changes (after at least
 * [minGapMs]) and otherwise at most every [repeatMs], says "You're there" once within [arriveM] (or
 * when the relay says arrived) and is then done. Distances alone changing ("3 metres" to "2 metres")
 * do not count as a change, so it does not chatter on every fix.
 */
class NavGuide(
    val place: String,
    private val repeatMs: Long = REPEAT_MS,
    private val minGapMs: Long = MIN_GAP_MS,
    private val arriveM: Double = ARRIVE_M,
) {
    var arrived = false
        private set
    private var lastKey: String? = null
    private var lastAtMs: Long? = null

    /** What to say for this reply at [nowMs], or null to stay quiet. */
    fun next(reply: NavReply, nowMs: Long): String? {
        if (arrived) return null
        val distance = reply.distanceM
        if (reply.arrived || (distance != null && distance <= arriveM)) {
            arrived = true
            return "You're there: $place."
        }
        val say = reply.instruction ?: if (distance != null && reply.bearingDeg != null) NavWords.instruction(place, distance, reply.bearingDeg) else return null
        val key = say.lowercase().replace(Regex("[0-9.,]+"), "#").replace(Regex("\\s+"), " ").trim()
        val since = lastAtMs?.let { nowMs - it }
        val speak = since == null || (key != lastKey && since >= minGapMs) || since >= repeatMs
        if (!speak) return null
        lastKey = key
        lastAtMs = nowMs
        return say
    }

    companion object {
        const val REPEAT_MS = 4_000L
        const val MIN_GAP_MS = 1_500L
        const val ARRIVE_M = 0.7
    }
}
