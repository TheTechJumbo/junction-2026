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
        assertEquals(Card.Due(pills, Glyph.PILL_BOX, Way(3.4, 40), "pill box"), card)
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
        assertEquals(Card.Checked(Glyph.PILL_BOX, null, false, Way(4.2, 120), "pill box"), card)
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
        assertEquals(Card.Found(Glyph.KEY, "Pill box", "09:40", Way(2.5, -70), "keys"), card)
    }

    @Test fun answersFadeAndIdleShowsTheNextReminder() {
        val later = Reminder("r2", "Evening pills", "20:00", "pill box", Reminder.State.LATER)
        val stale = Asked(Topic.Pills, at(8, 0).toInstant(), null)
        assertEquals(Card.Idle(LocalTime.of(12, 30), later), WearerBrain.decide(WearerInputs(at(12, 30), listOf(later), asked = stale)))
    }

    @Test fun anUnusableFixGivesNoDirections() {
        val lost = Fix(false, listOf(Place("pill box", Way(1.0, 0))), emptyList())
        assertEquals(Card.Due(pills, Glyph.PILL_BOX, null, "pill box"), WearerBrain.decide(WearerInputs(at(8, 5), listOf(pills), lost)))
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

    // ---- Guidance: the way around the furniture, Go, and named places ----

    @Test fun theRelaysRouteAroundFurnitureReplacesTheStraightLine() {
        val fix = WearerJson.fix("""{"state":"found",
            "waypoints":[{"name":"pill box","distance_m":3.1,"bearing_deg":40},{"name":"bathroom","distance_m":5.0,"bearing_deg":-90}],
            "seen":[{"name":"keys","near":"Hall table","time":"09:12","distance_m":2.0,"bearing_deg":10}],
            "route":{"ok":true,"to":{
                "pill box":{"way":"route","length_m":4.2,"corners":2,"next":{"distance_m":1.1,"bearing_deg":-35}},
                "keys":{"way":"straight","length_m":2.0,"corners":0,"next":{"distance_m":2.0,"bearing_deg":10}}}}}""")
        assertEquals(Way(4.2, -35, 2), fix.way("pill box"))
        assertEquals(Way(5.0, -90), fix.way("bathroom")) // no route for it: the straight line
        assertEquals(Way(2.0, 10, 0), fix.way("keys"))
        // A fix without a route (or with route: null) still parses.
        assertEquals(Way(3.1, 40), WearerJson.fix("""{"state":"found","route":null,"waypoints":[{"name":"pill box","distance_m":3.1,"bearing_deg":40}]}""").way("pill box"))
    }

    @Test fun cardsWithAPlaceCarryItSoGoCanWalkThere() {
        val due = WearerBrain.decide(WearerInputs(at(8, 5), listOf(pills), room(box = Way(3.4, 40)))) as Card.Due
        assertEquals("pill box", due.target)
        val notTaken = WearerBrain.decide(WearerInputs(at(8, 41), fix = room(box = Way(4.2, 120)),
            asked = Asked(Topic.Pills, at(8, 41).toInstant(), null))) as Card.Checked
        assertEquals("pill box", notTaken.target)
    }

    @Test fun pillsAlreadyOpenedTodayNeverShowTheWayBack() {
        val card = WearerBrain.decide(WearerInputs(at(9, 0), fix = room(box = Way(2.0, 0)), sensed = listOf(Sensed("pillbox_open", at(8, 0))),
            asked = Asked(Topic.Pills, at(9, 0).toInstant(), null))) as Card.Checked
        assertNull(card.way); assertNull(card.target)
    }

    @Test fun goingSomewhereFollowsTheWayAndSaysWhenThere() {
        val walking = WearerBrain.decide(WearerInputs(at(14, 0), fix = room(box = Way(3.0, 25, 1)), guideTo = "pill box"))
        assertEquals(Card.Guide(Glyph.PILL_BOX, Way(3.0, 25, 1), "pill box", false), walking)
        val there = WearerBrain.decide(WearerInputs(at(14, 1), fix = room(box = Way(0.6, 5)), guideTo = "pill box"))
        assertEquals(true, (there as Card.Guide).arrived)
        val lost = WearerBrain.decide(WearerInputs(at(14, 1), fix = Fix(false, emptyList(), emptyList()), guideTo = "pill box"))
        assertNull((lost as Card.Guide).way)
    }

    @Test fun askingForAnyNamedPlaceUsesTheCarersNames() {
        val home = Fix(true, listOf(Place("Bathroom", Way(5.0, -90)), Place("bed", Way(7.0, 120)), Place("kitchen table", Way(2.0, 0))), emptyList())
        assertEquals(Topic.Go("the loo"), WearerBrain.classify("Where is the loo?"))
        assertEquals("Bathroom", WearerBrain.resolve("the loo", home))
        assertEquals("bed", WearerBrain.resolve((WearerBrain.classify("Take me back to bed") as Topic.Go).words, home))
        assertEquals("kitchen table", WearerBrain.resolve((WearerBrain.classify("How do I get to the kitchen?") as Topic.Go).words, home))
        assertNull(WearerBrain.resolve("garage", home))
        // Pills, door and item questions keep their own cards.
        assertEquals(Topic.Pills, WearerBrain.classify("Where are my pills?"))
        assertEquals(Topic.Item("keys"), WearerBrain.classify("Where are my keys?"))
        val unknown = WearerBrain.decide(WearerInputs(at(14, 0), fix = home, asked = Asked(Topic.Go("garage"), at(14, 0).toInstant(), null)))
        assertEquals(Card.Checked(Glyph.PLACE, null, null, null), unknown)
    }

    @Test fun atNightTheDoorCardShowsTheWayToBedAndGoingThereComesFirst() {
        val home = Fix(true, listOf(Place("front door", Way(0.5, 0)), Place("bedroom", Way(6.0, 150, 2))), emptyList())
        val night = WearerBrain.decide(WearerInputs(at(3, 11), fix = home)) as Card.Night
        assertEquals(Way(6.0, 150, 2), night.way)
        val walking = WearerBrain.decide(WearerInputs(at(3, 11), fix = home, guideTo = "bed"))
        assertEquals(Card.Guide(Glyph.BED, Way(6.0, 150, 2), "bedroom", false), walking)
    }
}
