package com.junction.glasses.core

import org.junit.Assert.*
import org.junit.Test

/** "Take me to …": what the wearer says, the relay's /navigate reply, and how often the glasses speak. */
class NavigationTest {
    private val places = listOf("pill box", "front door", "our table", "light tree", "sponsor booth")

    @Test fun guidancePhrasesFindThePlaceForgivingSmallDifferences() {
        for (said in listOf("Take me to the pill box", "take me to the pillbox", "Where is the pill box?", "How do I get to the pill boxes",
                "Hey Meta, take me to my pill box please", "guide me to pill box", "I want to go to the pill box", "where's the pill box")) {
            assertEquals(said, NavCommand.GoTo("pill box"), NavPhrases.parse(said, places))
        }
        assertEquals(NavCommand.GoTo("front door"), NavPhrases.parse("How do I get to the front door?", places))
        assertEquals(NavCommand.GoTo("our table"), NavPhrases.parse("take me back to our table", places))
        assertEquals(NavCommand.GoTo("light tree"), NavPhrases.parse("take me to the light tree near the stage", places))
        assertEquals("a place said on its own", NavCommand.GoTo("sponsor booth"), NavPhrases.parse("Sponsor booth", places))
    }

    @Test fun questionsThatAreNotGuidanceAreLeftForTheAnswers() {
        for (said in listOf("Did I take my pills?", "Did I lock the door?", "Where are my keys?", "What did I do today?", "")) {
            assertNull(said, NavPhrases.parse(said, places))
        }
        assertNull("nothing known to go to", NavPhrases.parse("take me to the pill box", emptyList()))
        assertNull("too far from any place", NavPhrases.parse("take me to the swimming pool", places))
    }

    @Test fun stopEndsGuidance() {
        for (said in listOf("stop", "Stop.", "cancel", "stop navigation", "never mind", "OK stop")) {
            assertEquals(said, NavCommand.Stop, NavPhrases.parse(said, places))
        }
    }

    @Test fun theRelayReplyIsReadLeniently() {
        val reply = NavReply.parse("""{"ok":true,"to":"pill box","arrived":false,"distance_m":3.2,"bearing_deg":-20,
            "instruction":"pill box, 3 metres ahead, slightly left","route":[[0,0],[1.5,2],[2,3]],"position":[0,0],"heading_deg":30,
            "at":"01:02:03","places":[{"name":"pill box","position":[2,3]},{"name":"broken"}],"plan":{"points":[[0,0],[4,4],["x",1]]}}""")
        assertTrue(reply.ok)
        assertEquals("pill box", reply.to)
        assertEquals(3.2, reply.distanceM!!, 1e-9)
        assertEquals(3, reply.route.size)
        assertEquals(FloorPoint(0.0, 0.0), reply.position)
        assertEquals(listOf(NavPlace("pill box", FloorPoint(2.0, 3.0))), reply.places)
        assertEquals(2, reply.plan.size)
        val refused = NavReply.parse("""{"ok":false,"error":"No place called 'pool' in kitchen."}""")
        assertFalse(refused.ok)
        assertEquals("No place called 'pool' in kitchen.", refused.error)
        assertFalse(NavReply.parse("not json").ok)
    }

    @Test fun directionsAreShortWords() {
        assertEquals("pill box, 3 metres ahead", NavWords.instruction("pill box", 3.2, 4.0))
        assertEquals("pill box, 3 metres ahead, slightly left", NavWords.instruction("pill box", 2.6, -30.0))
        assertEquals("front door, 1 metre, turn right", NavWords.instruction("front door", 1.2, 90.0))
        assertEquals("behind you, turn around", NavWords.direction(170.0))
        assertEquals("less than a metre", NavWords.distance(0.9))
    }

    @Test fun guidanceSpeaksOnChangeOrEveryFourSecondsAndNotOnEveryFix() {
        val guide = NavGuide("pill box")
        fun reply(d: Double, b: Double) = NavReply(ok = true, distanceM = d, bearingDeg = b)
        assertEquals("pill box, 4 metres ahead, slightly left", guide.next(reply(4.0, -30.0), 0))
        assertNull("only the distance changed", guide.next(reply(3.4, -28.0), 1_000))
        assertNull("still the same direction", guide.next(reply(3.0, -25.0), 2_000))
        assertEquals("repeated after 4 s", "pill box, 3 metres ahead, slightly left", guide.next(reply(2.8, -25.0), 4_000))
        assertNull("a new direction waits the minimum gap", guide.next(reply(2.5, 90.0), 5_000))
        assertEquals("pill box, 2 metres, turn right", guide.next(reply(2.4, 90.0), 5_600))
        assertFalse(guide.arrived)
    }

    @Test fun arrivalIsSaidOnceWithinSeventyCentimetresOrWhenTheRelaySaysSo() {
        val guide = NavGuide("pill box")
        assertNotNull(guide.next(NavReply(ok = true, distanceM = 2.0, bearingDeg = 0.0), 0))
        assertEquals("You're there: pill box.", guide.next(NavReply(ok = true, distanceM = 0.6, bearingDeg = 0.0), 1_000))
        assertTrue(guide.arrived)
        assertNull(guide.next(NavReply(ok = true, distanceM = 0.5, bearingDeg = 0.0), 9_000))
        val told = NavGuide("front door")
        assertEquals("You're there: front door.", told.next(NavReply(ok = true, arrived = true, distanceM = 1.5), 0))
        // The relay's own words are used when it gives them.
        val worded = NavGuide("light tree")
        assertEquals("light tree: turn right at the table", worded.next(NavReply(ok = true, distanceM = 5.0, bearingDeg = 0.0,
            instruction = "light tree: turn right at the table"), 0))
    }
}
