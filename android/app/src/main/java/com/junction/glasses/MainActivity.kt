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
    private lateinit var backendStatus: TextView
    private lateinit var permissionStatus: TextView
    private lateinit var speechStatus: TextView
    private lateinit var diagnosticStatus: TextView
    private var configuredClient: HttpBackendClient? = null
    private var configuredOrigin: String? = null
    private var configuredEvidence: EvidenceClient? = null
    private var configuredDay: DayClient? = null
    private var connectionCheck: Cancelable? = null
    private var connectionGeneration = 0L
    private var diagnosticJob: Job? = null
    private lateinit var deviceStatus: TextView
    private lateinit var motionStatus: TextView
    private lateinit var answerView: TextView
    private lateinit var evidenceView: TextView
    private lateinit var question: EditText
    private lateinit var endpoint: EditText
    private lateinit var token: EditText
    private lateinit var connectButton: Button
    private var shownAnswer: String? = null
    private var lastQuestion: String? = null
    private lateinit var displayTestButton: Button
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
    /** Where the wearer is being walked to, and since when (guidance stops itself after a while). */
    private var guideTo: String? = null
    private var guideSince: Instant? = null
    private var arrivedAt: Instant? = null
    private val guideTicker = object : Runnable {
        override fun run() {
            if (guideTo == null) return
            relay?.where { result -> main.post { if (demoScene < 0 && guideTo != null) { result.getOrNull()?.let { fix = it }; showWearer() } } }
            main.postDelayed(this, GUIDE_REFRESH_MS)
        }
    }
    private var demoScene = -1
    private var demoNow: ZonedDateTime? = null
    private var shownCard: Card? = null
    private var cardJob: Job? = null
    // Spoken cues for audio-only glasses: the last thing said (by key), when, and whether a question's
    // answer is being spoken right now (cues never talk over it).
    private var speakCues = true
    private var lastCueKey: String? = null
    private var lastCueAt = 0L
    private var turnBusy = false
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
        updatePermissionStatus()
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
        label("Gen 2 glasses: spoken answers, with records on your phone. No glasses display required.")
        label("Recorded observations, not confirmation that medication was taken.")
        label("1. Backend setup", 22f)
        label("Use the backend API_TOKEN, not an ElevenLabs key or database URL. Emulator: http://10.0.2.2:8000. USB phone: http://127.0.0.1:8000 with adb reverse.")
        endpoint = field("Backend HTTPS URL").apply {
            if (BuildConfig.DEBUG) setText(if (android.os.Build.FINGERPRINT.contains("generic") || android.os.Build.MODEL.contains("Emulator")) "http://10.0.2.2:8000" else "http://127.0.0.1:8000")
        }
        token = field("Session token — kept only while app is open", true)
        connectButton = button("Connect backend") { connectBackend() }
        backendStatus = label("Backend: not checked.")
        label("2. Ask and listen", 22f)
        status = label("Enter your backend address and session token.")
        speechStatus = label("Speech source: not tested.")
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
        button("Stop") { cancelConnectionCheck(); stopTurn(); glasses.stopCamera(); glasses.stopMotion(); status.text = "Stopped." }
        button("Replay answer") {
            cancelDiagnostic(); stopRecognition(); glasses.stopListening()
            val previous = lastQuestion
            if (previous != null && isNavigationQuestion(previous)) submit(previous) else controller?.replay()
        }
        button("Ask for directions") {
            AlertDialog.Builder(this).setTitle("Get a spoken location estimate")
                .setItems(arrayOf("Where am I?", "Where is the pill box?", "Where is the bathroom?", "Where is the front door?")) { _, index ->
                    val request = listOf("Where am I?", "Where is the pill box?", "Where is the bathroom?", "Where is the front door?")[index]
                    question.setText(request); submit(request)
                }.show()
        }
        label("Directions use the backend’s latest location estimate. Replay re-checks direction requests. No continuous guidance or obstacle detection.")
        answerView = label("Your answer will appear here.", 22f)
        evidenceView = label("")
        label("3. Glasses setup and tests", 22f)
        label("Pair your glasses in Meta AI, enable Developer Mode, then enable access, register and connect below.")
        permissionStatus = label("")
        deviceStatus = label("Not initialized. Enable Developer Mode in Meta AI and pair the glasses.")
        button("Enable glasses access") {
            permissions.launch(arrayOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO))
        }
        button("Register with Meta AI") { glasses.register() }
        button("Connect / reconnect glasses") { glasses.connect() }
        diagnosticStatus = label("Hardware tests: not run. A successful SDK call still needs confirmation on the glasses.")
        val displayOption = CheckBox(this).apply {
            text = "Optional: I am using display glasses"
            isChecked = false
            setOnCheckedChangeListener { _, enabled ->
                stopTurn(); glasses.setDisplayEnabled(enabled); shownAnswer = null
                displayTestButton.visibility = if (enabled) android.view.View.VISIBLE else android.view.View.GONE
                diagnosticStatus.text = "Glasses mode changed. Reconnect glasses; audio and phone answers remain available."
            }
        }
        column.addView(displayOption)
        displayTestButton = button("Test display") { testDisplay() }.apply { visibility = android.view.View.GONE }
        button("Test audio") {
            if (controller == null) status.text = "Connect the backend first to test provider speech and phone fallback."
            else { stopTurn(); lastQuestion = null; controller?.testAudio() }
        }
        label("Audio uses Android’s selected media output. Confirm you hear it and check the phone’s Bluetooth media route.")
        label("Camera is off until requested. Each capture stops it; an unused camera session stops after 60 seconds. Start it again for another photo.")
        button("Start glasses camera") { glasses.startCamera() }
        button("Capture and attach supporting photo") { attachPhoto() }
        button("Stop glasses camera") { glasses.stopCamera() }
        button("Test camera — capture and discard") { testCamera() }
        motionStatus = label("Motion readings are off; no gestures have been inferred.")
        button("Start glasses motion") { glasses.startMotion() }
        button("Stop glasses motion") { glasses.stopMotion() }
        button("Disconnect glasses") { stopTurn(); glasses.disconnect() }
        label("Wearer screen", 22f)
        column.addView(CheckBox(this).apply {
            text = "Speak directions and reminders (for glasses without a display)"
            isChecked = true
            setOnCheckedChangeListener { _, on -> speakCues = on; if (!on) lastCueKey = null }
        })
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
            onGo = ::startGuide
            onStopGuide = ::stopGuide
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
            deviceStatus.text = "Registration: ${s.registration}\nDevices: ${s.deviceCount} · ${s.connection}\nCamera: ${s.camera} · ${displayDescription(s.display)}\nSpeech: ${s.speech} · Motion: ${s.motion}\nGlasses consent — Camera: ${s.cameraPermission}; microphone: ${s.microphonePermission}" + (s.error?.let { "\n$it" } ?: "")
        } }
        lifecycleScope.launch { glasses.motion.collect { reading ->
            motionStatus.text = reading?.let { "Glasses acceleration: %.2f, %.2f, %.2f".format(it.x, it.y, it.z) }
                ?: "Motion readings are off; no gestures have been inferred."
        } }
        lifecycleScope.launch { glasses.transcripts.collect { transcript ->
            glasses.stopListening(); question.setText(transcript); submit(transcript)
        } }
        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED) glasses.initialize()
        updatePermissionStatus()
    }
    private fun cancelConnectionCheck() {
        connectionGeneration++
        if (connectionCheck != null) backendStatus.text = "Backend check cancelled. Tap Connect backend to retry."
        connectionCheck?.cancel(); connectionCheck = null
        connectButton.isEnabled = true
    }
    private fun connectBackend() {
        stopTurn()
        cancelConnectionCheck()
        controller = null
        evidenceClient = null
        latestEvidence = emptyList(); shownAnswer = null; lastQuestion = null
        answerView.text = "Your answer will appear here."; evidenceView.text = ""
        val origin = endpoint.text.toString().trim()
        try {
            val suppliedToken = token.text.toString()
            val client = if (suppliedToken.isBlank() && origin == configuredOrigin) configuredClient
                ?: throw IllegalArgumentException() else HttpBackendClient(origin, suppliedToken, allowLocalHttp = BuildConfig.DEBUG)
            if (suppliedToken.isNotBlank()) {
                configuredEvidence = EvidenceClient(origin, suppliedToken, allowLocalHttp = BuildConfig.DEBUG)
                configuredDay = DayClient(origin, suppliedToken, home, allowLocalHttp = BuildConfig.DEBUG)
            }
            configuredClient = client; configuredOrigin = origin
            token.text.clear()
            val generation = connectionGeneration
            backendStatus.text = "Backend: checking authentication and event API…"
            connectButton.isEnabled = false
            connectionCheck = client.checkConnection { result -> main.post {
                if (generation != connectionGeneration) return@post
                connectionCheck = null; connectButton.isEnabled = true
                result.fold(onSuccess = {
                    evidenceClient = configuredEvidence
                    dayClient = configuredDay
                    controller = VoiceTurnController(client, audio, { work -> if (Looper.myLooper() == main.looper) work() else main.post(work) }, ::renderTurn)
                    backendStatus.text = "Backend: reachable and authenticated at last check."
                    status.text = "Backend connection verified. Ask a question or test audio."
                    refreshWearer()
                }, onFailure = { failure ->
                    backendStatus.text = when ((failure as? BackendFailure)?.status) {
                        401, 403 -> "Backend: token rejected. Enter the backend API_TOKEN and retry."
                        503 -> "Backend: service unavailable. Check the server and retry."
                        else -> if ((failure as? BackendFailure)?.kind == "network")
                            "Backend: unreachable. Check the address, server and network or USB forwarding."
                            else "Backend: unexpected response. Check that this is the event API address."
                    }
                    status.text = "Backend not connected."
                })
            } }
        } catch (_: Exception) {
            backendStatus.text = "Backend: not connected. Use a server origin and API_TOKEN. HTTPS is required except debug loopback."
        }
    }
    private fun updatePermissionStatus() {
        if (!::permissionStatus.isInitialized) return
        fun granted(permission: String) = if (checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED) "allowed" else "not allowed"
        permissionStatus.text = "Phone permissions — Bluetooth: ${granted(Manifest.permission.BLUETOOTH_CONNECT)}; microphone: ${granted(Manifest.permission.RECORD_AUDIO)}; camera: ${granted(Manifest.permission.CAMERA)}. Glasses camera/microphone consent is requested separately when used."
    }
    private fun displayDescription(value: String): String = when (value) {
        "NOT_REQUIRED" -> "Display: not needed (Gen 2 audio mode)"
        "UNSUPPORTED" -> "No display on these glasses; spoken answers remain available"
        else -> "Optional display: $value"
    }
    private fun isNavigationQuestion(value: String) = Regex("\\b(where|directions?|navigate|navigation)\\b|\\bhow (?:do i |can i )?get\\b", RegexOption.IGNORE_CASE).containsMatchIn(value)
    private fun testDisplay() {
        if (!glasses.state.value.displayEnabled) {
            diagnosticStatus.text = "Gen 2 audio mode: no display test needed. Use Test audio."
            return
        }
        stopTurn()
        diagnosticStatus.text = "Display: sending test…"
        diagnosticJob = lifecycleScope.launch {
            val sent = glasses.showAnswer("Display test. Memory with receipts is connected.")
            diagnosticStatus.text = if (sent) "Display: test sent. Confirm that you can see it on the glasses."
                else "Display: test not sent. Register and connect display-capable glasses, then wait for Display: STARTED."
        }
    }
    private fun testCamera() {
        stopTurn()
        diagnosticStatus.text = "Camera: capturing test photo…"
        diagnosticJob = lifecycleScope.launch {
            val photo = glasses.capturePhoto()
            try {
                diagnosticStatus.text = if (photo == null) "Camera: no photo captured. Connect glasses, start the camera and grant camera consent first."
                    else "Camera: captured ${photo.size} bytes; discarded without saving or uploading."
            } finally { photo?.fill(0) }
        }
    }
    private fun submit(text: String) {
        cancelDiagnostic()
        stopRecognition(); glasses.stopListening()
        asked = Asked(WearerBrain.classify(text), wearerNow().toInstant(), null)
        // "Take me to the bathroom": start walking straight away when the relay knows the place.
        (asked?.topic as? Topic.Go)?.let { go -> fix?.let { WearerBrain.resolve(go.words, it) }?.let { startGuide(it); asked = null } }
        if (demoScene >= 0) { showWearer(); return }
        if (controller == null) { status.text = "Connect the backend first."; if (::wearer.isInitialized) wearer.setStatus("Not connected"); return }
        lastQuestion = text
        controller?.submit(text)
    }
    private fun renderTurn(turn: TurnState) {
        renderStatus(turn)
        turnBusy = turn.phase in setOf(TurnPhase.ASKING, TurnPhase.FETCHING_SPEECH, TurnPhase.PLAYING, TurnPhase.FALLBACK)
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
        speechStatus.text = when (turn.speechSource) {
            SpeechSource.NONE -> "Speech source: not used for this turn yet."
            SpeechSource.ELEVENLABS -> "Speech source: ElevenLabs through backend."
            SpeechSource.PHONE -> "Speech source: offline Android fallback."
        } + when (turn.phase) {
            TurnPhase.COMPLETE -> " Playback completed."
            TurnPhase.ERROR -> " Playback or request failed."
            TurnPhase.CANCELLED -> " Stopped."
            else -> ""
        }
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
        if (turn.question == "Audio test") diagnosticStatus.text = "Audio test: ${status.text}\n${speechStatus.text} Confirm where you hear the sound."
    }
    private fun renderAnswer(turn: TurnState) {
        if (turn.phase == TurnPhase.ASKING) {
            displayJob?.cancel(); shownAnswer = null; latestEvidence = emptyList(); answerView.text = "Finding observations…"; evidenceView.text = ""
        }
        turn.answer?.let { answer ->
            latestEvidence = answer.evidence
            answerView.text = answer.text
            evidenceView.text = answer.evidence.joinToString("\n") { "${it.type} · ${it.ts} · ${it.zone}" }
            if (glasses.state.value.displayEnabled && shownAnswer != answer.text && turn.phase != TurnPhase.CANCELLED) {
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
        val selectionGeneration = evidenceGeneration
        val choices = latestEvidence.toList()
        AlertDialog.Builder(this).setTitle("Choose the observation")
            .setItems(choices.map { "${it.type} · ${it.ts}" }.toTypedArray()) { _, index ->
                if (selectionGeneration != evidenceGeneration || client !== evidenceClient) return@setItems
                val event = choices[index]
                AlertDialog.Builder(this).setTitle("Send a supporting photo?")
                    .setMessage("The photo will be sent to your configured backend for this observation. It does not confirm ingestion. Use synthetic test scenes until the team's image handling is approved.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Capture and attach") { _, _ ->
                        if (selectionGeneration != evidenceGeneration || client !== evidenceClient) return@setPositiveButton
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
    private fun cancelDiagnostic() {
        if (diagnosticJob?.isActive == true) diagnosticStatus.text = "Hardware test cancelled. Run the test again when ready."
        diagnosticJob?.cancel(); diagnosticJob = null
    }
    private fun stopTurn() { cancelDiagnostic(); cancelEvidence(); stopRecognition(); displayJob?.cancel(); glasses.stopListening(); controller?.cancel(); audio.stop() }
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
        if (guideTo != null && guideSince?.let { java.time.Duration.between(it, Instant.now()) > GUIDE_LONGEST } == true) stopGuide()
        var card = WearerBrain.decide(WearerInputs(now, reminders, fix, sensed, asked, guideTo))
        if (quietNight && card is Card.Night) card = WearerBrain.decide(WearerInputs(now, reminders, null, emptyList(), asked, guideTo))
        // There: keep the tick on screen a moment, then go back to the usual card.
        if (card is Card.Guide && card.arrived) {
            val first = arrivedAt ?: Instant.now().also { arrivedAt = it }
            if (java.time.Duration.between(first, Instant.now()) > ARRIVED_SHOWN) { stopGuide(); return }
        } else arrivedAt = null
        wearer.setClock(now.toLocalTime())
        wearer.show(card)
        sayCue(card)
        if (card != shownCard) {
            shownCard = card
            cardJob?.cancel()
            cardJob = lifecycleScope.launch { glasses.showCard(card, ::finishReminder, { main.post { startGuide(it) } }, { main.post { stopGuide() } }) }
        }
    }

    /** Say the card's cue once; again only when it changes, at most every few seconds, never over an answer. */
    private fun sayCue(card: Card) {
        val cue = WearerVoice.cue(card)
        if (cue == null) { lastCueKey = null; return }
        val now = android.os.SystemClock.elapsedRealtime()
        if (!speakCues || turnBusy || cue.key == lastCueKey || now - lastCueAt < CUE_GAP_MS) return
        lastCueKey = cue.key; lastCueAt = now
        audio.speakFallback(cue.line) {}
    }

    /** Walk the wearer to a place or item: the card follows each new position fix (every 2 s). */
    private fun startGuide(target: String) {
        guideTo = target; guideSince = Instant.now(); arrivedAt = null; asked = null
        if (target == "bed") nightAnsweredAt = Instant.now()
        main.removeCallbacks(guideTicker); if (demoScene < 0) main.post(guideTicker)
        showWearer()
    }
    private fun stopGuide() {
        guideTo = null; guideSince = null; arrivedAt = null
        main.removeCallbacks(guideTicker)
        showWearer()
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
        guideTo = null; guideSince = null; arrivedAt = null; main.removeCallbacks(guideTicker)
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
        status.text = "Wearer demo scene ${demoScene + 1} of $DEMO_SCENES"
        wearer.setStatus("")
        showWearer()
    }

    override fun onStop() {
        // Meta registration and consent intentionally leave this Activity briefly.
        cancelConnectionCheck(); cancelDiagnostic()
        cancelEvidence(); stopRecognition(); displayJob?.cancel(); controller?.cancel(); audio.stop()
        main.removeCallbacks(refresher); main.removeCallbacks(guideTicker)
        if (!glasses.isExternalFlowPending) { glasses.stopListening(); glasses.suspendLifecycle() }
        super.onStop()
    }
    override fun onResume() {
        super.onResume()
        main.removeCallbacks(refresher); main.post(refresher)
        if (guideTo != null && demoScene < 0) { main.removeCallbacks(guideTicker); main.post(guideTicker) }
        if (::glasses.isInitialized) glasses.onResume()
        updatePermissionStatus()
    }
    private companion object {
        const val DEMO_SCENES = 11
        const val GUIDE_REFRESH_MS = 2_000L
        const val CUE_GAP_MS = 4_000L
        val GUIDE_LONGEST: java.time.Duration = java.time.Duration.ofMinutes(5)
        val ARRIVED_SHOWN: java.time.Duration = java.time.Duration.ofSeconds(8)
    }
    override fun onDestroy() {
        recognizer?.destroy(); glasses.close(); audio.close()
        super.onDestroy()
    }
}
