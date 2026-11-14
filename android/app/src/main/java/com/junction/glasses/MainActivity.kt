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
import com.junction.glasses.meta.MetaGlassesAdapter
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
    private var pendingPhoneListen = false
    private var recognitionGeneration = 0L
    private var displayJob: Job? = null
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
        button("Stop") { cancelConnectionCheck(); stopTurn(); status.text = "Stopped." }
        button("Replay answer") { cancelDiagnostic(); stopRecognition(); glasses.stopListening(); controller?.replay() }
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
        button("Test display") { testDisplay() }
        button("Test audio") {
            if (controller == null) status.text = "Connect the backend first to test provider speech and phone fallback."
            else { stopTurn(); controller?.testAudio() }
        }
        label("Audio uses Android’s selected media output. Confirm you hear it and check the phone’s Bluetooth media route.")
        button("Start glasses camera") { glasses.startCamera() }
        button("Capture and attach supporting photo") { attachPhoto() }
        button("Stop glasses camera") { glasses.stopCamera() }
        button("Test camera — capture and discard") { testCamera() }
        motionStatus = label("Motion readings are off; no gestures have been inferred.")
        button("Start glasses motion") { glasses.startMotion() }
        button("Stop glasses motion") { glasses.stopMotion() }
        button("Disconnect glasses") { stopTurn(); glasses.disconnect() }
        label("Audio uses Android’s selected media output. Check the Bluetooth output at the venue. Phone recognition may use the device’s speech service; no recordings are saved by this app.")
        setContentView(ScrollView(this).apply {
            addView(column)
            setOnApplyWindowInsetsListener { view, insets ->
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(0, bars.top, 0, bars.bottom)
                insets
            }
        })
        lifecycleScope.launch { glasses.state.collect { s ->
            deviceStatus.text = "Registration: ${s.registration}\nDevices: ${s.deviceCount} · ${s.connection}\nCamera: ${s.camera} · Display: ${s.display}\nSpeech: ${s.speech} · Motion: ${s.motion}\nGlasses consent — Camera: ${s.cameraPermission}; microphone: ${s.microphonePermission}" + (s.error?.let { "\n$it" } ?: "")
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
        latestEvidence = emptyList(); shownAnswer = null
        answerView.text = "Your answer will appear here."; evidenceView.text = ""
        val origin = endpoint.text.toString().trim()
        try {
            val suppliedToken = token.text.toString()
            val client = if (suppliedToken.isBlank() && origin == configuredOrigin) configuredClient
                ?: throw IllegalArgumentException() else HttpBackendClient(origin, suppliedToken, allowLocalHttp = BuildConfig.DEBUG)
            if (suppliedToken.isNotBlank()) {
                configuredEvidence = EvidenceClient(origin, suppliedToken, allowLocalHttp = BuildConfig.DEBUG)
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
                    controller = VoiceTurnController(client, audio, { work -> if (Looper.myLooper() == main.looper) work() else main.post(work) }, ::renderTurn)
                    backendStatus.text = "Backend: reachable and authenticated at last check."
                    status.text = "Backend connection verified. Ask a question or test audio."
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
    private fun testDisplay() {
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
        if (controller == null) { status.text = "Connect the backend first."; return }
        controller?.submit(text)
    }
    private fun renderTurn(turn: TurnState) {
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
    override fun onStop() {
        // Meta registration and consent intentionally leave this Activity briefly.
        cancelConnectionCheck(); cancelDiagnostic()
        cancelEvidence(); stopRecognition(); displayJob?.cancel(); controller?.cancel(); audio.stop()
        if (!glasses.isExternalFlowPending) { glasses.stopListening(); glasses.suspendLifecycle() }
        super.onStop()
    }
    override fun onResume() {
        super.onResume()
        if (::glasses.isInitialized) glasses.onResume()
        updatePermissionStatus()
    }
    override fun onDestroy() {
        recognizer?.destroy(); glasses.close(); audio.close()
        super.onDestroy()
    }
}
