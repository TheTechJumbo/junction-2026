package com.junction.glasses

import android.app.AlertDialog
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.text.InputType
import android.view.Gravity
import android.view.WindowManager
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import com.junction.glasses.core.*
import com.junction.glasses.live.CameraFrame
import com.junction.glasses.live.CameraSource
import com.junction.glasses.live.GlassesCamera
import com.junction.glasses.live.LiveRelayLoop
import com.junction.glasses.live.LiveSnapshot
import com.junction.glasses.live.LiveSources
import com.junction.glasses.live.PhoneCamera
import com.junction.glasses.live.RelayClient
import com.junction.glasses.live.RoomRecorder
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import com.junction.glasses.meta.MetaGlassesAdapter
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.Job
import java.util.Locale
import kotlin.math.roundToInt

class MainActivity : ComponentActivity() {
    companion object {
        /** Tests that drive the controls by hand switch this off before creating the Activity. */
        internal var automaticSetup = true
        /** Tests replace the Android Keystore memory with a software key; production leaves this null. */
        internal var backendMemoryOverride: BackendMemory? = null
        private val PHONE_PERMISSIONS = arrayOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        private const val STATE_PERMISSIONS_REQUESTED = "auto_setup_permissions_requested"
        private const val STATE_REGISTRATION_REQUESTED = "auto_setup_registration_requested"
        private const val STATE_CONSENT_REQUESTED = "auto_setup_consent_requested"
        private const val REGISTRATION_SETTLE_MS = 3_000L
    }
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
    private var lastQuestion: String? = null
    private lateinit var displayTestButton: Button
    private var pendingPhoneListen = false
    private var recognitionGeneration = 0L
    private var displayJob: Job? = null
    private lateinit var colors: Palette
    private lateinit var setupStatus: TextView
    private lateinit var backendChip: TextView
    private lateinit var glassesChip: TextView
    private lateinit var micChip: TextView
    private var autoGlasses = AutoGlassesSetup()
    private var autoPermissionsRequested = false
    private var autoPermissionsPending = false
    private var registrationWatchStarted = false
    private var registrationSettled = false
    private var autoRetryJob: Job? = null
    private var backendInterrupted = false
    private lateinit var liveButton: Button
    private lateinit var liveHeadline: TextView
    private lateinit var liveDetails: TextView
    private lateinit var liveThumb: ImageView
    /** Created only when the live camera or a recording starts; no camera object exists before a frame is wanted. */
    private var liveSources: LiveSources<CameraFrame>? = null
    private var cameraSource = CameraSource.PHONE
    private var liveLoop: LiveRelayLoop<CameraFrame>? = null
    private var recordJob: Job? = null
    private lateinit var zoneField: EditText
    private lateinit var secondsField: EditText
    private lateinit var recordButton: Button
    private lateinit var recordStatus: TextView
    private var pendingLiveCamera = false
    private var relayUrl = RelayClient.DEFAULT_URL
    private var relayKey: String? = null
    private var relayZone = RelayClient.DEFAULT_ZONE
    /** DEBUG builds with automatic setup only; release builds never persist the backend. */
    private val backendMemory: BackendMemory? by lazy {
        if (BuildConfig.DEBUG && automaticSetup) backendMemoryOverride ?: BackendMemory.androidKeystore(applicationContext) else null
    }
    private fun stopRecognition() {
        recognitionGeneration++
        pendingPhoneListen = false
        recognizer?.cancel()
    }
    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED) glasses.initialize()
        if (pendingPhoneListen) { pendingPhoneListen = false; startPhoneListening() }
        if (pendingLiveCamera) {
            pendingLiveCamera = false
            if (isGranted(Manifest.permission.CAMERA)) startLiveCamera() else liveDetails.text = "Camera permission is needed for the live camera."
        }
        updatePermissionStatus()
        autoPermissionsPending = false
        continueAutomaticSetup()
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        autoPermissionsRequested = savedInstanceState?.getBoolean(STATE_PERMISSIONS_REQUESTED) == true
        autoGlasses = AutoGlassesSetup(
            registrationRequested = savedInstanceState?.getBoolean(STATE_REGISTRATION_REQUESTED) == true,
            consentRequested = savedInstanceState?.getBoolean(STATE_CONSENT_REQUESTED) == true,
        )
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        audio = AndroidAudioOutput(this)
        glasses = MetaGlassesAdapter(this, lifecycleScope)
        colors = Palette((resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES)
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(12), dp(16), dp(24))
            setBackgroundColor(colors.background)
        }
        var container: LinearLayout = column
        fun label(text: String, size: Float = 16f): TextView = TextView(this).apply {
            this.text = text; textSize = size; setTextColor(colors.text)
            setPadding(0, dp(6), 0, dp(6)); container.addView(this)
        }
        fun note(text: String): TextView = label(text, 13f).apply { setTextColor(colors.secondaryText) }
        fun card(heading: String) {
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(12), dp(16), dp(16))
                background = rounded(colors.surface, 16, colors.cardStroke)
                elevation = dp(2).toFloat()
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                    .apply { topMargin = dp(12) }
            }
            column.addView(card)
            container = card
            label(heading, 20f).setTypeface(null, Typeface.BOLD)
        }
        fun field(hintText: String, secret: Boolean = false): EditText = EditText(this).apply {
            hint = hintText; isSingleLine = true; isSaveEnabled = false
            inputType = if (secret) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
                else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            importantForAutofill = android.view.View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
            setTextColor(colors.text); setHintTextColor(colors.secondaryText)
            container.addView(this)
        }
        fun button(title: String, primary: Boolean = false, action: () -> Unit): Button = Button(this).apply {
            text = title; setOnClickListener { action() }; stylePill(this, primary); container.addView(this)
        }
        label("Memory with receipts", 26f).setTypeface(null, Typeface.BOLD)
        note("Gen 2 glasses: spoken answers, with records on your phone. No glasses display required.")
        note("Recorded observations, not confirmation that medication was taken.")
        card("Status")
        val chips = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(4), 0, dp(4)) }
        container.addView(chips)
        fun chip(): TextView = TextView(this).apply {
            textSize = 13f; gravity = Gravity.CENTER; maxLines = 2
            setPadding(dp(8), dp(6), dp(8), dp(6))
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(6) }
            chips.addView(this)
        }
        backendChip = chip(); glassesChip = chip(); micChip = chip()
        setupStatus = note("Setting up automatically…")
        card("Live camera")
        note("Sends the selected camera to the relay one photo at a time, so the laptop can rebuild the room. Frames go only to the configured relay; nothing is saved on the phone.")
        val sourceGroup = RadioGroup(this).apply { orientation = LinearLayout.HORIZONTAL }
        for (source in CameraSource.values()) {
            sourceGroup.addView(RadioButton(this).apply {
                id = android.view.View.generateViewId()
                text = source.label
                tag = source
                setTextColor(colors.text)
                isChecked = source == cameraSource
            })
        }
        sourceGroup.setOnCheckedChangeListener { group, checkedId ->
            val picked = group.findViewById<RadioButton>(checkedId)?.tag as? CameraSource
            if (picked != null) selectCameraSource(picked)
        }
        container.addView(sourceGroup)
        zoneField = field("Zone (a-z, 0-9, _ or -)").apply { setText(relayZone) }
        liveButton = button("Start live camera", primary = true) { toggleLiveCamera() }
        liveHeadline = label("Live camera is off.", 18f).apply { setTypeface(null, Typeface.BOLD) }
        liveDetails = label("")
        liveThumb = ImageView(this).apply {
            visibility = android.view.View.GONE
            adjustViewBounds = true
            maxHeight = dp(180)
            contentDescription = "Last frame sent to the relay"
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = dp(8) }
            container.addView(this)
        }
        note("Record room takes about 3 photos per second from the selected camera, then replaces the zone's baseline on the relay (what the room should look like). Walk slowly around the room.")
        secondsField = field("Seconds to record (10 to 60)").apply {
            inputType = InputType.TYPE_CLASS_NUMBER
            setText((RoomRecorder.DEFAULT_DURATION_MS / 1000).toString())
        }
        recordButton = button("Record room") { if (recordJob != null) stopRecording("Recording stopped. Nothing was uploaded.") else confirmRecording() }
        recordStatus = label("")
        card("1. Backend setup")
        note("Use the backend API_TOKEN, not an ElevenLabs key or database URL. Emulator: http://10.0.2.2:8000. USB phone: http://127.0.0.1:8000 with adb reverse.")
        endpoint = field("Backend HTTPS URL").apply {
            if (BuildConfig.DEBUG) setText(if (android.os.Build.FINGERPRINT.contains("generic") || android.os.Build.MODEL.contains("Emulator")) "http://10.0.2.2:8000" else "http://127.0.0.1:8000")
        }
        token = field(if (BuildConfig.DEBUG) "Session token — debug builds remember it, encrypted" else "Session token — kept only while app is open", true)
        connectButton = button("Connect backend", primary = true) { connectBackend() }
        backendStatus = label("Backend: not checked.")
        if (BuildConfig.DEBUG) button("Forget saved backend") { forgetBackend() }
        card("2. Ask and listen")
        status = label("Enter your backend address and session token.")
        speechStatus = label("Speech source: not tested.")
        question = field("Ask a question")
        button("Ask", primary = true) { submit(question.text.toString()) }
        button("Use phone microphone") {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                pendingPhoneListen = true; permissions.launch(arrayOf(Manifest.permission.RECORD_AUDIO))
            } else startPhoneListening()
        }
        button("Ask through glasses", primary = true) {
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
        note("Directions use the backend’s latest location estimate. Replay re-checks direction requests. No continuous guidance or obstacle detection.")
        answerView = label("Your answer will appear here.", 22f)
        evidenceView = label("")
        card("3. Glasses setup and tests")
        note("Pair your glasses in Meta AI, enable Developer Mode, then enable access, register and connect below.")
        permissionStatus = label("")
        deviceStatus = label("Not initialized. Enable Developer Mode in Meta AI and pair the glasses.")
        button("Enable glasses access") { permissions.launch(PHONE_PERMISSIONS) }
        button("Register with Meta AI") { glasses.register() }
        button("Connect / reconnect glasses", primary = true) { glasses.connect() }
        diagnosticStatus = label("Hardware tests: not run. A successful SDK call still needs confirmation on the glasses.")
        val displayOption = CheckBox(this).apply {
            text = "Optional: I am using display glasses"
            setTextColor(colors.text)
            isChecked = false
            setOnCheckedChangeListener { _, enabled ->
                stopTurn(); glasses.setDisplayEnabled(enabled); shownAnswer = null
                displayTestButton.visibility = if (enabled) android.view.View.VISIBLE else android.view.View.GONE
                diagnosticStatus.text = "Glasses mode changed. Reconnect glasses; audio and phone answers remain available."
            }
        }
        container.addView(displayOption)
        displayTestButton = button("Test display") { testDisplay() }.apply { visibility = android.view.View.GONE }
        button("Test audio") {
            if (controller == null) status.text = "Connect the backend first to test provider speech and phone fallback."
            else { stopTurn(); lastQuestion = null; controller?.testAudio() }
        }
        note("Audio uses Android’s selected media output. Confirm you hear it and check the phone’s Bluetooth media route.")
        card("Glasses camera and motion")
        note("Camera is off until requested. Each capture stops it; an unused camera session stops after 60 seconds. Start it again for another photo.")
        button("Start glasses camera") { glasses.startCamera() }
        button("Capture and attach supporting photo") { attachPhoto() }
        button("Stop glasses camera") { glasses.stopCamera() }
        button("Test camera — capture and discard") { testCamera() }
        motionStatus = label("Motion readings are off; no gestures have been inferred.")
        button("Start glasses motion") { glasses.startMotion() }
        button("Stop glasses motion") { glasses.stopMotion() }
        button("Disconnect glasses") { stopTurn(); cancelAutomaticRetry(); autoGlasses.userDisconnected(); glasses.disconnect() }
        note("Audio uses Android’s selected media output. Check the Bluetooth output at the venue. Phone recognition may use the device’s speech service; no recordings are saved by this app.")
        setContentView(ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(colors.background)
            addView(column)
            setOnApplyWindowInsetsListener { view, insets ->
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(0, bars.top, 0, bars.bottom)
                insets
            }
        })
        lifecycleScope.launch { glasses.state.collect { s ->
            deviceStatus.text = "Registration: ${s.registration}\nDevices: ${s.deviceCount} · ${s.connection}\nCamera: ${s.camera} · ${displayDescription(s.display)}\nSpeech: ${s.speech} · Motion: ${s.motion}\nGlasses consent — Camera: ${s.cameraPermission}; microphone: ${s.microphonePermission}" + (s.error?.let { "\n$it" } ?: "")
            refreshAutomaticSetup()
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
        backendMemory?.loadRelay()?.let { (url, key, zone) -> relayUrl = url; relayKey = key; relayZone = zone; zoneField.setText(zone) }
        if (!consumeDebugBackendExtras(apply = savedInstanceState == null)) connectRememberedBackend()
        renderRelaySettings()
        startAutomaticSetup()
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        consumeDebugBackendExtras(apply = true)
    }
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(STATE_PERMISSIONS_REQUESTED, autoPermissionsRequested)
        outState.putBoolean(STATE_REGISTRATION_REQUESTED, autoGlasses.registrationRequested)
        outState.putBoolean(STATE_CONSENT_REQUESTED, autoGlasses.consentRequested)
    }
    /**
     * DEBUG builds only: adb extras backend_url/api_token fill the endpoint and token fields as if typed
     * and connect; relay_url/relay_key/relay_zone configure the live camera and are remembered,
     * encrypted. They are removed from the Intent first; a restored Activity ([apply] false) never
     * reuses them. Release builds ignore them entirely. Returns true when backend extras were present.
     */
    private fun consumeDebugBackendExtras(apply: Boolean): Boolean {
        val source: Intent = intent ?: return false
        val extras = DebugBackendLaunch.read(source, BuildConfig.DEBUG) ?: return false
        setIntent(DebugBackendLaunch.withoutExtras(source))
        if (!apply) return false
        if (extras.hasRelay) applyRelaySettings(extras.relayUrl, extras.relayKey, extras.relayZone)
        extras.backendUrl?.let { endpoint.setText(it) }
        extras.apiToken?.let { connectAutomatically(it) }
        return extras.hasBackend
    }
    /** New relay settings from adb replace the remembered ones; a running live camera restarts with them. */
    private fun applyRelaySettings(url: String?, key: String?, zone: String?) {
        url?.let { relayUrl = it }
        key?.let { relayKey = it }
        zone?.let { relayZone = it; zoneField.setText(it) }
        relayKey?.let { backendMemory?.saveRelay(relayUrl, it, relayZone) }
        renderRelaySettings()
        if (liveLoop != null) { stopLiveCamera("Live camera restarting with the new relay settings."); startLiveCamera() }
    }
    /** The relay address and zone, and whether a key is set. The key itself is never shown. */
    private fun renderRelaySettings() {
        if (liveLoop != null) return
        liveDetails.text = "Relay: $relayUrl · zone $relayZone · key ${if (relayKey.isNullOrBlank()) "not set" else "set"}."
    }
    private fun toggleLiveCamera() {
        if (liveLoop != null) stopLiveCamera("Live camera is off.") else startLiveCamera()
    }
    /** Phone or glasses. A running stream or recording takes its next frame from the new source; no automatic fallback. */
    private fun selectCameraSource(source: CameraSource) {
        cameraSource = source
        liveSources?.select(source)
        if (liveLoop != null) liveHeadline.text = "Live camera on · ${source.label} · zone ${zoneField.text.toString().trim()}"
    }
    /** The frame source for the live stream and recordings; it creates the selected camera only when a frame is wanted. */
    private fun sources(): LiveSources<CameraFrame> = liveSources ?: LiveSources<CameraFrame>(cameraSource) { source ->
        when (source) {
            CameraSource.PHONE -> PhoneCamera(this)
            CameraSource.GLASSES -> GlassesCamera(glasses)
        }
    }.also { liveSources = it }
    /** Stops the camera once neither the live stream nor a recording needs it. */
    private fun releaseSources() {
        if (liveLoop != null || recordJob != null) return
        liveSources?.stop()
        liveSources = null
    }
    private fun updateKeepScreenOn() {
        if (liveLoop != null || recordJob != null) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }
    /** The relay client for the zone on the card, or null after saying on [status] what is missing. */
    private fun relayClient(status: TextView): RelayClient? {
        val key = relayKey
        if (key.isNullOrBlank()) {
            status.text = if (BuildConfig.DEBUG) "No relay key yet. Send it with adb: --es relay_key … (see README)."
                else "The live camera needs a debug build with a relay key."
            return null
        }
        val zone = zoneField.text.toString().trim()
        if (!RelayClient.isZone(zone)) {
            status.text = "Zone names use a-z, 0-9, _ and - (for example kitchen)."
            return null
        }
        return try { RelayClient(relayUrl, key, zone, allowLocalHttp = BuildConfig.DEBUG) } catch (_: Exception) {
            status.text = "Relay address not accepted. Use HTTPS (debug builds may use http://127.0.0.1)."
            null
        }
    }
    /** Starts the send loop: capture the newest frame, post it, wait for the reply, repeat. */
    private fun startLiveCamera() {
        if (liveLoop != null) return
        if (recordJob != null) { liveDetails.text = "Recording the room; start the live camera after it."; return }
        val client = relayClient(liveDetails) ?: return
        if (cameraSource == CameraSource.PHONE && !isGranted(Manifest.permission.CAMERA)) {
            pendingLiveCamera = true
            permissions.launch(arrayOf(Manifest.permission.CAMERA))
            return
        }
        val frames = sources()
        liveButton.text = "Stop live camera"
        liveHeadline.text = "Live camera on · ${cameraSource.label} · zone ${zoneField.text.toString().trim()}"
        liveDetails.text = if (cameraSource == CameraSource.GLASSES) "Connecting the glasses camera…" else "Starting the camera…"
        liveLoop = LiveRelayLoop<CameraFrame>(
            scope = lifecycleScope,
            capture = { frames.capture() },
            post = { frame -> client.locate(frame.jpeg) },
            onUpdate = { renderLive(it) },
        ).also { it.start() }
        updateKeepScreenOn()
    }
    private fun stopLiveCamera(message: String) {
        pendingLiveCamera = false
        val wasRunning = liveLoop != null
        liveLoop?.stop(); liveLoop = null
        releaseSources()
        updateKeepScreenOn()
        if (!::liveButton.isInitialized || !wasRunning) return
        liveButton.text = "Start live camera"
        liveHeadline.text = message
    }
    /** Record room replaces the zone's baseline on the relay, so it asks first, naming the zone. */
    private fun confirmRecording() {
        val client = relayClient(recordStatus) ?: return
        val zone = zoneField.text.toString().trim()
        val seconds = (secondsField.text.toString().trim().toLongOrNull() ?: (RoomRecorder.DEFAULT_DURATION_MS / 1000))
            .coerceIn(RoomRecorder.MIN_DURATION_MS / 1000, RoomRecorder.MAX_DURATION_MS / 1000)
        secondsField.setText(seconds.toString())
        if (cameraSource == CameraSource.PHONE && !isGranted(Manifest.permission.CAMERA)) {
            recordStatus.text = "The phone camera needs the camera permission. Allow it, then tap Record room again."
            permissions.launch(arrayOf(Manifest.permission.CAMERA))
            return
        }
        AlertDialog.Builder(this).setTitle("Replace the baseline of zone \"$zone\"?")
            .setMessage("Records $seconds s from the ${cameraSource.label.lowercase(Locale.US)} camera at about 3 photos per second, then replaces " +
                "what the relay knows of how zone \"$zone\" should look. Walk slowly around the room. Photos stay in memory and go only to the relay.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Record") { _, _ -> startRecording(client, zone, seconds) }
            .show()
    }
    private fun startRecording(client: RelayClient, zone: String, seconds: Long) {
        if (recordJob != null) return
        if (liveLoop != null) stopLiveCamera("Live camera paused for the room recording.")
        val frames = sources()
        val recorder = RoomRecorder(capture = { frames.capture().jpeg }, durationMs = seconds * 1000)
        recordButton.text = "Stop recording"
        recordStatus.text = "Recording zone $zone from the ${cameraSource.label.lowercase(Locale.US)} camera…"
        // LAZY: lifecycleScope starts coroutines immediately on the main thread; recordJob must be set first.
        val job = lifecycleScope.launch(start = CoroutineStart.LAZY) {
            try {
                val zip = recorder.record { progress ->
                    recordStatus.text = "Recording zone $zone: ${progress.frames} frames · ${megabytes(progress.bytes)} · " +
                        "${progress.elapsedMs / 1000} of $seconds s" + (if (progress.failedFrames > 0) " · ${progress.failedFrames} frames failed" else "")
                }
                liveSources?.stop()
                liveSources = null
                if (zip.count < RoomRecorder.MIN_FRAMES) {
                    recordStatus.text = "Only ${zip.count} frames were captured; the relay needs at least ${RoomRecorder.MIN_FRAMES}. Nothing was uploaded."
                    return@launch
                }
                val bytes = zip.bytes()
                recordStatus.text = "Uploading ${zip.count} frames (${megabytes(bytes.size)}) as the baseline of zone $zone. The relay rebuilds the room; this can take a minute or more…"
                val reply = client.baseline(bytes, zone)
                recordStatus.text = "Baseline saved for zone ${reply.zone ?: zone}: the relay kept ${reply.framesKept ?: "?"} of ${zip.count} frames."
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                recordStatus.text = "Recording not saved: ${LiveRelayLoop.describe(e)}"
            } finally {
                if (recordJob === coroutineContext[Job]) {
                    recordJob = null
                    recordButton.text = "Record room"
                    releaseSources()
                    updateKeepScreenOn()
                }
            }
        }
        recordJob = job
        updateKeepScreenOn()
        job.start()
    }
    private fun stopRecording(message: String) {
        val job = recordJob ?: return
        recordJob = null
        job.cancel()
        recordButton.text = "Record room"
        recordStatus.text = message
        releaseSources()
        updateKeepScreenOn()
    }
    private fun megabytes(bytes: Int): String = String.format(Locale.US, "%.1f MB", bytes / (1024.0 * 1024.0))
    private fun renderLive(snapshot: LiveSnapshot<CameraFrame>) {
        if (liveLoop == null) return
        // The camera makes a thumbnail at most twice a second; other frames leave the picture as it is.
        snapshot.lastFrame?.thumbnail?.let { liveThumb.setImageBitmap(it); liveThumb.visibility = android.view.View.VISIBLE }
        val reply = snapshot.reply
        liveHeadline.text = reply?.headline?.takeIf { it.isNotBlank() } ?: "Live camera on · waiting for the first reply…"
        val seconds = snapshot.roundTripSeconds?.let { String.format(Locale.US, "%.2f s", it) } ?: "—"
        val rate = snapshot.framesPerSecond?.let { String.format(Locale.US, "%.1f", it) } ?: "—"
        val lines = mutableListOf(listOfNotNull("State: ${reply?.state ?: "—"}", reply?.quick?.summary()).joinToString(" · "))
        lines += "Round trip $seconds · $rate frames/s (last 10 s) · frames sent ${snapshot.framesSent}" +
            " · shaky skipped ${snapshot.lastFrame?.shakySkipped ?: 0}"
        reply?.waypoints?.take(3)?.forEach { lines += "${it.name}: ${String.format(Locale.US, "%.1f", it.distanceM)} m, ${it.bearingDeg}°" }
        snapshot.error?.let { lines += "Error: $it Retrying in 2 s." }
        liveDetails.text = lines.joinToString("\n")
    }
    /** DEBUG builds only: reconnect with the Keystore-encrypted backend remembered from an earlier run. */
    private fun connectRememberedBackend() {
        val (origin, remembered) = backendMemory?.load() ?: return
        endpoint.setText(origin)
        connectAutomatically(remembered)
    }
    /** Same path as typing the token and tapping Connect backend; the token never stays in the field. */
    private fun connectAutomatically(secret: String) {
        token.setText(secret)
        connectBackend()
        token.text.clear()
    }
    private fun forgetBackend() {
        backendMemory?.forget()
        backendStatus.text = "Backend: saved address, token and relay settings forgotten on this phone. The current connection lasts until the app closes."
    }
    private fun isGranted(permission: String) = checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED
    /** Automatic setup step 1: request missing phone permissions once per launch, never again if denied. */
    private fun startAutomaticSetup() {
        if (automaticSetup && !autoPermissionsRequested && PHONE_PERMISSIONS.any { !isGranted(it) }) {
            autoPermissionsRequested = true; autoPermissionsPending = true
            permissions.launch(PHONE_PERMISSIONS)
        } else continueAutomaticSetup()
        renderSetupStatus()
    }
    /** Automatic glasses setup: follows the registration state flow; Meta AI is opened at most once. */
    private fun continueAutomaticSetup() {
        if (automaticSetup && isGranted(Manifest.permission.BLUETOOTH_CONNECT)) {
            glasses.initialize()
            if (!registrationWatchStarted && glasses.state.value.initialized) {
                registrationWatchStarted = true
                lifecycleScope.launch { delay(REGISTRATION_SETTLE_MS); registrationSettled = true; refreshAutomaticSetup() }
            }
        }
        refreshAutomaticSetup()
    }
    private fun refreshAutomaticSetup() {
        advanceAutomaticGlasses()
        renderSetupStatus()
    }
    private fun advanceAutomaticGlasses() {
        if (!automaticSetup || !registrationWatchStarted) return
        val step = autoGlasses.next(glasses.state.value, foreground = lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED),
            externalFlowPending = glasses.isExternalFlowPending, registrationSettled = registrationSettled)
        when (step) {
            AutoGlassesSetup.Step.REGISTER -> glasses.register()
            AutoGlassesSetup.Step.REQUEST_CONSENT -> glasses.requestConsent { autoGlasses.consentFinished(); refreshAutomaticSetup() }
            AutoGlassesSetup.Step.CONNECT -> glasses.connect()
            AutoGlassesSetup.Step.RETRY_LATER -> {
                cancelAutomaticRetry()
                val wait = autoGlasses.retryDelayMs
                autoRetryJob = lifecycleScope.launch { delay(wait); autoGlasses.retryDue(); refreshAutomaticSetup() }
            }
            AutoGlassesSetup.Step.WAIT -> Unit
        }
    }
    private fun cancelAutomaticRetry() { autoRetryJob?.cancel(); autoRetryJob = null }
    private fun renderSetupStatus() {
        if (!::setupStatus.isInitialized) return
        renderStatusChips()
        if (!automaticSetup) { setupStatus.text = "Automatic setup is off. Use the buttons below."; return }
        val phone = when {
            PHONE_PERMISSIONS.all { isGranted(it) } -> "allowed."
            autoPermissionsPending -> "waiting for your answer…"
            else -> "not all allowed. Tap Enable glasses access to ask again."
        }
        val backend = when {
            controller != null -> "connected."
            connectionCheck != null -> "checking…"
            configuredOrigin != null -> "not connected. See the backend status below."
            else -> "enter the address and session token below."
        }
        val s = glasses.state.value
        val glassesStep = when {
            !s.initialized -> if (isGranted(Manifest.permission.BLUETOOTH_CONNECT)) "starting Meta SDK…" else "needs Bluetooth access."
            s.deviceCount == 0 && registrationSettled && s.connection != AutoGlassesSetup.STARTED ->
                "no glasses found, so Meta AI is not opened. Use the glasses buttons when glasses are nearby."
            s.registration != AutoGlassesSetup.REGISTERED ->
                if (autoGlasses.registrationRequested) "approve this app in Meta AI." else "checking Meta AI registration…"
            autoGlasses.consentRunning -> "asking for glasses camera and microphone consent in Meta AI…"
            s.connection == AutoGlassesSetup.STARTED -> "connected."
            autoGlasses.idleReconnect -> "Glasses idle, reconnecting… (the glasses end idle sessions; this is normal)"
            autoGlasses.retryPending -> "Waiting for glasses: put them on / unfold them. Retry ${autoGlasses.retriesUsed} of ${AutoGlassesSetup.MAX_RETRIES} in a few seconds."
            s.connection != "DISCONNECTED" -> "connecting (${s.connection}). Waiting for glasses: put them on / unfold them."
            autoGlasses.retriesUsed >= AutoGlassesSetup.MAX_RETRIES -> "not connected. Put on / unfold the glasses, then tap Connect / reconnect glasses."
            else -> "not connected. Tap Connect / reconnect glasses."
        }
        setupStatus.text = "Setting up automatically:\nPermissions: $phone\nBackend: $backend\nGlasses: $glassesStep" +
            (autoGlasses.lastFailure?.let { "\nLast glasses error: $it" } ?: "")
    }
    private fun renderStatusChips() {
        when {
            controller != null -> paintChip(backendChip, ChipLevel.GOOD, "Backend connected")
            connectionCheck != null -> paintChip(backendChip, ChipLevel.WAIT, "Backend checking…")
            configuredOrigin != null -> paintChip(backendChip, ChipLevel.BAD, "Backend not connected")
            else -> paintChip(backendChip, ChipLevel.WAIT, "Backend not set up")
        }
        val s = glasses.state.value
        when {
            !s.initialized && isGranted(Manifest.permission.BLUETOOTH_CONNECT) -> paintChip(glassesChip, ChipLevel.WAIT, "Glasses starting")
            !s.initialized -> paintChip(glassesChip, ChipLevel.BAD, "Glasses need Bluetooth")
            s.deviceCount == 0 && registrationSettled && s.connection != AutoGlassesSetup.STARTED ->
                paintChip(glassesChip, ChipLevel.WAIT, "No glasses found")
            s.registration != AutoGlassesSetup.REGISTERED -> paintChip(glassesChip, ChipLevel.WAIT, "Glasses not registered")
            s.connection == AutoGlassesSetup.STARTED -> paintChip(glassesChip, ChipLevel.GOOD, "Glasses STARTED")
            automaticSetup && autoGlasses.idleReconnect -> paintChip(glassesChip, ChipLevel.WAIT, "Glasses idle, reconnecting")
            s.connection == "DISCONNECTED" && !autoGlasses.retryPending -> paintChip(glassesChip, ChipLevel.BAD, "Glasses DISCONNECTED")
            else -> paintChip(glassesChip, ChipLevel.WAIT, "Glasses ${s.connection}")
        }
        val phoneMic = isGranted(Manifest.permission.RECORD_AUDIO)
        when {
            s.speech == "STARTED" -> paintChip(micChip, ChipLevel.GOOD, "Mic listening")
            s.microphonePermission == "GRANTED" -> paintChip(micChip, ChipLevel.GOOD, "Mic ready")
            phoneMic -> paintChip(micChip, ChipLevel.WAIT, "Mic needs consent")
            else -> paintChip(micChip, ChipLevel.BAD, "Mic not allowed")
        }
    }
    private fun paintChip(chip: TextView, level: ChipLevel, text: String) {
        chip.text = text
        chip.setTextColor(colors.chipText(level))
        chip.background = rounded(colors.chipFill(level), 14, null)
    }
    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).roundToInt()
    private fun rounded(fill: Int, radiusDp: Int, stroke: Int?): GradientDrawable = GradientDrawable().apply {
        setCornerRadius(dp(radiusDp).toFloat())
        setColor(fill)
        if (stroke != null) setStroke(dp(1), stroke)
    }
    /** Primary actions are filled pills, secondary ones outlined; the button text is never changed. */
    private fun stylePill(button: Button, primary: Boolean) {
        val shape = if (primary) rounded(colors.primary, 22, null) else rounded(Color.TRANSPARENT, 22, colors.outline)
        val mask = rounded(Color.WHITE, 22, null)
        button.background = RippleDrawable(ColorStateList.valueOf(colors.ripple), shape, mask)
        button.setTextColor(ColorStateList(
            arrayOf(intArrayOf(-android.R.attr.state_enabled), intArrayOf()),
            intArrayOf(colors.secondaryText, if (primary) colors.onPrimary else colors.primary),
        ))
        button.isAllCaps = false
        button.textSize = 15f
        button.stateListAnimator = null
        button.minHeight = dp(44)
        button.setPadding(dp(16), dp(8), dp(16), dp(8))
        button.layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(8) }
    }
    private fun cancelConnectionCheck() {
        connectionGeneration++
        if (connectionCheck != null) backendStatus.text = "Backend check cancelled. Tap Connect backend to retry."
        connectionCheck?.cancel(); connectionCheck = null
        connectButton.isEnabled = true
        renderSetupStatus()
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
            }
            configuredClient = client; configuredOrigin = origin
            token.text.clear()
            val rememberToken = suppliedToken.takeIf { it.isNotBlank() }
            val generation = connectionGeneration
            backendStatus.text = "Backend: checking authentication and event API…"
            connectButton.isEnabled = false
            connectionCheck = client.checkConnection { result -> main.post {
                if (generation != connectionGeneration) return@post
                connectionCheck = null; connectButton.isEnabled = true
                result.fold(onSuccess = {
                    evidenceClient = configuredEvidence
                    controller = VoiceTurnController(client, audio, { work -> if (Looper.myLooper() == main.looper) work() else main.post(work) }, ::renderTurn)
                    // DEBUG only: a verified origin and token are remembered, encrypted, for the next launch.
                    if (rememberToken != null) backendMemory?.save(origin, rememberToken)
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
                renderSetupStatus()
            } }
            renderSetupStatus()
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
        if (controller == null) { status.text = "Connect the backend first."; return }
        lastQuestion = text
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
    override fun onStop() {
        // Meta registration and consent intentionally leave this Activity briefly.
        backendInterrupted = connectionCheck != null
        cancelConnectionCheck(); cancelDiagnostic()
        cancelEvidence(); stopRecognition(); displayJob?.cancel(); controller?.cancel(); audio.stop()
        cancelAutomaticRetry()
        stopLiveCamera("Live camera stopped because the app left the screen.")
        stopRecording("Recording stopped because the app left the screen. Nothing was uploaded; record again when ready.")
        if (!glasses.isExternalFlowPending) { glasses.stopListening(); glasses.suspendLifecycle() }
        super.onStop()
    }
    override fun onResume() {
        super.onResume()
        if (::glasses.isInitialized) glasses.onResume()
        updatePermissionStatus()
        // Leaving the foreground disconnects the glasses (onStop); automatic setup reconnects once per return.
        if (::glasses.isInitialized) {
            resumeInterruptedBackendCheck()
            cancelAutomaticRetry()
            autoGlasses.onForeground()
            continueAutomaticSetup()
        }
    }
    /** A backend check cut short by leaving the app (e.g. Meta AI consent) is re-run with the in-memory client. */
    private fun resumeInterruptedBackendCheck() {
        val interrupted = backendInterrupted
        backendInterrupted = false
        if (automaticSetup && interrupted && controller == null && configuredClient != null &&
            token.text.isEmpty() && endpoint.text.toString().trim() == configuredOrigin) connectBackend()
    }
    override fun onDestroy() {
        stopLiveCamera("Live camera is off.")
        stopRecording("Recording stopped.")
        recognizer?.destroy(); glasses.close(); audio.close()
        super.onDestroy()
    }
}

internal enum class ChipLevel { GOOD, WAIT, BAD }

/** Light and dark colours for the programmatic views; the theme switches with the system night mode. */
internal class Palette(private val night: Boolean) {
    val background = if (night) Color.rgb(17, 19, 21) else Color.rgb(242, 245, 246)
    val surface = if (night) Color.rgb(32, 36, 40) else Color.WHITE
    val cardStroke = if (night) Color.rgb(52, 58, 64) else Color.rgb(222, 227, 231)
    val text = if (night) Color.rgb(232, 234, 237) else Color.rgb(32, 33, 36)
    val secondaryText = if (night) Color.rgb(154, 160, 166) else Color.rgb(95, 99, 104)
    val primary = if (night) Color.rgb(138, 180, 248) else Color.rgb(26, 115, 232)
    val onPrimary = if (night) Color.rgb(16, 32, 64) else Color.WHITE
    val outline = if (night) Color.rgb(95, 99, 104) else Color.rgb(189, 193, 198)
    val ripple = if (night) Color.argb(64, 255, 255, 255) else Color.argb(40, 0, 0, 0)
    fun chipFill(level: ChipLevel): Int = when (level) {
        ChipLevel.GOOD -> if (night) Color.rgb(30, 58, 43) else Color.rgb(230, 244, 234)
        ChipLevel.WAIT -> if (night) Color.rgb(61, 52, 32) else Color.rgb(254, 247, 224)
        ChipLevel.BAD -> if (night) Color.rgb(68, 36, 36) else Color.rgb(252, 232, 230)
    }
    fun chipText(level: ChipLevel): Int = when (level) {
        ChipLevel.GOOD -> if (night) Color.rgb(129, 201, 149) else Color.rgb(19, 115, 51)
        ChipLevel.WAIT -> if (night) Color.rgb(253, 214, 99) else Color.rgb(156, 86, 0)
        ChipLevel.BAD -> if (night) Color.rgb(242, 139, 130) else Color.rgb(197, 34, 31)
    }
}
