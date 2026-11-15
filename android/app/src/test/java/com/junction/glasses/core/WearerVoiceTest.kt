package com.junction.glasses.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalTime

class WearerVoiceTest {
    private fun guide(way: Way?, arrived: Boolean = false) = Card.Guide(Glyph.PILL_BOX, way, "pill box", arrived)

    @Test fun directionsAreSaidInPlainWords() {
        assertEquals("Pill box: turn left, 3 metres.", WearerVoice.cue(guide(Way(3.2, -90)))!!.line)
        assertEquals("Pill box: straight ahead, almost there.", WearerVoice.cue(guide(Way(0.9, 5)))!!.line)
        assertEquals("You're at the pill box.", WearerVoice.cue(guide(Way(0.5, 0), arrived = true))!!.line)
        assertEquals("Finding where you are.", WearerVoice.cue(guide(null))!!.line)
        assertEquals(listOf("straight ahead", "slightly right", "turn right", "turn around", "slightly left"),
            listOf(0, 45, 100, 170, -30).map(WearerVoice::direction))
    }

    @Test fun walkingOnIsSaidAgainOnlyWhenSomethingWorthSayingChanges() {
        val first = WearerVoice.cue(guide(Way(4.6, 30)))!!.key
        assertEquals(first, WearerVoice.cue(guide(Way(4.1, 35)))!!.key)      // same direction, same distance step
        assertNotEquals(first, WearerVoice.cue(guide(Way(2.9, 35)))!!.key)   // reached the next step (3 m)
        assertNotEquals(first, WearerVoice.cue(guide(Way(4.1, -80)))!!.key)  // new direction
    }

    @Test fun remindersNightAndGoingOutAreSaidButAnswersAreNot() {
        val pills = Reminder("r1", "Morning pills", "08:00", "pill box", Reminder.State.DUE)
        assertEquals("Time for morning pills, the pill box: slightly right, 3 metres.",
            WearerVoice.cue(Card.Due(pills, Glyph.PILL_BOX, Way(3.4, 40), "pill box"))!!.line)
        assertEquals("It's 3:11. Back to bed? Your bed is behind you.",
            WearerVoice.cue(Card.Night(LocalTime.of(3, 11), Way(6.0, 160)))!!.line)
        assertEquals("Going out? Keys: hall table. Wallet: not seen. Lock the door.",
            WearerVoice.cue(Card.GoingOut(listOf(CheckItem(Glyph.KEY, "Hall table"), CheckItem(Glyph.WALLET, null))))!!.line)
        assertNull(WearerVoice.cue(Card.Checked(Glyph.PILL_BOX, LocalTime.of(8, 0), true, null)))
        assertNull(WearerVoice.cue(Card.Said("The door closed at 8:15.")))
    }
}
