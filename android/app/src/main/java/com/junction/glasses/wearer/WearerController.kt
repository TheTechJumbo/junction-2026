package com.junction.glasses.wearer

import android.os.Handler
import android.os.SystemClock
import com.junction.glasses.core.*
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The wearer's screen: what it is built from (today's sensor events, the vision relay's reminders and
 * position fix, the last question), which card to show (core/Wearer.kt), and showing it on the phone,
 * on the glasses and, for glasses without a display, as a short spoken cue (core/WearerVoice.kt).
 * It refreshes every 10 s while the app is in front, and every 2 s while walking someone somewhere.
 * The Activity owns the views, the backend connection and the audio; this owns the wearer's state.
 */
class WearerController(
    private val panel: WearerPanel,
    private val main: Handler,
    private val home: ZoneId,
    /** Speak a cue through the phone voice (it reaches the glasses' speakers over Bluetooth). */
    private val speak: (String) -> Unit,
    /** Send a card to the glasses display, when there is one. */
    private val toGlasses: (Card) -> Unit,
) {
    var dayClient: DayClient? = null
    var relay: RelayClient? = null
    /** True while a question's answer is being spoken: cues never talk over it. */
    var turnBusy = false
    var speakCues = true
        set(value) { field = value; if (!value) lastCueKey = null }
    val inDemo get() = demoScene >= 0

    private var reminders: List<Reminder> = emptyList()
    private var fix: Fix? = null
    private var sensed: List<Sensed> = emptyList()
    private var asked: Asked? = null
    private var nightAnsweredAt: Instant? = null
    private var guideTo: String? = null
    private var guideSince: Instant? = null
    private var arrivedAt: Instant? = null
    private var demoScene = -1
    private var demoNow: ZonedDateTime? = null
    private var shownCard: Card? = null
    private var lastCueKey: String? = null
    private var lastCueAt = 0L

    private val refresher = object : Runnable {
        override fun run() { refresh(); main.postDelayed(this, REFRESH_MS) }
    }
    private val guideTicker = object : Runnable {
        override fun run() {
            if (guideTo == null) return
            relay?.where { result -> main.post { if (!inDemo && guideTo != null) { result.getOrNull()?.let { fix = it }; show() } } }
            main.postDelayed(this, GUIDE_REFRESH_MS)
        }
    }

    fun resume() {
        main.removeCallbacks(refresher); main.post(refresher)
        if (guideTo != null && !inDemo) { main.removeCallbacks(guideTicker); main.post(guideTicker) }
    }
    fun pause() { main.removeCallbacks(refresher); main.removeCallbacks(guideTicker) }

    /** Pulls today's sensor observations and, when configured, the relay's reminders and position fix. */
    fun refresh() {
        if (!inDemo) {
            dayClient?.today(LocalDate.now(home)) { result -> result.onSuccess { list -> main.post { if (!inDemo) { sensed = list; show() } } } }
            relay?.reminders { result -> result.onSuccess { list -> main.post { if (!inDemo) { reminders = list; show() } } } }
            relay?.where { result -> main.post { if (!inDemo) { fix = result.getOrNull(); show() } } }
        }
        show()
    }

    /** A question was asked (typed, tapped or spoken). Returns true when the demo answers it alone. */
    fun ask(text: String): Boolean {
        asked = Asked(WearerBrain.classify(text), now().toInstant(), null)
        // "Take me to the bathroom": start walking straight away when the relay knows the place.
        (asked?.topic as? Topic.Go)?.let { go -> fix?.let { WearerBrain.resolve(go.words, it) }?.let { startGuide(it); asked = null } }
        if (inDemo) show()
        return inDemo
    }

    /** The backend answered: its text for the answer card, and its sensor evidence straight away. */
    fun answered(answer: Answer) {
        asked = asked?.copy(answerText = answer.text)
        val fromAnswer = answer.evidence.filter { it.source == "sensor" }.mapNotNull { o ->
            runCatching { Sensed(o.type, ZonedDateTime.parse(o.ts).withZoneSameInstant(home)) }.getOrNull()
        }
        if (!inDemo) sensed = (sensed + fromAnswer).distinct()
        show()
    }

    /** Decides the card and shows it on the phone and the glasses, and says it when it guides or reminds. */
    fun show() {
        val now = now()
        val quietNight = nightAnsweredAt?.let { Duration.between(it, Instant.now()).toMinutes() < 10 } == true
        if (guideTo != null && guideSince?.let { Duration.between(it, Instant.now()) > GUIDE_LONGEST } == true) { stopGuide(); return }
        var card = WearerBrain.decide(WearerInputs(now, reminders, fix, sensed, asked, guideTo))
        if (quietNight && card is Card.Night) card = WearerBrain.decide(WearerInputs(now, reminders, null, emptyList(), asked, guideTo))
        // There: keep the tick on screen a moment, then go back to the usual card.
        if (card is Card.Guide && card.arrived) {
            val first = arrivedAt ?: Instant.now().also { arrivedAt = it }
            if (Duration.between(first, Instant.now()) > ARRIVED_SHOWN) { stopGuide(); return }
        } else arrivedAt = null
        panel.setClock(now.toLocalTime())
        panel.show(card)
        sayCue(card)
        if (card != shownCard) { shownCard = card; toGlasses(card) }
    }

    /** Walk the wearer to a place or item: the card follows each new position fix (every 2 s). */
    fun startGuide(target: String) {
        guideTo = target; guideSince = Instant.now(); arrivedAt = null; asked = null
        if (target == "bed") nightAnsweredAt = Instant.now()
        main.removeCallbacks(guideTicker); if (!inDemo) main.post(guideTicker)
        show()
    }
    fun stopGuide() {
        guideTo = null; guideSince = null; arrivedAt = null
        main.removeCallbacks(guideTicker)
        show()
    }
    fun finishReminder(id: String) {
        reminders = reminders.map { if (it.id == id) it.copy(state = Reminder.State.DONE) else it }
        show()
        relay?.done(id) { main.post { refresh() } }
    }
    /** "Back to bed?" answered: the night card stays quiet for ten minutes. */
    fun dismissNight() { nightAnsweredAt = Instant.now(); show() }

    /** Say the card's cue once; again only when it changes, at most every few seconds, never over an answer. */
    private fun sayCue(card: Card) {
        val cue = WearerVoice.cue(card)
        if (cue == null) { lastCueKey = null; return }
        val now = SystemClock.elapsedRealtime()
        if (!speakCues || turnBusy || cue.key == lastCueKey || now - lastCueAt < CUE_GAP_MS) return
        lastCueKey = cue.key; lastCueAt = now
        speak(cue.line)
    }

    private fun now(): ZonedDateTime = demoNow ?: ZonedDateTime.now(home)
    private fun clear() {
        reminders = emptyList(); fix = null; sensed = emptyList(); asked = null; nightAnsweredAt = null
        guideTo = null; guideSince = null; arrivedAt = null; main.removeCallbacks(guideTicker)
    }

    /** Example scenes for demos and setup checks, without sensors, relay or glasses. Returns the scene number (1-based). */
    fun nextDemoScene(): Int {
        demoScene = (demoScene + 1) % DEMO_SCENES
        val today = ZonedDateTime.now(home).withSecond(0).withNano(0)
        fun at(h: Int, m: Int) = today.withHour(h).withMinute(m)
        val pills = Reminder("demo-pills", "Morning pills", "08:00", WearerBrain.PILL_BOX, Reminder.State.DUE)
        val room = { box: Way?, door: Way?, seen: List<Sighting> ->
            Fix(true, listOfNotNull(box?.let { Place(WearerBrain.PILL_BOX, it) }, door?.let { Place(WearerBrain.FRONT_DOOR, it) }), seen)
        }
        clear()
        demoNow = at(8, 5)
        when (demoScene) {
            0 -> { reminders = listOf(pills); fix = room(Way(3.4, 40), Way(6.0, -150), emptyList()) }
            // Walking to the pill box after Go: the route goes round the table (next corner, then one turn).
            1 -> { reminders = listOf(pills); fix = room(Way(2.6, -35, 1), Way(5.0, 170), emptyList()); guideTo = WearerBrain.PILL_BOX; guideSince = Instant.now() }
            2 -> { demoNow = at(8, 41); sensed = listOf(Sensed("pillbox_open", at(8, 0))); asked = Asked(Topic.Pills, demoNow!!.toInstant(), null) }
            3 -> { demoNow = at(8, 41); fix = room(Way(4.2, 120), null, emptyList()); asked = Asked(Topic.Pills, demoNow!!.toInstant(), null) }
            4 -> { demoNow = at(10, 30); fix = room(null, Way(1.2, 0), listOf(Sighting("keys", "Hall table", "09:12", Way(1.5, 35)), Sighting("wallet", "Kitchen", "08:50", null))) }
            5 -> { demoNow = at(3, 11); sensed = listOf(Sensed("door_open", at(3, 10))) }
            6 -> { demoNow = at(9, 45); fix = room(null, null, listOf(Sighting("keys", "Pill box", "09:40", Way(2.5, -70)))); asked = Asked(Topic.Item("keys"), demoNow!!.toInstant(), null) }
            7 -> { demoNow = at(8, 20); sensed = listOf(Sensed("door_open", at(8, 14)), Sensed("door_closed", at(8, 15))); asked = Asked(Topic.Door, demoNow!!.toInstant(), null) }
            8 -> { demoNow = at(3, 12); sensed = listOf(Sensed("door_open", at(3, 10))); fix = Fix(true, listOf(Place(WearerBrain.FRONT_DOOR, Way(0.6, 0)), Place("bed", Way(6.5, 150, 2))), emptyList()); guideTo = "bed"; guideSince = Instant.now() }
            9 -> { demoNow = at(14, 0); fix = Fix(true, listOf(Place("bathroom", Way(0.5, 10))), emptyList()); guideTo = "bathroom"; guideSince = Instant.now() }
            else -> { demoNow = at(12, 30); reminders = listOf(Reminder("demo-evening", "Evening pills", "20:00", WearerBrain.PILL_BOX, Reminder.State.LATER)) }
        }
        panel.setStatus("")
        show()
        return demoScene + 1
    }
    fun demoOff() { demoScene = -1; demoNow = null; clear(); refresh() }

    companion object {
        const val DEMO_SCENES = 11
        private const val REFRESH_MS = 10_000L
        private const val GUIDE_REFRESH_MS = 2_000L
        private const val CUE_GAP_MS = 4_000L
        private val GUIDE_LONGEST: Duration = Duration.ofMinutes(5)
        private val ARRIVED_SHOWN: Duration = Duration.ofSeconds(8)
    }
}
