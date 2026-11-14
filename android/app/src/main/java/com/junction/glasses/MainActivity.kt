package com.junction.glasses

import android.app.AlertDialog
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.text.InputType
import android.view.WindowManager
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import com.junction.glasses.core.*
import com.junction.glasses.meta.GlassesCards
import com.junction.glasses.meta.MetaGlassesAdapter
import com.junction.glasses.wearer.WearerPanel
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.coroutines.launch
import kotlinx.coroutines.Job

class MainActivity : ComponentActivity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var glasses: MetaGlassesAdapter
    private lateinit var audio: AndroidAudioOutput
    private var controller: VoiceTurnController? = null
    private var evidenceClient: EvidenceClient? = null
    private var latestEvidence: List<Observation> = emptyList()
    private var evidenceJob: Job? = null
    private var evidenceUpload: Cancelable? = null
    private var evidenceGeneration = 0L
    private var recognizer: SpeechRecognizer? = null
    private lateinit var status: TextView
    private lateinit var deviceStatus: TextView
    private lateinit var motionStatus: TextView
    private lateinit var answerView: TextView
    private lateinit var evidenceView: TextView
    private lateinit var question: EditText
    private lateinit var endpoint: EditText
    private lateinit var token: EditText
    private lateinit var connectButton: Button
    private var shownAnswer: String? = null
    private var pendingPhoneListen = false
    private var recognitionGeneration = 0L
    private var displayJob: Job? = null
    // The wearer's screen and what it is built from (see core/Wearer.kt).
    private lateinit var wearer: WearerPanel
    private lateinit var setup: LinearLayout
    private val home: ZoneId = ZoneId.of("Europe/Helsinki")
    private var dayClient: DayClient? = null
    private var relay: RelayClient? = null
    private var reminders: List<Reminder> = emptyList()
    private var fix: Fix? = null
    private var sensed: List<Sensed> = emptyList()
    private var asked: Asked? = null
    private var nightAnsweredAt: Instant? = null
    private var demoScene = -1
    private var demoNow: ZonedDateTime? = null
    private var shownCard: Card? = null
    private var cardJob: Job? = null
    private val refresher = object : Runnable {
        override fun run() { refreshWearer(); main.postDelayed(this, 10_000) }
    }
    private fun stopRecognition() {
        recognitionGeneration++
        pendingPhoneListen = false
        recognizer?.cancel()
    }
    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED) glasses.initialize()
        if (pendingPhoneListen) { pendingPhoneListen = false; startPhoneListening() }
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        audio = AndroidAudioOutput(this)
        glasses = MetaGlassesAdapter(this, lifecycleScope)
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24)
            setBackgroundColor(Color.rgb(246, 248, 248))
        }
        fun label(text: String, size: Float = 16f): TextView = TextView(this).apply {
            this.text = text; textSize = size; setPadding(0, 12, 0, 8); column.addView(this)
        }
        fun field(hintText: String, secret: Boolean = false): EditText = EditText(this).apply {
            hint = hintText; isSingleLine = true; isSaveEnabled = false
            inputType = if (secret) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
                else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            importantForAutofill = android.view.View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
            column.addView(this)
        }
        fun button(title: String, action: () -> Unit): Button = Button(this).apply {
            text = title; setOnClickListener { action() }; column.addView(this)
        }
        label("Memory with receipts", 26f)
        label("Recorded observations, not confirmation that medication was taken.")
        endpoint = field("Backend HTTPS URL").apply { if (BuildConfig.DEBUG) setText("http://127.0.0.1:8000") }
        token = field("Session token — kept only while app is open", true)
        connectButton = button("Connect backend") { connectBackend() }
        status = label("Enter your backend address and session token.")
        question = field("Ask a question")
        button("Ask") { submit(question.text.toString()) }
        button("Use phone microphone") {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                pendingPhoneListen = true; permissions.launch(arrayOf(Manifest.permission.RECORD_AUDIO))
            } else startPhoneListening()
        }
        button("Ask through glasses") {
            if (controller == null) { status.text = "Connect the backend first." }
            else { stopTurn(); glasses.startListening() }
        }
        button("Stop") { stopTurn(); status.text = "Stopped." }
        button("Replay answer") { stopRecognition(); glasses.stopListening(); controller?.replay() }
        answerView = label("Your answer will appear here.", 22f)
        evidenceView = label("")
        label("Glasses connection", 22f)
        deviceStatus = label("Not initialized. Enable Developer Mode in Meta AI and pair the glasses.")
        button("Enable glasses access") {
            permissions.launch(arrayOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO))
        }
        button("Register with Meta AI") { glasses.register() }
        button("Connect / reconnect glasses") { glasses.connect() }
        button("Start glasses camera") { glasses.startCamera() }
        button("Capture and attach supporting photo") { attachPhoto() }
        button("Stop glasses camera") { glasses.stopCamera() }
        button("Capture test photo — do not upload") {
            lifecycleScope.launch {
                val photo = glasses.capturePhoto()
                status.text = if (photo == null) "No photo captured. Check camera access and connection."
                    else "Captured ${photo.size} bytes in memory; discarded without upload."
                photo?.fill(0)
            }
        }
        motionStatus = label("Motion readings are off; no gestures have been inferred.")
        button("Start glasses motion") { glasses.startMotion() }
        button("Stop glasses motion") { glasses.stopMotion() }
        button("Disconnect glasses") { stopTurn(); glasses.disconnect() }
        label("Wearer screen", 22f)
        val relayUrl = field("Vision relay URL (optional)").apply { if (BuildConfig.DEBUG) setText("http://127.0.0.1:8090") }
        val relayKey = field("Vision relay key", true)
        button("Connect vision relay") {
            relay = try { RelayClient(relayUrl.text.toString(), relayKey.text.toString(), allowLocalHttp = BuildConfig.DEBUG) }
                catch (_: Exception) { null }
            relayKey.text.clear()
            status.text = if (relay == null) "Use the relay origin and its key." else "Vision relay configured for reminders and directions."
            refreshWearer()
        }
        button("Wearer demo: next scene") { nextDemoScene() }
        button("Wearer demo: off") { demoScene = -1; demoNow = null; reminders = emptyList(); fix = null; sensed = emptyList(); asked = null; refreshWearer() }
        label("Audio uses Android’s selected media output. Check the Bluetooth output at the venue. Phone recognition may use the device’s speech service; no recordings are saved by this app.")
        setup = column.apply { visibility = android.view.View.GONE }
        wearer = WearerPanel(this).apply {
            onAsk = { q -> question.setText(q); submit(q) }
            onMic = {
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    pendingPhoneListen = true; permissions.launch(arrayOf(Manifest.permission.RECORD_AUDIO))
                } else startPhoneListening()
            }
            onDone = ::finishReminder
            onDismiss = { nightAnsweredAt = Instant.now(); showWearer() }
            onSetup = { setup.visibility = if (setup.visibility == android.view.View.VISIBLE) android.view.View.GONE else android.view.View.VISIBLE }
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(WearerPanel.NIGHT); isFillViewport = true
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.VERTICAL; setBackgroundColor(WearerPanel.NIGHT)
                addView(wearer); addView(setup)
            })
            setOnApplyWindowInsetsListener { view, insets ->
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(0, bars.top, 0, bars.bottom)
                insets
            }
        })
        androidx.core.view.WindowCompat.getInsetsController(window, window.decorView).apply { isAppearanceLightStatusBars = false; isAppearanceLightNavigationBars = false }
        lifecycleScope.launch { glasses.state.collect { s ->
            deviceStatus.text = "Registration: ${s.registration}\nDevices: ${s.deviceCount} · ${s.connection}\nCamera: ${s.camera} · Display: ${s.display}\nSpeech: ${s.speech} · Motion: ${s.motion}" + (s.error?.let { "\n$it" } ?: "")
        } }
        lifecycleScope.launch { glasses.motion.collect { reading ->
            motionStatus.text = reading?.let { "Glasses acceleration: %.2f, %.2f, %.2f".format(it.x, it.y, it.z) }
                ?: "Motion readings are off; no gestures have been inferred."
        } }
        lifecycleScope.launch { glasses.transcripts.collect { transcript ->
            glasses.stopListening(); question.setText(transcript); submit(transcript)
        } }
        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED) glasses.initialize()
    }
    private fun connectBackend() {
        stopTurn()
        controller = null
        evidenceClient = null
        try {
            val client = HttpBackendClient(endpoint.text.toString().trim(), token.text.toString(), allowLocalHttp = BuildConfig.DEBUG)
            evidenceClient = EvidenceClient(endpoint.text.toString().trim(), token.text.toString(), allowLocalHttp = BuildConfig.DEBUG)
            dayClient = DayClient(endpoint.text.toString().trim(), token.text.toString(), home, allowLocalHttp = BuildConfig.DEBUG)
            controller = VoiceTurnController(client, audio, { work -> if (Looper.myLooper() == main.looper) work() else main.post(work) }, ::renderTurn)
            token.text.clear()
            status.text = "Backend configured. Ask a question to test the connection."
            refreshWearer()
        } catch (_: Exception) { status.text = "Use an HTTPS origin and nonempty session token. Debug builds also support USB loopback." }
    }
    private fun submit(text: String) {
        stopRecognition(); glasses.stopListening()
        asked = Asked(WearerBrain.classify(text), wearerNow().toInstant(), null)
        if (demoScene >= 0) { showWearer(); return }
        if (controller == null) { status.text = "Connect the backend first."; if (::wearer.isInitialized) wearer.setStatus("Not connected"); return }
        controller?.submit(text)
    }
    private fun renderTurn(turn: TurnState) {
        renderStatus(turn)
        if (::wearer.isInitialized) wearer.setStatus(when (turn.phase) {
            TurnPhase.ASKING -> "…"; TurnPhase.ERROR -> "No answer. Try again."; else -> ""
        })
        turn.answer?.let { answer ->
            asked = asked?.copy(answerText = answer.text)
            // The answer's own sensor evidence counts even before the next timeline refresh.
            val fromAnswer = answer.evidence.filter { it.source == "sensor" }.mapNotNull { o ->
                runCatching { Sensed(o.type, ZonedDateTime.parse(o.ts).withZoneSameInstant(home)) }.getOrNull()
            }
            if (demoScene < 0) sensed = (sensed + fromAnswer).distinct()
            showWearer()
        }
        renderAnswer(turn)
    }
    private fun renderStatus(turn: TurnState) {
        status.text = turn.message ?: when (turn.phase) {
            TurnPhase.IDLE -> "Ready"
            TurnPhase.ASKING -> "Finding recorded observations…"
            TurnPhase.FETCHING_SPEECH -> "Preparing speech…"
            TurnPhase.PLAYING -> "Speaking"
            TurnPhase.FALLBACK -> "Using offline phone speech"
            TurnPhase.COMPLETE -> "Ready for another question"
            TurnPhase.ERROR -> "Could not complete the question"
            TurnPhase.CANCELLED -> "Stopped"
        }
    }
    private fun renderAnswer(turn: TurnState) {
        if (turn.phase == TurnPhase.ASKING) {
            displayJob?.cancel(); shownAnswer = null; latestEvidence = emptyList(); answerView.text = "Finding observations…"; evidenceView.text = ""
        }
        turn.answer?.let { answer ->
            latestEvidence = answer.evidence
            answerView.text = answer.text
            evidenceView.text = answer.evidence.joinToString("\n") { "${it.type} · ${it.ts} · ${it.zone}" }
            if (shownAnswer != answer.text && turn.phase != TurnPhase.CANCELLED) {
                shownAnswer = answer.text
                displayJob?.cancel()
                displayJob = lifecycleScope.launch { glasses.showAnswer(answer.text) }
            }
        }
    }
    private fun cancelEvidence() {
        evidenceGeneration++
        evidenceJob?.cancel(); evidenceJob = null
        evidenceUpload?.cancel(); evidenceUpload = null
    }
    private fun attachPhoto() {
        val client = evidenceClient
        if (client == null || latestEvidence.isEmpty()) {
            status.text = "Ask about a recorded observation first, then select it for the photo."
            return
        }
        val choices = latestEvidence.toList()
        AlertDialog.Builder(this).setTitle("Choose the observation")
            .setItems(choices.map { "${it.type} · ${it.ts}" }.toTypedArray()) { _, index ->
                val event = choices[index]
                AlertDialog.Builder(this).setTitle("Send a supporting photo?")
                    .setMessage("The photo will be sent to your configured backend for this observation. It does not confirm ingestion. Use synthetic test scenes until the team's image handling is approved.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Capture and attach") { _, _ ->
                        cancelEvidence()
                        val id = evidenceGeneration
                        evidenceJob = lifecycleScope.launch {
                            val bytes = glasses.capturePhoto()
                            if (bytes == null) { status.text = "Capture unavailable. Start the glasses camera first."; return@launch }
                            try {
                                if (id == evidenceGeneration) evidenceUpload = client.uploadJpeg(event.id, bytes) { result -> main.post {
                                    if (id == evidenceGeneration) status.text = if (result.isSuccess) "Supporting photo attached. Ask again to refresh the observation." else "Photo not attached. Check the connection and try again."
                                } }
                            } finally { bytes.fill(0) }
                        }
                    }.show()
            }.show()
    }
    private fun stopTurn() { cancelEvidence(); stopRecognition(); displayJob?.cancel(); glasses.stopListening(); controller?.cancel(); audio.stop() }
    private fun startPhoneListening() {
        if (controller == null) { status.text = "Connect the backend first."; return }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            status.text = "Microphone permission is needed. You can still type a question."; return
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) { status.text = "Phone speech recognition unavailable. Type your question."; return }
        stopTurn(); recognizer?.destroy()
        val recognitionId = recognitionGeneration
        recognizer = SpeechRecognizer.createSpeechRecognizer(this).also { speech ->
            speech.setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) { if (recognitionId == recognitionGeneration) status.text = "Listening through phone speech service…" }
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() { if (recognitionId == recognitionGeneration) status.text = "Transcribing…" }
                override fun onError(error: Int) { if (recognitionId == recognitionGeneration) status.text = "Speech not recognized. Retry or type your question." }
                override fun onResults(results: Bundle?) {
                    if (recognitionId != recognitionGeneration) return
                    val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                    if (!text.isNullOrBlank()) { question.setText(text); submit(text) }
                }
                override fun onPartialResults(partialResults: Bundle?) {}
                override fun onEvent(eventType: Int, params: Bundle?) {}
            })
            speech.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
                putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            })
        }
    }
    private fun wearerNow(): ZonedDateTime = demoNow ?: ZonedDateTime.now(home)

    /** Pulls today's sensor observations and, when configured, the relay's reminders and position fix. */
    private fun refreshWearer() {
        if (demoScene < 0) {
            dayClient?.today(LocalDate.now(home)) { result -> result.onSuccess { list -> main.post { if (demoScene < 0) { sensed = list; showWearer() } } } }
            relay?.reminders { result -> result.onSuccess { list -> main.post { if (demoScene < 0) { reminders = list; showWearer() } } } }
            relay?.where { result -> main.post { if (demoScene < 0) { fix = result.getOrNull(); showWearer() } } }
        }
        showWearer()
    }

    /** Decides the card and shows it on the phone and, when connected, on the glasses. */
    private fun showWearer() {
        if (!::wearer.isInitialized) return
        val now = wearerNow()
        val quietNight = nightAnsweredAt?.let { java.time.Duration.between(it, Instant.now()).toMinutes() < 10 } == true
        var card = WearerBrain.decide(WearerInputs(now, reminders, fix, sensed, asked, null))
        if (quietNight && card is Card.Night) card = WearerBrain.decide(WearerInputs(now, reminders, null, emptyList(), asked, null))
        wearer.setClock(now.toLocalTime())
        wearer.show(card)
        if (card != shownCard) {
            shownCard = card
            cardJob?.cancel()
            cardJob = lifecycleScope.launch { glasses.showCard(card, ::finishReminder) }
        }
    }

    private fun finishReminder(id: String) {
        reminders = reminders.map { if (it.id == id) it.copy(state = Reminder.State.DONE) else it }
        showWearer()
        relay?.done(id) { main.post { refreshWearer() } }
    }

    /** Example scenes for demos and setup checks, without sensors, relay or glasses. */
    private fun nextDemoScene() {
        demoScene = (demoScene + 1) % DEMO_SCENES
        val today = ZonedDateTime.now(home).withSecond(0).withNano(0)
        fun at(h: Int, m: Int) = today.withHour(h).withMinute(m)
        val pills = Reminder("demo-pills", "Morning pills", "08:00", WearerBrain.PILL_BOX, Reminder.State.DUE)
        val room = { box: Way?, door: Way?, seen: List<Sighting> ->
            Fix(true, listOfNotNull(box?.let { Place(WearerBrain.PILL_BOX, it) }, door?.let { Place(WearerBrain.FRONT_DOOR, it) }), seen)
        }
        reminders = emptyList(); fix = null; sensed = emptyList(); asked = null; nightAnsweredAt = null
        demoNow = at(8, 5)
        when (demoScene) {
            0 -> { reminders = listOf(pills); fix = room(Way(3.4, 40), Way(6.0, -150), emptyList()) }
            1 -> { reminders = listOf(pills); fix = room(Way(0.8, -8), Way(5.0, 170), emptyList()) }
            2 -> { demoNow = at(8, 41); sensed = listOf(Sensed("pillbox_open", at(8, 0))); asked = Asked(Topic.Pills, demoNow!!.toInstant(), null) }
            3 -> { demoNow = at(8, 41); fix = room(Way(4.2, 120), null, emptyList()); asked = Asked(Topic.Pills, demoNow!!.toInstant(), null) }
            4 -> { demoNow = at(10, 30); fix = room(null, Way(1.2, 0), listOf(Sighting("keys", "Hall table", "09:12", Way(1.5, 35)), Sighting("wallet", "Kitchen", "08:50", null))) }
            5 -> { demoNow = at(3, 11); sensed = listOf(Sensed("door_open", at(3, 10))) }
            6 -> { demoNow = at(9, 45); fix = room(null, null, listOf(Sighting("keys", "Pill box", "09:40", Way(2.5, -70)))); asked = Asked(Topic.Item("keys"), demoNow!!.toInstant(), null) }
            7 -> { demoNow = at(8, 20); sensed = listOf(Sensed("door_open", at(8, 14)), Sensed("door_closed", at(8, 15))); asked = Asked(Topic.Door, demoNow!!.toInstant(), null) }
            else -> { demoNow = at(12, 30); reminders = listOf(Reminder("demo-evening", "Evening pills", "20:00", WearerBrain.PILL_BOX, Reminder.State.LATER)) }
        }
        status.text = "Wearer demo scene ${demoScene + 1} of $DEMO_SCENES"
        wearer.setStatus("")
        showWearer()
    }

    override fun onStop() {
        // Meta registration and consent intentionally leave this Activity briefly.
        cancelEvidence(); stopRecognition(); displayJob?.cancel(); controller?.cancel(); audio.stop()
        main.removeCallbacks(refresher)
        if (!glasses.isExternalFlowPending) { glasses.stopListening(); glasses.suspendLifecycle() }
        super.onStop()
    }
    override fun onResume() {
        super.onResume()
        main.removeCallbacks(refresher); main.post(refresher)
        if (::glasses.isInitialized) glasses.onResume()
    }
    private companion object { const val DEMO_SCENES = 9 }
    override fun onDestroy() {
        recognizer?.destroy(); glasses.close(); audio.close()
        super.onDestroy()
    }
}
