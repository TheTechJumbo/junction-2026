package com.junction.glasses.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FindPhrasesTest {
    @Test fun findsTheThingAskedFor() {
        val asked = mapOf(
            "find me the orange box" to "orange box", "Find the orange box for me, please." to "orange box",
            "Where is the orange box?" to "orange box", "Hey Meta, where's my orange box?" to "orange box",
            "where did I put the orange box" to "orange box", "Where did I leave my orange box?" to "orange box",
            "can you find the red cup" to "red cup", "I can't find the orange box" to "orange box",
            "have you seen my wallet" to "wallet", "do you know where the orange box is" to "orange box",
            "where can I find the orange box" to "orange box", "look for my keys" to "keys",
        )
        for ((said, thing) in asked) assertEquals(said, thing, FindPhrases.objectIn(said))
        for (said in listOf("where am I", "where was I at 10", "where is it", "what did I do today", "did I take my pills",
            "take me to the snack table", "where have I been today", "stop", "find")) assertNull(said, FindPhrases.objectIn(said))
    }

    @Test fun decidesWhoAnswers() {
        val places = listOf("our table", "snack table", "IronShard booth", "drinks fridge", "exit door")
        assertTrue(FindPhrases.asksRelay("orange box", places, emptyList()))
        assertTrue(FindPhrases.asksRelay("red cup", places, emptyList()))
        assertTrue("a thing the map lists as seen", FindPhrases.asksRelay("orange boxes", places + "orange box", listOf("orange box")))
        assertFalse("a named place keeps the guidance", FindPhrases.asksRelay("snack table", places, emptyList()))
        assertFalse("sounds like a place", FindPhrases.asksRelay("garage", places, emptyList()))
        assertFalse("the backend's own items", FindPhrases.asksRelay("keys", places, emptyList()))
    }

    @Test fun takeMeThere() {
        for (said in listOf("take me there", "Take me there.", "yes, take me there please", "OK take me to it", "guide me there"))
            assertTrue(said, FindPhrases.isTakeMeThere(said))
        for (said in listOf("take me to the snack table", "there", "where is it")) assertFalse(said, FindPhrases.isTakeMeThere(said))
    }

    @Test fun readsTheRelayAnswer() {
        val reply = FindReply.parse("""{"ok":true,"answer":"The orange box is on our table. I last saw it at 10:42.","target":"orange box","confirmed":true,"answered_by":"finder"}""")
        assertEquals("orange box", reply.target)
        assertTrue(reply.confirmed)
        assertEquals("finder", reply.answeredBy)
        assertNull(FindReply.parse("""{"answer":"I have not seen the red cup yet.","target":null}""").target)
        val place = NavReply.parse("""{"ok":true,"places":[{"name":"orange box","position":[1,2],"kind":"object"},{"name":"our table","position":[1,2.2]}]}""").places
        assertEquals("object", place[0].kind)
        assertNull(place[1].kind)
    }
}
