package com.junction.glasses.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

class WearerBrainTest {
    private val zone = ZoneId.of("Europe/Helsinki")
    private fun at(h: Int, m: Int) = ZonedDateTime.of(2026, 11, 14, h, m, 0, 0, zone)
    private val pills = Reminder("r1", "Morning pills", "08:00", "pill box", Reminder.State.DUE)
    private fun room(box: Way? = null, door: Way? = null, seen: List<Sighting> = emptyList()) =
        Fix(true, listOfNotNull(box?.let { Place("pill box", it) }, door?.let { Place("front door", it) }), seen)

    @Test fun dueReminderPointsToItsPlace() {
        val card = WearerBrain.decide(WearerInputs(at(8, 5), listOf(pills), room(box = Way(3.4, 40))))
        assertEquals(Card.Due(pills, Glyph.PILL_BOX, Way(3.4, 40)), card)
    }

    @Test fun anOpenedPillBoxAnswersWithItsTimeNeverWithPillsTaken() {
        val card = WearerBrain.decide(WearerInputs(at(8, 41), sensed = listOf(Sensed("pillbox_open", at(8, 0))),
            asked = Asked(Topic.Pills, at(8, 41).toInstant(), null)))
        assertEquals(Card.Checked(Glyph.PILL_BOX, LocalTime.of(8, 0), true, null), card)
    }

    @Test fun noOpeningTodayPointsToThePillBox() {
        val yesterday = Sensed("pillbox_open", at(8, 0).minusDays(1))
        val card = WearerBrain.decide(WearerInputs(at(8, 41), fix = room(box = Way(4.2, 120)), sensed = listOf(yesterday),
            asked = Asked(Topic.Pills, at(8, 41).toInstant(), null)))
        assertEquals(Card.Checked(Glyph.PILL_BOX, null, false, Way(4.2, 120)), card)
    }

    @Test fun atTheFrontDoorInTheDayItIsTimeToCheckTheThingsToTake() {
        val card = WearerBrain.decide(WearerInputs(at(10, 30), fix = room(door = Way(1.2, 0), seen = listOf(Sighting("keys", "Hall table", "09:12", null)))))
        card as Card.GoingOut
        assertEquals(CheckItem(Glyph.KEY, "Hall table"), card.items.first())
        assertNull(card.items.first { it.glyph == Glyph.WALLET }.near)
    }

    @Test fun aDoorOpeningAtNightAsksAboutBedNotTheChecklist() {
        val card = WearerBrain.decide(WearerInputs(at(3, 11), sensed = listOf(Sensed("door_open", at(3, 10)))))
        assertEquals(Card.Night(LocalTime.of(3, 10)), card)
        // Ten minutes later it is no longer news.
        assertTrue(WearerBrain.decide(WearerInputs(at(3, 25), sensed = listOf(Sensed("door_open", at(3, 10))))) is Card.Idle)
    }

    @Test fun nightOutranksADueReminderAndAReminderOutranksTheChecklist() {
        assertTrue(WearerBrain.decide(WearerInputs(at(3, 11), listOf(pills), sensed = listOf(Sensed("door_open", at(3, 10))))) is Card.Night)
        assertTrue(WearerBrain.decide(WearerInputs(at(10, 30), listOf(pills), room(door = Way(1.0, 0)))) is Card.Due)
    }

    @Test fun aClosedDoorIsReportedAsClosedNeverAsLocked() {
        val card = WearerBrain.decide(WearerInputs(at(8, 20), sensed = listOf(Sensed("door_open", at(8, 14)), Sensed("door_closed", at(8, 15))),
            asked = Asked(Topic.Door, at(8, 20).toInstant(), null)))
        assertEquals(Card.Checked(Glyph.DOOR, LocalTime.of(8, 15), true, null), card)
    }

    @Test fun lostItemsShowWhereTheyWereLastSeen() {
        val keys = Sighting("keys", "Pill box", "09:40", Way(2.5, -70))
        val card = WearerBrain.decide(WearerInputs(at(9, 45), fix = room(seen = listOf(keys)), asked = Asked(Topic.Item("keys"), at(9, 45).toInstant(), null)))
        assertEquals(Card.Found(Glyph.KEY, "Pill box", "09:40", Way(2.5, -70)), card)
    }

    @Test fun answersFadeAndIdleShowsTheNextReminder() {
        val later = Reminder("r2", "Evening pills", "20:00", "pill box", Reminder.State.LATER)
        val stale = Asked(Topic.Pills, at(8, 0).toInstant(), null)
        assertEquals(Card.Idle(LocalTime.of(12, 30), later), WearerBrain.decide(WearerInputs(at(12, 30), listOf(later), asked = stale)))
    }

    @Test fun anUnusableFixGivesNoDirections() {
        val lost = Fix(false, listOf(Place("pill box", Way(1.0, 0))), emptyList())
        assertEquals(Card.Due(pills, Glyph.PILL_BOX, null), WearerBrain.decide(WearerInputs(at(8, 5), listOf(pills), lost)))
    }

    @Test fun questionsAreSortedByTopic() {
        assertEquals(Topic.Pills, WearerBrain.classify("Did I take my pills?"))
        assertEquals(Topic.Door, WearerBrain.classify("Did I lock the door?"))
        assertEquals(Topic.Item("keys"), WearerBrain.classify("Where are my keys?"))
        assertEquals(Topic.Other, WearerBrain.classify("What day is it?"))
        assertEquals("One two three four five six seven eight…", WearerBrain.shorten("One two three four five six seven eight nine ten. Second."))
    }

    @Test fun relayAndTimelineJsonParse() {
        val reminders = WearerJson.reminders("""{"ok":true,"reminders":[{"id":"a","text":"Pills","time":"08:00","place":"pill box","state":"due"},{"id":"b","text":"x","time":"09:00","place":"","state":"weird"}]}""")
        assertEquals(listOf(Reminder("a", "Pills", "08:00", "pill box", Reminder.State.DUE)), reminders)
        val fix = WearerJson.fix("""{"state":"found","waypoints":[{"name":"pill box","distance_m":3.4,"bearing_deg":40.2}],
            "seen":[{"name":"keys","near":"hall","time":"09:12","distance_m":1.5,"bearing_deg":-35,"doubt":false},{"name":"wallet","near":"","doubt":true}]}""")
        assertEquals(Fix(true, listOf(Place("pill box", Way(3.4, 40))), listOf(Sighting("keys", "hall", "09:12", Way(1.5, -35)))), fix)
        assertEquals(false, WearerJson.fix("""{"state":"lost","waypoints":[]}""").usable)
        val sensed = WearerJson.sensed("""{"events":[{"type":"pillbox_open","source":"sensor","ts":"2026-11-14T08:00:00+02:00"},
            {"type":"object_gone","source":"vision","ts":"2026-11-14T09:00:00+02:00"}]}""", zone)
        assertEquals(listOf(Sensed("pillbox_open", at(8, 0))), sensed)
    }
}
