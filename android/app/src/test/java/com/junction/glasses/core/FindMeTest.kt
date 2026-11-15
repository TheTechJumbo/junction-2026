package com.junction.glasses.core

import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

/** "Find me X": what the wearer says, the relay's /find reply, and the ask, look and answer flow. */
class FindMeTest {
    @Test fun findPhrasesNameTheThingWithoutArticlesOrPoliteWords() {
        val orangeBox = FindCommand.Find("orange box")
        for (said in listOf("Find me the orange box", "find me orange box", "Where is the orange box?", "Where's the orange box",
                "where did I put the orange box", "Where did I leave my orange box?", "Have you seen the orange box?",
                "Hey Meta, find me the orange box please", "Can you find the orange box for me?", "help me find the orange box",
                "look for the orange box", "OK, where is my orange box")) {
            assertEquals(said, orangeBox, FindPhrases.parse(said))
        }
        assertEquals(FindCommand.Find("keys"), FindPhrases.parse("Where are my keys?"))
        assertEquals(FindCommand.Find("glasses case"), FindPhrases.parse("have you seen my glasses case"))
    }

    @Test fun otherQuestionsAndPronounsAreNotFinds() {
        for (said in listOf("Did I take my pills?", "Did I lock the door?", "What did I do today?", "where is it", "Where are you?",
                "find", "take me to the pill box", "stop", "")) {
            assertNull(said, FindPhrases.parse(said))
        }
    }

    @Test fun takeMeThereGoesToTheThingJustFound() {
        for (said in listOf("Take me there", "take me there please", "Hey Meta, take me to it", "show me the way", "go there")) {
            assertEquals(said, FindCommand.TakeMeThere, FindPhrases.parse(said))
        }
    }

    @Test fun theRelayReplyIsReadLeniently() {
        val reply = FindReply.parse("""{"ok":true,"what":"orange box","found":true,"place":"snack table","position":[-0.2,-3.4,0.1],
            "at":"04:12","moved_from":"our table","distance_m":2.6,"bearing_deg":-30,"answer":"The orange box is on the snack table.",
            "looking":false,"stale":false}""")
        assertTrue(reply.ok); assertTrue(reply.found)
        assertEquals("snack table", reply.place)
        assertEquals(FloorPoint(-0.2, -3.4), reply.position)
        assertEquals("our table", reply.movedFrom)
        assertEquals(2.6, reply.distanceM!!, 1e-9)
        assertEquals("The orange box is on the snack table.", reply.answer)
        val looking = FindReply.parse("""{"ok":true,"what":"orange box","found":false,"looking":true,"position":null,"moved_from":{"place":"desk"}}""")
        assertTrue(looking.looking); assertFalse(looking.found); assertNull(looking.position); assertEquals("desk", looking.movedFrom)
        assertFalse(FindReply.parse("not json").ok)
        assertEquals("No zone", FindReply.parse("""{"ok":false,"error":"No zone"}""").error)
    }

    @Test fun answersUseTheRelayWordsOrShortOnes() {
        assertEquals("Here.", FindWords.answer(FindReply(ok = true, found = true, answer = "Here."), "orange box"))
        assertEquals("The orange box is by the snack table, 3 metres, ahead, slightly left.",
            FindWords.answer(FindReply(ok = true, found = true, place = "snack table", distanceM = 2.6, bearingDeg = -30.0), "orange box"))
        assertEquals("The keys: 1 metre, turn right.", FindWords.answer(FindReply(ok = true, found = true, distanceM = 1.2, bearingDeg = 90.0), "keys"))
        assertEquals("I haven't seen the keys.", FindWords.answer(FindReply(ok = true, found = false), "keys"))
        assertEquals("I'll look for the orange box.", FindWords.looking("orange box"))
    }

    /** A fake relay and clock: replies in order (an exception is thrown), time moves only when the search waits. */
    private class Script(vararg replies: Any) {
        val queue = ArrayDeque(replies.toList())
        var clock = 0L
        var fetches = 0
        val said = mutableListOf<String>()
        val shown = mutableListOf<FindReply>()
        val waits = mutableListOf<Long>()
        fun search() = FindSearch("orange box",
            fetch = { fetches++; when (val next = if (queue.size > 1) queue.removeFirst() else queue.first()) {
                is Exception -> throw next
                else -> next as FindReply
            } },
            say = { said += it }, shown = { shown += it }, now = { clock },
            sleep = { waits += it; clock += it }, failure = { "Can't reach the laptop." })
    }

    private val looking = FindReply(ok = true, what = "orange box", looking = true)
    private val found = FindReply(ok = true, what = "orange box", found = true, place = "snack table", answer = "The orange box is on the snack table.")

    @Test fun foundAtOnceIsSaidOnce() = runBlocking<Unit> {
        val s = Script(found)
        assertEquals(found, s.search().run())
        assertEquals(1, s.fetches)
        assertEquals(listOf("The orange box is on the snack table."), s.said)
        assertTrue(s.waits.isEmpty())
    }

    @Test fun lookingSaysSoThenAsksEveryThreeSecondsUntilFound() = runBlocking<Unit> {
        val s = Script(looking, looking, found)
        assertEquals(found, s.search().run())
        assertEquals(3, s.fetches)
        assertEquals(listOf("I'll look for the orange box.", "The orange box is on the snack table."), s.said)
        assertEquals(listOf(3_000L, 3_000L), s.waits)
        assertEquals(3, s.shown.size)
    }

    @Test fun lookingGivesUpAfterAMinute() = runBlocking<Unit> {
        val s = Script(looking) // the same answer every time
        assertEquals(looking, s.search().run())
        assertEquals("asks at 0, 3, … 60 s", 21, s.fetches)
        assertEquals(listOf("I'll look for the orange box.", "I couldn't find the orange box yet."), s.said)
        assertEquals(60_000L, s.clock)
    }

    @Test fun aFirstFailureIsSaidAndLaterOnesAreRetried() = runBlocking<Unit> {
        val failing = Script(java.io.IOException("down"))
        assertNull(failing.search().run())
        assertEquals(listOf("Can't reach the laptop."), failing.said)
        assertEquals(1, failing.fetches)
        val flaky = Script(looking, java.io.IOException("blip"), found)
        assertEquals(found, flaky.search().run())
        assertEquals(listOf("I'll look for the orange box.", "The orange box is on the snack table."), flaky.said)
    }

    @Test fun notFoundAndNotLookingIsAnsweredAtOnce() = runBlocking<Unit> {
        val s = Script(FindReply(ok = true, what = "orange box", found = false, looking = false))
        s.search().run()
        assertEquals(listOf("I haven't seen the orange box."), s.said)
        assertEquals(1, s.fetches)
    }
}
