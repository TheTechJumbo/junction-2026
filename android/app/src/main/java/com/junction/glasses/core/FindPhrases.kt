package com.junction.glasses.core

import com.google.gson.JsonParser

/**
 * "Find me the orange box", "where is the orange box", "where did I put my orange box": the thing the wearer asks
 * for. The vision relay answers these from where the glasses last saw the thing (`POST /ask`, its live/finder.py,
 * which reads the questions the same way). "Take me there" afterwards walks the wearer to it (`/navigate`).
 */
object FindPhrases {
    private val LEADS = listOf("hey meta ", "ok ", "okay ", "hey ", "please ", "excuse me ")
    private val QUESTIONS = listOf(
        "^where (?:did|have|had|do) (?:i|we|you) (?:last )?(?:put|leave|left|place|placed|drop|dropped|see|seen|saw|set|keep|store)(?: down)? (.+)$",
        "^where (?:can|could|do|would|will) (?:i|we) find (.+)$",
        "^(?:can|could|would|will) you (?:please )?(?:find|look for|search for|locate|spot)(?: me| for me)? (.+)$",
        "^(?:please )?(?:find|look for|search for|locate|spot)(?: me| for me)? (.+)$",
        "^where(?: is| s| are|re| was| were) (.+)$",
        "^(?:have|did) you seen? (.+)$",
        "^(?:i|we) (?:have |ve )?(?:lost|misplaced|cannot find|can not find|can t find|cant find|couldn t find) (.+)$",
        "^do you know where (.+?) (?:is|are)$",
    ).map { Regex(it) }
    private val TAIL = Regex("\\b(please|for me|now|again|right now|today|then|at|located|kept|gone|go|went|to|is|are|was|were|put|left|in this room|in here|around here|around)$")
    private val ARTICLES = setOf("the", "my", "our", "a", "an", "your", "his", "her", "their", "some", "this", "that", "these", "those")
    private val PRONOUNS = setOf("it", "they", "them", "that", "this", "i", "me", "we", "you", "he", "she", "everything", "something",
        "anything", "there", "here", "home")
    private val PEOPLE = setOf("i", "we", "you", "he", "she")
    /** The things the backend has always answered (its own item list): those questions keep going there. */
    private val BACKEND_ITEMS = setOf("key", "keys", "wallet", "purse", "phone", "mobile", "glasses", "spectacles", "remote")
    /** "Where is the garage": a place, not a thing to find (as the relay's PLACE_LIKE). */
    private val PLACE_LIKE = setOf("room", "kitchen", "bathroom", "toilet", "loo", "wc", "garage", "bedroom", "hall", "hallway", "garden",
        "door", "exit", "entrance", "stairs", "lift", "elevator", "office", "lounge", "shop", "street", "station", "stage", "booth",
        "table", "fridge", "desk", "bed", "sofa", "couch", "corridor", "lobby", "reception", "car", "house", "home", "flat", "building",
        "floor", "venue", "area", "zone", "place")
    private val THERE = setOf("take me there", "take me to it", "take me to them", "guide me there", "lead me there", "walk me there",
        "bring me there", "show me the way", "show me where", "how do i get there", "go there", "take me")

    fun plain(text: String): String =
        text.lowercase().replace('’', '\'').replace(Regex("[^a-z0-9]+"), " ").trim().replace(Regex("\\s+"), " ")

    private fun withoutLead(text: String): String {
        var said = plain(text)
        for (lead in LEADS) if (said.startsWith(lead)) said = said.removePrefix(lead)
        return said
    }

    /** The thing asked for ("orange box"), or null when [text] is not a find / where-is question. */
    fun objectIn(text: String): String? {
        val said = withoutLead(text)
        for (pattern in QUESTIONS) {
            val found = pattern.find(said) ?: continue
            var thing = found.groupValues[1].trim()
            repeat(4) { thing = TAIL.replace(thing, "").trim() }
            val words = thing.split(' ').filter { it.isNotEmpty() && it !in ARTICLES }
            if (words.isEmpty() || words.all { it in PRONOUNS } || words.first() in PEOPLE || words.size > 5) return null
            return words.joinToString(" ")
        }
        return null
    }

    /** "Take me there", "yes, take me there please": go to what was just found. */
    fun isTakeMeThere(text: String): Boolean {
        var said = withoutLead(text).removePrefix("yes ").removePrefix("ok ").removeSuffix(" please").removeSuffix(" now").trim()
        said = said.removePrefix("please ")
        return said in THERE
    }

    /**
     * Whether the relay's finder should answer about [thing]: yes for a thing it already knows ([objects]: what the map
     * lists as seen things) and for any other thing that is not one of the backend's own items, not a named place
     * ([places]) and does not sound like a place.
     */
    fun asksRelay(thing: String, places: Collection<String>, objects: Collection<String>): Boolean {
        val words = plain(thing).split(' ').filter { it.isNotEmpty() }
        if (objects.any { NavPhrases.similarity(words.joinToString(" "), NavPhrases.normalize(it)) >= NavPhrases.BARE_MATCH }) return true
        if (words.any { it in BACKEND_ITEMS }) return false
        if (words.any { it in PLACE_LIKE || it.removeSuffix("s") in PLACE_LIKE }) return false
        val (_, score) = NavPhrases.best(words.joinToString(" "), places) ?: return true
        return score < NavPhrases.BARE_MATCH
    }
}

/** The relay's answer to a find question (`POST /ask`): the words, and the name `/navigate` takes for "take me there". */
data class FindReply(val answer: String, val target: String?, val confirmed: Boolean, val answeredBy: String?) {
    companion object {
        fun parse(json: String): FindReply {
            val root = JsonParser.parseString(json).asJsonObject
            fun text(key: String) = root.get(key)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString?.takeIf { it.isNotBlank() }
            val answer = text("answer") ?: throw IllegalArgumentException("no answer")
            return FindReply(answer.take(2000), text("target")?.take(80),
                root.get("confirmed")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean }?.asBoolean ?: false, text("answered_by"))
        }
    }
}
