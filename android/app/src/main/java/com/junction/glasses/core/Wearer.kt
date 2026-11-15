package com.junction.glasses.core

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.time.Duration
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

/*
 * What the wearer sees. The same card is shown on the glasses display and on the phone: one card at a
 * time, symbols first, a few words at most. Nothing here claims more than the sensors and the vision
 * relay observed: an opened pill box is "opened", never "pills taken"; a closed door is never "locked".
 */

/** The symbols the wearer UI uses. Each has a drawing (phone, and bitmaps for the glasses) and, where
 * Meta's display has one, a native glasses icon. */
enum class Glyph { PILL_BOX, DOOR, KEY, WALLET, PHONE, GLASSES, LOCK, HOUSE, MOON, BED, CHECK, CROSS, QUESTION, BELL, EYE, CLOCK, PLACE }

/** The way to something from where the wearer stands. When the vision relay found a route around the
 * furniture, [distanceM] is the walk along it, [bearingDeg] points to its next corner and [turns] counts
 * the corners after that; otherwise it is the straight line. Degrees are from straight ahead, + right. */
data class Way(val distanceM: Double, val bearingDeg: Int, val turns: Int = 0) {
    val metres: String get() = if (distanceM < 1.0) "<1 m" else "${Math.round(distanceM)} m"
}

data class Reminder(val id: String, val text: String, val time: String, val place: String?, val state: State) {
    enum class State { LATER, DUE, DONE, MISSED }
}
data class Place(val name: String, val way: Way)
data class Sighting(val name: String, val near: String, val time: String?, val way: Way?)
/** A position fix from the vision relay. [usable] is false when the relay is not sure where the wearer is. */
data class Fix(val usable: Boolean, val places: List<Place>, val seen: List<Sighting>) {
    fun place(name: String): Place? = if (usable) places.firstOrNull { it.name.equals(name, ignoreCase = true) } else null
    /** The way to a named place or a seen item. */
    fun way(name: String): Way? = place(name)?.way
        ?: if (usable) seen.firstOrNull { it.name.equals(name, ignoreCase = true) }?.way else null
    val names: List<String> get() = places.map { it.name } + seen.map { it.name }
}
/** A sensor observation from today's timeline. */
data class Sensed(val type: String, val at: ZonedDateTime)
data class Asked(val topic: Topic, val at: Instant, val answerText: String?)
sealed interface Topic {
    data object Pills : Topic
    data object Door : Topic
    data class Item(val name: String) : Topic
    /** "Take me to the bathroom", "where is the bed": any place the carer named on the relay. */
    data class Go(val words: String) : Topic
    data object Other : Topic
}

data class WearerInputs(
    val now: ZonedDateTime,
    val reminders: List<Reminder> = emptyList(),
    val fix: Fix? = null,
    val sensed: List<Sensed> = emptyList(),
    val asked: Asked? = null,
    /** Set while the wearer is being walked somewhere (they pressed Go, or asked to be taken there). */
    val guideTo: String? = null,
)

data class CheckItem(val glyph: Glyph, val near: String?)

sealed interface Card {
    /** Nothing needs the wearer. The glasses show nothing; the phone shows the clock and what is next. */
    data class Idle(val now: LocalTime, val next: Reminder?) : Card
    /** A reminder is due: its symbol, its time and, when the relay knows, the way to its place. */
    data class Due(val reminder: Reminder, val glyph: Glyph, val way: Way?, val target: String? = null) : Card
    /** The answer to "did I…?": what was observed and when ([ok] true), not observed ([ok] false) or
     * unclear ([ok] null), with the way to it when that helps. */
    data class Checked(val glyph: Glyph, val at: LocalTime?, val ok: Boolean?, val way: Way?, val target: String? = null) : Card
    /** At the front door during the day: the things to take, each with where it was last seen. */
    data class GoingOut(val items: List<CheckItem>) : Card
    /** At night by the front door: the time, "back to bed?" and the way to bed when the relay knows it. */
    data class Night(val at: LocalTime, val way: Way? = null) : Card
    /** Walking somewhere: the arrow, the place's symbol and the distance, updated as the wearer moves.
     * [way] is null while the relay is still finding where the wearer stands. */
    data class Guide(val glyph: Glyph, val way: Way?, val target: String, val arrived: Boolean) : Card
    /** "Where are my keys?": where they were last seen, when, and the way there. */
    data class Found(val glyph: Glyph, val near: String?, val time: String?, val way: Way?, val target: String? = null) : Card
    /** Any other answer, as short text. */
    data class Said(val text: String) : Card
}

object WearerBrain {
    const val FRONT_DOOR = "front door"
    const val PILL_BOX = "pill box"
    /** Standing this close to the front door counts as being about to go out. */
    const val DOOR_RADIUS_M = 2.0
    private val DOOR_JUST_OPENED = Duration.ofMinutes(2)
    private val NIGHT_DOOR = Duration.ofMinutes(10)
    private val ANSWER_SHOWN = Duration.ofSeconds(45)
    private val TAKE_ALONG = listOf("keys", "wallet", "phone")
    /** Closer than this to the place counts as there. */
    const val ARRIVED_M = 0.8

    fun decide(input: WearerInputs): Card {
        val now = input.now
        val night = now.hour < 6
        val nearDoor = input.fix?.place(FRONT_DOOR)?.let { it.way.distanceM <= DOOR_RADIUS_M } == true
        val doorOpened = input.sensed.filter { it.type == "door_open" }.maxByOrNull { it.at }
        fun doorWithin(window: Duration) = doorOpened != null && !doorOpened.at.isAfter(now) &&
            Duration.between(doorOpened.at, now) <= window

        // The wearer asked to be walked somewhere: that comes first until they are there or stop it.
        input.guideTo?.let { return guide(it, input.fix) }
        if (night && (nearDoor || doorWithin(NIGHT_DOOR))) {
            val bed = input.fix?.let { fix -> resolve("bed", fix)?.let(fix::way) }
            return Card.Night((if (doorWithin(NIGHT_DOOR)) doorOpened!!.at else now).toLocalTime().withSecond(0).withNano(0), bed)
        }
        input.reminders.firstOrNull { it.state == Reminder.State.DUE }?.let { reminder ->
            val place = reminder.place ?: placeIn(reminder.text)
            return Card.Due(reminder, glyphFor(place ?: reminder.text), place?.let { input.fix?.way(it) }, place)
        }
        if (!night && (nearDoor || doorWithin(DOOR_JUST_OPENED))) {
            return Card.GoingOut(TAKE_ALONG.map { name ->
                CheckItem(glyphFor(name), input.fix?.seen?.firstOrNull { it.name.equals(name, true) }?.near?.ifBlank { null })
            })
        }
        input.asked?.takeIf { Duration.between(it.at, now.toInstant()) <= ANSWER_SHOWN }?.let { return answer(it, input) }
        val next = input.reminders.filter { it.state == Reminder.State.LATER }.minByOrNull { it.time }
        return Card.Idle(now.toLocalTime().withSecond(0).withNano(0), next)
    }

    private fun answer(asked: Asked, input: WearerInputs): Card {
        val zone = input.now.zone
        fun lastToday(type: String) = input.sensed.filter { it.type == type && it.at.toLocalDate() == input.now.toLocalDate() }
            .maxByOrNull { it.at }?.at?.withZoneSameInstant(zone)?.toLocalTime()?.withSecond(0)?.withNano(0)
        return when (val topic = asked.topic) {
            // Opened today: no way to the box, so the card never leads to a second dose.
            Topic.Pills -> lastToday("pillbox_open")?.let { Card.Checked(Glyph.PILL_BOX, it, true, null) }
                ?: Card.Checked(Glyph.PILL_BOX, null, false, input.fix?.way(PILL_BOX), PILL_BOX)
            Topic.Door -> {
                val opened = lastToday("door_open"); val closed = lastToday("door_closed")
                when {
                    closed != null && (opened == null || !closed.isBefore(opened)) -> Card.Checked(Glyph.DOOR, closed, true, null)
                    opened != null -> Card.Checked(Glyph.DOOR, opened, null, input.fix?.way(FRONT_DOOR), FRONT_DOOR)
                    else -> Card.Checked(Glyph.DOOR, null, null, input.fix?.way(FRONT_DOOR), FRONT_DOOR)
                }
            }
            is Topic.Item -> input.fix?.seen?.firstOrNull { it.name.equals(topic.name, true) }
                ?.let { Card.Found(glyphFor(it.name), it.near.ifBlank { null }, it.time, it.way, it.name) }
                ?: Card.Checked(glyphFor(topic.name), null, false, null)
            is Topic.Go -> input.fix?.let { fix -> resolve(topic.words, fix)?.let { guide(it, fix) } }
                ?: Card.Checked(Glyph.PLACE, null, null, null)
            Topic.Other -> Card.Said(shorten(asked.answerText ?: ""))
        }
    }

    /** The guide card for a place or item: the way there, or "there" once within [ARRIVED_M]. */
    fun guide(target: String, fix: Fix?): Card.Guide {
        val name = fix?.let { resolve(target, it) } ?: target
        val way = fix?.way(name)
        return Card.Guide(glyphFor(name), way, name, way != null && way.distanceM <= ARRIVED_M)
    }

    /** The relay's name for what the words ask for: the exact name, a known other word for it
     * (loo -> bathroom), or a name that shares a word with the question. */
    fun resolve(words: String, fix: Fix): String? {
        val names = fix.names
        names.firstOrNull { it.equals(words.trim(), ignoreCase = true) }?.let { return it }
        val asked = Regex("[a-z]+").findAll(words.lowercase()).map { it.value }
            .flatMap { listOf(it) + SAME_PLACE[it].orEmpty() }.filter { it.length > 1 && it !in FILLER }.toSet()
        return names.firstOrNull { name -> Regex("[a-z]+").findAll(name.lowercase()).any { it.value in asked } }
    }
    private val SAME_PLACE = mapOf(
        "toilet" to listOf("bathroom", "wc"), "loo" to listOf("bathroom", "toilet"), "wc" to listOf("bathroom", "toilet"),
        "bathroom" to listOf("toilet"), "bed" to listOf("bedroom"), "bedroom" to listOf("bed"), "sleep" to listOf("bed", "bedroom"),
        "pills" to listOf("pill"), "medicine" to listOf("pill"), "tablets" to listOf("pill"),
        "out" to listOf("door"), "exit" to listOf("door"), "couch" to listOf("sofa"), "sofa" to listOf("couch"))
    private val FILLER = setOf("the", "my", "to", "where", "take", "me", "how", "get", "can", "you", "please", "way",
        "back", "show", "bring", "guide", "lead", "walk", "is", "are", "of", "go", "do", "find")

    /** What a spoken or typed question is about. */
    fun classify(question: String): Topic {
        val q = question.lowercase()
        return when {
            Regex("\\b(pill|pills|medic\\w*|tablet\\w*|dose)\\b").containsMatchIn(q) -> Topic.Pills
            Regex("\\b(door|lock\\w*)\\b").containsMatchIn(q) -> Topic.Door
            else -> ITEMS.entries.firstOrNull { (_, words) -> words.any { Regex("\\b$it\\b").containsMatchIn(q) } }
                ?.let { Topic.Item(it.key) }
                ?: GO.find(q)?.let { Topic.Go(q.substring(it.range.last + 1).trim(' ', '?', '.', '!')) }?.takeIf { it.words.isNotBlank() }
                ?: Topic.Other
        }
    }
    private val GO = Regex("\\b(?:(?:take|bring|guide|lead|walk) me(?: back)? to|how (?:do|can) i get to|(?:the )?way to|where(?:'s| is| are)|go to)\\b")
    private val ITEMS = mapOf("keys" to listOf("key", "keys"), "wallet" to listOf("wallet", "purse"),
        "phone" to listOf("phone", "mobile"), "glasses" to listOf("glasses", "spectacles"))

    fun glyphFor(name: String): Glyph {
        val n = name.lowercase()
        return when {
            "pill" in n || "medic" in n -> Glyph.PILL_BOX
            "door" in n -> Glyph.DOOR
            "key" in n -> Glyph.KEY
            "wallet" in n || "purse" in n -> Glyph.WALLET
            "phone" in n -> Glyph.PHONE
            "glasses" in n -> Glyph.GLASSES
            "bed" in n -> Glyph.BED
            else -> Glyph.PLACE
        }
    }
    private fun placeIn(text: String): String? = when {
        Regex("pill|medic", RegexOption.IGNORE_CASE).containsMatchIn(text) -> PILL_BOX
        Regex("door", RegexOption.IGNORE_CASE).containsMatchIn(text) -> FRONT_DOOR
        else -> null
    }
    /** At most about eight words: the glasses show a line, not a paragraph. */
    fun shorten(text: String): String {
        val first = text.trim().split(Regex("(?<=[.!?])\\s+")).firstOrNull().orEmpty()
        val words = first.split(Regex("\\s+")).filter { it.isNotBlank() }
        return if (words.size <= 8) first else words.take(8).joinToString(" ") + "…"
    }
}

/** Parsing for the vision relay (`GET /reminders`, `GET /where`) and the event API (`GET /timeline`). */
object WearerJson {
    fun reminders(json: String): List<Reminder> {
        val root = JsonParser.parseString(json).asJsonObject
        return root.getAsJsonArray("reminders")?.mapNotNull { element ->
            val r = element.asJsonObject
            val state = when (r.str("state")) {
                "later" -> Reminder.State.LATER; "due" -> Reminder.State.DUE
                "done" -> Reminder.State.DONE; "missed" -> Reminder.State.MISSED; else -> return@mapNotNull null
            }
            Reminder(r.str("id") ?: return@mapNotNull null, r.str("text").orEmpty(), r.str("time").orEmpty(), r.str("place")?.ifBlank { null }, state)
        }.orEmpty()
    }

    fun fix(json: String): Fix {
        val root = JsonParser.parseString(json).asJsonObject
        fun way(o: JsonObject): Way? {
            val d = o.get("distance_m")?.takeUnless { it.isJsonNull }?.asDouble ?: return null
            val b = o.get("bearing_deg")?.takeUnless { it.isJsonNull }?.asDouble ?: return null
            return if (d.isFinite() && b.isFinite()) Way(d, Math.round(b).toInt()) else null
        }
        val usable = root.str("state") != "lost"
        // The relay's route around the furniture, per place or item name, when it found one.
        val routed = root.obj("route")?.obj("to")?.entrySet()?.mapNotNull { (name, value) ->
            val o = value.takeIf { it.isJsonObject }?.asJsonObject ?: return@mapNotNull null
            val next = o.obj("next") ?: return@mapNotNull null
            val length = o.get("length_m")?.takeUnless { it.isJsonNull }?.asDouble ?: return@mapNotNull null
            val bearing = next.get("bearing_deg")?.takeUnless { it.isJsonNull }?.asDouble ?: return@mapNotNull null
            if (!length.isFinite() || !bearing.isFinite()) return@mapNotNull null
            name.lowercase() to Way(length, Math.round(bearing).toInt(), o.get("corners")?.takeUnless { it.isJsonNull }?.asInt ?: 0)
        }?.toMap().orEmpty()
        val places = root.getAsJsonArray("waypoints")?.mapNotNull { e ->
            val o = e.asJsonObject; val name = o.str("name") ?: return@mapNotNull null
            Place(name, routed[name.lowercase()] ?: way(o) ?: return@mapNotNull null)
        }.orEmpty()
        val seen = root.getAsJsonArray("seen")?.mapNotNull { e ->
            val o = e.asJsonObject
            if (o.get("doubt")?.takeUnless { it.isJsonNull }?.asBoolean == true) return@mapNotNull null
            val name = o.str("name") ?: return@mapNotNull null
            Sighting(name, o.str("near").orEmpty(), o.str("time"), routed[name.lowercase()] ?: way(o))
        }.orEmpty()
        return Fix(usable, places, seen)
    }

    /** Sensor observations only: vision evidence never drives a reminder or an answer card. */
    fun sensed(json: String, zone: ZoneId): List<Sensed> {
        val root = JsonParser.parseString(json).asJsonObject
        return root.getAsJsonArray("events")?.mapNotNull { e ->
            val o = e.asJsonObject
            if (o.str("source") != "sensor") return@mapNotNull null
            val at = runCatching { ZonedDateTime.parse(o.str("ts")).withZoneSameInstant(zone) }.getOrNull() ?: return@mapNotNull null
            Sensed(o.str("type") ?: return@mapNotNull null, at)
        }.orEmpty()
    }

    private fun JsonObject.str(key: String): String? = get(key)?.takeUnless { it.isJsonNull }?.asString
    private fun JsonObject.obj(key: String): JsonObject? = get(key)?.takeIf { it.isJsonObject }?.asJsonObject
}
