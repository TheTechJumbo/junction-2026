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
import com.junction.glasses.live.FileUploadBody
import com.junction.glasses.live.GlassesCamera
import com.junction.glasses.live.GlassesVideo
import com.junction.glasses.live.GlassesVideoLibrary
import com.junction.glasses.live.GlassesVideoWatch
import com.junction.glasses.live.LiveRelayLoop
import com.junction.glasses.live.LiveSnapshot
import com.junction.glasses.live.LiveSources
import com.junction.glasses.live.PhoneCamera
import com.junction.glasses.live.PhoneVideoRecorder
import com.junction.glasses.live.RecordingNames
import com.junction.glasses.live.RecordingReply
import com.junction.glasses.live.RecordingUploader
import com.junction.glasses.live.RelayClient
import com.junction.glasses.live.RoomRecorder
import com.junction.glasses.live.FramePump
import com.junction.glasses.live.AlwaysOn
import com.junction.glasses.live.AlwaysOnRules
import com.junction.glasses.live.AlwaysOnStatus
import com.junction.glasses.live.SizeSettle
import com.junction.glasses.live.StreamUploadBody
import com.junction.glasses.live.UploadOutcome
import android.database.ContentObserver
import android.os.Build
import android.os.SystemClock
import java.io.File
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import com.junction.glasses.meta.MetaGlassesAdapter
import kotlinx.coroutines.delay
import com.junction.glasses.meta.GlassesCards
import com.junction.glasses.wearer.WearerPanel
import com.junction.glasses.wearer.WearerController
import com.junction.glasses.wearer.WearerMap
import com.junction.glasses.live.FailoverRelay
import com.junction.glasses.live.ChunkDrain
import com.junction.glasses.live.ChunkQueue
import com.junction.glasses.live.RelayFailure
import java.time.LocalDate
import java.time.ZonedDateTime
import com.junction.glasses.core.RelayClient as WearerRelayClient
import kotlinx.coroutines.launch
import kotlinx.coroutines.Job
import java.util.Locale
import kotlin.math.roundToInt

class MainActivity : ComponentActivity() {
    companion object {
        /** Tests that drive the controls by hand switch this off before creating the Activity. */
        internal var automaticSetup = true
        /** Tests switch off the always-on recording's automatic start (it would start a foreground service). */
        internal var alwaysOnAutoStart = true
        /** Tests replace the Android Keystore memory with a software key; production leaves this null. */
        internal var backendMemoryOverride: BackendMemory? = null
        private val PHONE_PERMISSIONS = arrayOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        private const val STATE_PERMISSIONS_REQUESTED = "auto_setup_permissions_requested"
        private const val STATE_REGISTRATION_REQUESTED = "auto_setup_registration_requested"
        private const val STATE_CONSENT_REQUESTED = "auto_setup_consent_requested"
        private const val STATE_GLASSES_APP_PAGE_OPENED = "auto_setup_glasses_app_page_opened"
        private const val REGISTRATION_SETTLE_MS = 3_000L
        /** How long a new glasses video may keep growing while Meta AI imports it. */
        private const val SETTLE_LIMIT_MS = 5 * 60 * 1000L
        /** Record room waits this long for the camera's first frame (the glasses camera can take a while). */
        private const val FIRST_FRAME_WAIT_MS = 45_000L
        /** How often guidance asks the relay for the way while walking. */
        private const val NAV_POLL_MS = 1_000L
        private const val VENUE_PREFS = "venue"
        private const val VENUE_ZONE = "zone"
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
    // The glasses by default; the phone camera only when picked (always-on recording, live camera and Record room).
    private var cameraSource = CameraSource.GLASSES
    private var liveLoop: LiveRelayLoop<CameraFrame>? = null
    private var recordJob: Job? = null
    private lateinit var zoneField: EditText
    private lateinit var secondsField: EditText
    private lateinit var recordButton: Button
    private lateinit var recordStatus: TextView
    private var pendingLiveCamera = false
    private lateinit var videoButton: Button
    private lateinit var uploadStatus: TextView
    private lateinit var sendWhenDone: Switch
    private lateinit var keepOnPhone: Switch
    private lateinit var autoSendGlasses: Switch
    private lateinit var phoneVideosList: LinearLayout
    private lateinit var glassesVideosList: LinearLayout
    /** Created only when "Record video" starts with the Phone source. */
    private var videoRecorder: PhoneVideoRecorder? = null
    private var videoFile: File? = null
    private var uploadJob: Job? = null
    private val glassesWatch = GlassesVideoWatch(System.currentTimeMillis() / 1000)
    private var glassesLibrary: GlassesVideoLibrary? = null
    private var glassesObserver: ContentObserver? = null
    private var glassesCheckJob: Job? = null
    private var armTimeoutJob: Job? = null
    private var armedZone: String? = null
    private var pendingMediaAction: (() -> Unit)? = null
    private var relayUrl = RelayClient.DEFAULT_URL
    /** The laptop's Wi-Fi address for the relay, tried when 127.0.0.1 (USB) does not answer. */
    private var relayLanUrl: String? = null
    private lateinit var alwaysOnButton: Button
    private lateinit var alwaysOnText: TextView
    private var pendingAlwaysOn = false
    private var notificationsAsked = false
    private var relayKey: String? = null
    private var relayZone = RelayClient.DEFAULT_ZONE
    /** DEBUG builds with automatic setup only; release builds never persist the backend. */
    private val backendMemory: BackendMemory? by lazy {
        if (BuildConfig.DEBUG && automaticSetup) backendMemoryOverride ?: BackendMemory.androidKeystore(applicationContext) else null
    }
    // The wearer's screen (wearer/WearerController.kt owns what it shows).
    private lateinit var wearer: WearerPanel
    private lateinit var screen: WearerController
    private lateinit var setup: LinearLayout
    private val home: ZoneId = ZoneId.of("Europe/Helsinki")
    private var cardJob: Job? = null
    /** The wearer's map refreshes every 10 s while the app is in front (the screen refreshes itself). */
    private val mapRefresher = object : Runnable {
        override fun run() { refreshMap(); main.postDelayed(this, 10_000) }
    }
    // The wearer's map and "take me to …" guidance (core/Navigation.kt, wearer/WearerMap.kt).
    private lateinit var navMap: WearerMap
    /** The debug panel while it is open (refreshed every 2 s), and its Retry uploads run. */
    private var debugDialog: AlertDialog? = null
    private var debugDrainJob: Job? = null
    private var navGuide: NavGuide? = null
    private var navJob: Job? = null
    /** The zone's place names, for matching what the wearer says; refreshed with the map. */
    private var knownPlaces: List<String> = emptyList()
    /** The live camera was started for guidance (to keep fixes coming) and stops with it. */
    private var navStartedLive = false
    private var navRelayFor: String? = null
    private var navRelayCached: FailoverRelay<RelayClient>? = null
    // "Find me X" (core/FindMe.kt): the running search, the thing asked for, whether and where it was found.
    private var findJob: Job? = null
    private var findWhat: String? = null
    private var findFound = false
    private var foundItem: NavPlace? = null
    /** The newest map reply, so a found thing can be drawn without asking the relay again. */
    private var lastMapReply: NavReply? = null
    private lateinit var takeMeThere: Button
    /**
     * The venue the map and guidance use (debug extra venue_zone, remembered), apart from the zone field
     * that recordings go to, so recordings can go to a test zone while the wearer is guided in the venue.
     */
    private val venueZone: String? get() = getSharedPreferences(VENUE_PREFS, MODE_PRIVATE).getString(VENUE_ZONE, null)
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
        if (pendingAlwaysOn) { pendingAlwaysOn = false; startAlwaysOn(auto = false) }
        pendingMediaAction?.let { action ->
            pendingMediaAction = null
            if (isGranted(mediaPermission())) action() else uploadStatus.text = "Allow access to videos so the app can send glasses videos."
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
            glassesAppPageOpened = savedInstanceState?.getBoolean(STATE_GLASSES_APP_PAGE_OPENED) == true,
        )
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        audio = AndroidAudioOutput(this)
        // The app's glasses adapter: a fresh one per Activity, except while the always-on recording runs.
        glasses = GlassesHub.forActivity(this, keepSession = AlwaysOn.isRecording)
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
        card("Always-on recording")
        note("Starts only when you tap Start recording. It then records the room for the laptop all the time, also with the screen off, in other apps " +
            "and after the app restarts, until you tap Stop here or in the notification. " +
            "Frames go only to the configured relay; chunks stay on the phone only until they are uploaded. Camera: the glasses, or the phone if you pick it.")
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
        alwaysOnButton = button("Start recording", primary = true) {
            if (AlwaysOn.isRecording) RecordingService.stop(this) else startAlwaysOn(auto = false)
        }
        alwaysOnText = label("Always-on recording is off.")
        card("Live camera")
        note("Sends the camera and zone chosen above to the relay one photo at a time, so the laptop can rebuild the room. Frames go only to the configured relay; nothing is saved on the phone.")
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
        note("Record room takes 10 photos per second from the selected camera (the newest frame each tick; none repeated), then replaces the zone's baseline on the relay (what the room should look like). Walk slowly around the room.")
        secondsField = field("Seconds to record (10 to 60)").apply {
            inputType = InputType.TYPE_CLASS_NUMBER
            setText((RoomRecorder.DEFAULT_DURATION_MS / 1000).toString())
        }
        recordButton = button("Record room") { if (recordJob != null) stopRecording("Recording stopped. Nothing was uploaded.") else confirmRecording() }
        note("Record video follows the camera choice. Phone: records here (no audio) and sends the video to the laptop when you stop. Glasses: record on the glasses; when Meta AI has imported the video, this app sends it to the laptop.")
        videoButton = button("Record video", primary = true) { recordVideoPressed() }
        recordStatus = label("")
        fun toggle(title: String, on: Boolean): Switch = Switch(this).apply {
            text = title
            isChecked = on
            setTextColor(colors.text)
            setPadding(0, dp(6), 0, dp(6))
            container.addView(this)
        }
        sendWhenDone = toggle("Send to laptop when done", true)
        keepOnPhone = toggle("Keep video on phone", false)
        autoSendGlasses = toggle("Auto-send new glasses videos", false)
        autoSendGlasses.setOnCheckedChangeListener { _, on -> autoSendChanged(on) }
        button("Check status") { checkRecordingStatus() }
        uploadStatus = label("")
        label("Phone videos waiting on the phone", 15f).setTypeface(null, Typeface.BOLD)
        phoneVideosList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; container.addView(this) }
        label("Glasses videos (imported by Meta AI)", 15f).setTypeface(null, Typeface.BOLD)
        button("Show glasses videos") { showGlassesVideos() }
        glassesVideosList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; container.addView(this) }
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
        button("Connect / reconnect glasses", primary = true) {
            // Always a clean start: drop any stale session and pending retry, then connect once.
            cancelAutomaticRetry(); autoGlasses.userReconnected(); glasses.reconnect()
        }
        button("Install glasses app in Meta AI") { glasses.openGlassesAppUpdate() }
        note("If the glasses connect and drop at once, Meta AI may need to install this app's Developer Mode glasses app. Glasses firmware updates do not include it, and no glasses reset is needed.")
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
        card("Wearer screen")
        container.addView(CheckBox(this).apply {
            text = "Speak directions and reminders (for glasses without a display)"
            isChecked = true; setTextColor(colors.text)
            setOnCheckedChangeListener { _, on -> screen.speakCues = on }
        })
        val wearerRelayUrl = field("Vision relay URL (optional)").apply { if (BuildConfig.DEBUG) setText("http://127.0.0.1:8090") }
        val wearerRelayKey = field("Vision relay key", true)
        button("Connect vision relay") {
            screen.relay = try { WearerRelayClient(wearerRelayUrl.text.toString(), wearerRelayKey.text.toString(), allowLocalHttp = BuildConfig.DEBUG) }
                catch (_: Exception) { null }
            wearerRelayKey.text.clear()
            status.text = if (screen.relay == null) "Use the relay origin and its key." else "Vision relay configured for reminders and directions."
            screen.refresh()
        }
        button("Wearer demo: next scene") { status.text = "Wearer demo scene ${screen.nextDemoScene()} of ${WearerController.DEMO_SCENES}" }
        button("Wearer demo: off") { screen.demoOff() }
        // Ed's wearer screen comes first; every setup card above is its setup section, shown from its setup button.
        setup = column.apply { visibility = android.view.View.GONE }
        wearer = WearerPanel(this).apply {
            onAsk = { q -> question.setText(q); submit(q) }
            onMic = {
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    pendingPhoneListen = true; permissions.launch(arrayOf(Manifest.permission.RECORD_AUDIO))
                } else startPhoneListening()
            }
            onDone = { screen.finishReminder(it) }
            onDismiss = { screen.dismissNight() }
            onGo = { screen.startGuide(it) }
            onStopGuide = { screen.stopGuide() }
            onSetup = { setup.visibility = if (setup.visibility == android.view.View.VISIBLE) android.view.View.GONE else android.view.View.VISIBLE }
        }
        screen = WearerController(wearer, main, home, speak = { line -> audio.speakFallback(line) {} }, toGlasses = { card ->
            cardJob?.cancel()
            cardJob = lifecycleScope.launch {
                glasses.showCard(card, { id -> main.post { screen.finishReminder(id) } }, { target -> main.post { screen.startGuide(target) } }, { main.post { screen.stopGuide() } })
            }
        })
        navMap = WearerMap(this).apply {
            onPlaceTap = { name -> startNavigation(name) }
            onStop = { stopNavigation("Stopped.") }
        }
        takeMeThere = Button(this).apply {
            isAllCaps = false; textSize = 20f; visibility = android.view.View.GONE
            stylePill(this, true)
            setOnClickListener { findWhat?.let { startNavigation(it) } }
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(WearerPanel.NIGHT); isFillViewport = true
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.VERTICAL; setBackgroundColor(WearerPanel.NIGHT)
                addView(wearer)
                addView(navMap, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(320)).apply { setMargins(dp(20), 0, dp(20), dp(4)) })
                addView(takeMeThere, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(dp(20), dp(4), dp(20), dp(4)) })
                // Small and dim, and it opens only on a long press, so the wearer does not open it by accident.
                addView(TextView(this@MainActivity).apply {
                    text = "debug"; textSize = 11f; setTextColor(Color.argb(110, 255, 255, 255))
                    contentDescription = "Debug panel (hold to open)"
                    setPadding(dp(12), dp(6), dp(12), dp(6))
                    setOnClickListener { Toast.makeText(this@MainActivity, "Hold to open the debug panel", Toast.LENGTH_SHORT).show() }
                    setOnLongClickListener { showDebugPanel(); true }
                }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                    gravity = Gravity.END; setMargins(0, 0, dp(16), dp(12))
                })
                addView(setup)
            })
            setOnApplyWindowInsetsListener { view, insets ->
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(0, bars.top, 0, bars.bottom)
                insets
            }
        })
        androidx.core.view.WindowCompat.getInsetsController(window, window.decorView).apply { isAppearanceLightStatusBars = false; isAppearanceLightNavigationBars = false }
        lifecycleScope.launch { glasses.state.collect { s ->
            deviceStatus.text = "Registration: ${s.registration}\nDevices: ${s.deviceCount} · ${s.connection}\nGlasses: ${s.device ?: "not reported yet"}\nCamera: ${s.camera} · ${displayDescription(s.display)}\nSpeech: ${s.speech} · Motion: ${s.motion}\nGlasses consent — Camera: ${s.cameraPermission}; microphone: ${s.microphonePermission}" + (s.error?.let { "\n$it" } ?: "")
            refreshAutomaticSetup()
        } }
        lifecycleScope.launch { AlwaysOn.status.collect { renderAlwaysOn(it) } }
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
        relayLanUrl = backendMemory?.loadRelayLanUrl()
        connectWearerRelay()
        if (!consumeDebugBackendExtras(apply = savedInstanceState == null)) connectRememberedBackend()
        renderRelaySettings()
        refreshPhoneVideos()
        startAutomaticSetup()
        maybeAutoStartAlwaysOn()
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
        outState.putBoolean(STATE_GLASSES_APP_PAGE_OPENED, autoGlasses.glassesAppPageOpened)
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
        extras.venueZone?.takeIf { RelayClient.isZone(it) }?.let { zone ->
            getSharedPreferences(VENUE_PREFS, MODE_PRIVATE).edit().putString(VENUE_ZONE, zone).apply()
            navRelayFor = null
            refreshMap()
        }
        extras.alwaysOn?.let { on ->
            RecordingService.setWanted(this, on)
            if (!on && AlwaysOn.isRecording) RecordingService.stop(this)
        }
        if (extras.hasRelay) applyRelaySettings(extras.relayUrl, extras.relayKey, extras.relayZone, extras.relayLanUrl)
        extras.backendUrl?.let { endpoint.setText(it) }
        extras.apiToken?.let { connectAutomatically(it) }
        return extras.hasBackend
    }
    /** New relay settings from adb replace the remembered ones; a running live camera restarts with them. */
    private fun applyRelaySettings(url: String?, key: String?, zone: String?, lanUrl: String? = null) {
        lanUrl?.let { relayLanUrl = it; backendMemory?.saveRelayLanUrl(it) }
        url?.let { relayUrl = it }
        key?.let { relayKey = it }
        zone?.let { relayZone = it; zoneField.setText(it) }
        relayKey?.let { backendMemory?.saveRelay(relayUrl, it, relayZone) }
        connectWearerRelay()
        renderRelaySettings()
        if (liveLoop != null) { stopLiveCamera("Live camera restarting with the new relay settings."); startLiveCamera() }
        if (::alwaysOnButton.isInitialized) maybeAutoStartAlwaysOn()
    }
    /** The wearer screen's reminders and position fixes come from the same relay (its USB address). */
    private fun connectWearerRelay() {
        val key = relayKey?.takeIf { it.isNotBlank() } ?: return
        if (!::screen.isInitialized) return
        screen.relay = try { WearerRelayClient(relayUrl, key, allowLocalHttp = BuildConfig.DEBUG) } catch (_: Exception) { return }
        screen.refresh()
    }
    /** The relay address and zone, and whether a key is set. The key itself is never shown. */
    private fun renderRelaySettings() {
        if (liveLoop != null) return
        liveDetails.text = "Relay: $relayUrl" + (relayLanUrl?.let { " (Wi-Fi: $it)" } ?: "") + " · zone $relayZone · key ${if (relayKey.isNullOrBlank()) "not set" else "set"}."
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
            CameraSource.PHONE -> PhoneCamera(this, this)
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
        if (liveLoop != null || recordJob != null || videoRecorder != null) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }
    private fun cardZone(): String = zoneField.text.toString().trim()
    /** The relay client for [zone] (the card's zone by default), or null after saying on [status] what is missing. */
    private fun relayClient(status: TextView, zone: String = cardZone()): RelayClient? {
        val key = relayKey
        if (key.isNullOrBlank()) {
            status.text = if (BuildConfig.DEBUG) "No relay key yet. Send it with adb: --es relay_key … (see README)."
                else "The live camera needs a debug build with a relay key."
            return null
        }
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
        if (AlwaysOn.isRecording) { liveDetails.text = "The always-on recording is using the camera and already sends instant fixes. Stop it first."; return }
        if (recordJob != null) { liveDetails.text = "Recording the room; start the live camera after it."; return }
        if (videoRecorder != null) { liveDetails.text = "Recording a video; start the live camera after it."; return }
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
        if (AlwaysOn.isRecording) { recordStatus.text = "The always-on recording is using the camera. Stop it first."; return }
        if (videoRecorder != null) { recordStatus.text = "A video is recording; stop it first."; return }
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
            .setMessage("Records $seconds s from the ${cameraSource.label.lowercase(Locale.US)} camera at ${RoomRecorder.FRAMES_PER_SECOND} photos per second, then replaces " +
                "what the relay knows of how zone \"$zone\" should look. Walk slowly around the room. Photos stay in memory and go only to the relay.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Record") { _, _ -> startRecording(client, zone, seconds) }
            .show()
    }
    private fun startRecording(client: RelayClient, zone: String, seconds: Long) {
        if (recordJob != null) return
        if (liveLoop != null) stopLiveCamera("Live camera paused for the room recording.")
        val frames = sources()
        // The pump keeps the newest encoded frame ready; each tick takes it if new, else skips the tick.
        val pump = FramePump(lifecycleScope, capture = { frames.capture().jpeg })
        val recorder = RoomRecorder(capture = { pump.takeNew() }, durationMs = seconds * 1000)
        recordButton.text = "Stop recording"
        recordStatus.text = "Recording zone $zone from the ${cameraSource.label.lowercase(Locale.US)} camera…"
        // LAZY: lifecycleScope starts coroutines immediately on the main thread; recordJob must be set first.
        val job = lifecycleScope.launch(start = CoroutineStart.LAZY) {
            try {
                pump.start()
                if (!pump.awaitFirst(FIRST_FRAME_WAIT_MS)) {
                    recordStatus.text = "The ${cameraSource.label.lowercase(Locale.US)} camera gave no frame in ${FIRST_FRAME_WAIT_MS / 1000} s" +
                        (pump.lastError?.let { ": ${LiveRelayLoop.describe(it)}" } ?: ".") + " Nothing was uploaded."
                    return@launch
                }
                val zip = recorder.record { progress ->
                    recordStatus.text = "Recording zone $zone: ${progress.frames} frames · ${megabytes(progress.bytes)} · " +
                        "${progress.elapsedMs / 1000} of $seconds s" + (if (progress.failedFrames > 0) " · ${progress.failedFrames} frames failed" else "") +
                        (if (progress.skippedFrames > 0) " · ${progress.skippedFrames} ticks without a new frame" else "")
                }
                pump.stop()
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
                pump.stop()
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
    private fun megabytes(bytes: Int): String = megabytes(bytes.toLong())
    private fun megabytes(bytes: Long): String = String.format(Locale.US, "%.1f MB", bytes / (1024.0 * 1024.0))
    private fun minutesSeconds(totalSeconds: Long): String = String.format(Locale.US, "%02d:%02d", totalSeconds / 60, totalSeconds % 60)

    // ---- Record video: Phone records here with CameraX; Glasses arms the app for Meta AI's import ----

    private fun recordVideoPressed() {
        when {
            videoRecorder != null -> stopPhoneVideo()
            glassesWatch.armed -> disarmGlasses("Cancelled. No glasses video will be sent for this recording.")
            AlwaysOn.isRecording && cameraSource == CameraSource.PHONE -> recordStatus.text = "The always-on recording is using the phone camera. Stop it first."
            cameraSource == CameraSource.PHONE -> startPhoneVideo()
            else -> armGlasses()
        }
    }
    private fun recordingsFolder(): File? = getExternalFilesDir("recordings")?.also { it.mkdirs() }
    /** In-app video from the phone's back camera; one camera use at a time, so live and Record room stop first. */
    private fun startPhoneVideo() {
        val zone = cardZone()
        if (!RelayClient.isZone(zone)) { recordStatus.text = "Zone names use a-z, 0-9, _ and - (for example kitchen)."; return }
        if (!isGranted(Manifest.permission.CAMERA)) {
            recordStatus.text = "The phone camera needs the camera permission. Allow it, then tap Record video again."
            permissions.launch(arrayOf(Manifest.permission.CAMERA))
            return
        }
        val folder = recordingsFolder() ?: run { recordStatus.text = "No app storage is available for videos."; return }
        if (liveLoop != null) stopLiveCamera("Live camera paused for the video recording.")
        if (recordJob != null) stopRecording("Record room stopped for the video recording. Nothing was uploaded.")
        val file = File(folder, RecordingNames.fileName(zone, LocalDateTime.now()))
        val recorder = PhoneVideoRecorder(this)
        videoRecorder = recorder
        videoFile = file
        videoButton.text = "Stop recording"
        recordStatus.setTextColor(Color.rgb(217, 48, 37))
        recordStatus.text = "● REC 00:00 · zone $zone"
        updateKeepScreenOn()
        lifecycleScope.launch {
            try {
                recorder.start(file,
                    onStatus = { elapsedNs, bytes ->
                        if (videoRecorder === recorder) recordStatus.text = "● REC ${minutesSeconds(elapsedNs / 1_000_000_000)} · ${megabytes(bytes)} · zone $zone"
                    },
                    onFinished = { code -> phoneVideoFinished(recorder, file, code, null) })
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                recorder.release()
                phoneVideoFinished(recorder, file, -1, LiveRelayLoop.describe(e))
            }
        }
    }
    private fun stopPhoneVideo() {
        val recorder = videoRecorder ?: return
        recorder.stop()
        recordStatus.text = "Finishing the video…"
    }
    private fun phoneVideoFinished(recorder: PhoneVideoRecorder, file: File, code: Int, failure: String?) {
        if (videoRecorder !== recorder) return
        videoRecorder = null
        videoFile = null
        videoButton.text = "Record video"
        recordStatus.setTextColor(colors.text)
        updateKeepScreenOn()
        if (!file.isFile || file.length() == 0L) {
            file.delete()
            recordStatus.text = "The video was not recorded" + (failure?.let { ": $it" } ?: " (CameraX code $code).")
            refreshPhoneVideos()
            return
        }
        recordStatus.text = "Saved ${file.name} (${megabytes(file.length())})." +
            (if (code != 0) " The recording ended early (CameraX code $code) but the file was kept." else "")
        refreshPhoneVideos()
        if (sendWhenDone.isChecked) uploadPhoneVideo(file)
    }
    /** Glasses: nothing records here. The app waits for the next video Meta AI imports, then sends it. */
    private fun armGlasses() {
        val zone = cardZone()
        relayClient(recordStatus, zone) ?: return
        withMediaAccess {
            armedZone = zone
            glassesWatch.arm(System.currentTimeMillis())
            watchGlassesVideos()
            videoButton.text = "Cancel"
            recordStatus.text = "Record on your glasses now: press and hold the capture button (or say \"Hey Meta, record a video\"). " +
                "When you stop, open Meta AI so it imports the video. This app then sends it to zone $zone."
            armTimeoutJob?.cancel()
            armTimeoutJob = lifecycleScope.launch {
                delay(GlassesVideoWatch.ARM_TIMEOUT_MS)
                if (glassesWatch.armed) disarmGlasses("No new glasses video arrived within 10 minutes. Record on the glasses, " +
                    "open Meta AI to import it, then tap Record video again, or send it from the glasses videos list.")
            }
        }
    }
    private fun disarmGlasses(message: String) {
        glassesWatch.disarm()
        armedZone = null
        armTimeoutJob?.cancel()
        armTimeoutJob = null
        videoButton.text = "Record video"
        recordStatus.text = message
        if (!autoSendGlasses.isChecked) unwatchGlassesVideos()
    }
    private fun autoSendChanged(on: Boolean) {
        if (on) withMediaAccess { watchGlassesVideos(); checkGlassesVideos() }
        else if (!glassesWatch.armed) unwatchGlassesVideos()
    }
    private fun mediaPermission(): String =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) Manifest.permission.READ_MEDIA_VIDEO else Manifest.permission.READ_EXTERNAL_STORAGE
    /** Runs [action] once the app may read videos, asking at first use. */
    private fun withMediaAccess(action: () -> Unit) {
        if (isGranted(mediaPermission())) { action(); return }
        pendingMediaAction = action
        uploadStatus.text = "Allow access to videos so the app can read the glasses videos Meta AI imported (read only)."
        permissions.launch(arrayOf(mediaPermission()))
    }
    private fun glassesVideos(): GlassesVideoLibrary = glassesLibrary ?: GlassesVideoLibrary(contentResolver).also { glassesLibrary = it }
    private fun watchGlassesVideos() {
        if (glassesObserver == null) glassesObserver = glassesVideos().watch { checkGlassesVideos() }
    }
    private fun unwatchGlassesVideos() {
        glassesObserver?.let { glassesVideos().unwatch(it) }
        glassesObserver = null
    }
    /** Armed or auto-send: sends the next new Meta AI video once its size has stopped growing for 3 s. */
    private fun checkGlassesVideos() {
        if (glassesCheckJob?.isActive == true || uploadJob != null) return
        val armed = glassesWatch.armed
        if (!armed && !autoSendGlasses.isChecked) return
        if (!isGranted(mediaPermission())) return
        val zone = armedZone ?: cardZone()
        val library = glassesVideos()
        glassesCheckJob = lifecycleScope.launch {
            val videos = withContext(Dispatchers.IO) { runCatching { library.query() }.getOrDefault(emptyList()) }
            val video = glassesWatch.next(videos, autoSendGlasses.isChecked) ?: return@launch
            relayClient(uploadStatus, zone) ?: return@launch
            glassesWatch.markSent(video.id)
            recordStatus.text = "New glasses video ${video.name}: waiting until Meta AI has finished importing it…"
            val settle = SizeSettle()
            val giveUpAt = SystemClock.elapsedRealtime() + SETTLE_LIMIT_MS
            var size: Long? = null
            while (SystemClock.elapsedRealtime() < giveUpAt) {
                size = withContext(Dispatchers.IO) { runCatching { library.size(video.id) }.getOrNull() }
                if (settle.settled(SystemClock.elapsedRealtime(), size ?: -1L)) break
                delay(1_000)
            }
            if (armed) disarmGlasses("Glasses video ${video.name} found.")
            if (size == null || size <= 0L) {
                recordStatus.text = "Glasses video ${video.name} could not be read. Send it from the glasses videos list."
                return@launch
            }
            uploadGlassesVideo(video.copy(sizeBytes = size), zone)
        }
    }
    private fun uploadPhoneVideo(file: File) {
        if (file == videoFile) { uploadStatus.text = "That video is still recording."; return }
        val zone = RecordingNames.zoneOf(file.name) ?: relayZone
        val client = relayClient(uploadStatus, zone) ?: return
        val uploader = RecordingUploader(packageName, zone) { video, videoZone, progress ->
            client.uploadRecording(videoZone, FileUploadBody(video, RelayClient.VIDEO_MP4, progress), RelayClient.sessionFor(video.name))
        }
        val keep = keepOnPhone.isChecked
        runUpload(file.name) { progress ->
            when (val outcome = uploader.send(file, keep, progress)) {
                is UploadOutcome.Sent -> sentText(outcome.reply) +
                    (if (outcome.deletedFromPhone) "\nThe video was deleted from the phone." else "\nThe video stays on the phone.")
                is UploadOutcome.Failed -> "Upload failed: ${outcome.message}\nThe video stays on the phone. Use Retry below, or copy it with:\n${outcome.adbPull}"
            }
        }
    }
    /** Reads Meta AI's video as a stream; never changes or deletes it. */
    private fun uploadGlassesVideo(video: GlassesVideo, zone: String) {
        val client = relayClient(uploadStatus, zone) ?: return
        val library = glassesVideos()
        runUpload(video.name) { progress ->
            try {
                val body = StreamUploadBody({ library.open(video.id) }, video.sizeBytes, RelayClient.VIDEO_MP4, progress)
                sentText(client.uploadRecording(zone, body, RelayClient.sessionFor(video.name))) + "\nMeta AI's copy on the phone is unchanged."
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                "Upload failed: ${LiveRelayLoop.describe(e)}\nSend it again from the glasses videos list."
            }
        }
    }
    /** One upload at a time, with progress in bytes and percent; [work] returns the text to show at the end. */
    private fun runUpload(name: String, work: suspend (onProgress: (Long, Long) -> Unit) -> String) {
        if (uploadJob != null) { uploadStatus.text = "Another upload is running; try again when it has finished."; return }
        uploadStatus.text = "Uploading $name…"
        val job = lifecycleScope.launch(start = CoroutineStart.LAZY) {
            try {
                var lastPercent = -2L
                uploadStatus.text = work { sent, total ->
                    val percent = if (total > 0) sent * 100 / total else -1L
                    if (percent != lastPercent) {
                        lastPercent = percent
                        val shown = if (total > 0) "${megabytes(sent)} of ${megabytes(total)} ($percent %)" else megabytes(sent)
                        main.post { if (uploadJob != null) uploadStatus.text = "Uploading $name: $shown" }
                    }
                }
            } finally {
                if (uploadJob === coroutineContext[Job]) uploadJob = null
                refreshPhoneVideos()
                checkGlassesVideos()
            }
        }
        uploadJob = job
        job.start()
    }
    private fun sentText(reply: RecordingReply): String = listOfNotNull(
        "Sent to the laptop" + (reply.zone?.let { " (zone $it)" } ?: "") + (reply.status?.let { ": $it" } ?: "."),
        reply.seconds?.let { "Video: ${String.format(Locale.US, "%.0f", it)} s" + (reply.id?.let { id -> " · id $id" } ?: "") },
        reply.viewerUrl(relayUrl)?.let { "Open on the laptop: $it" },
    ).joinToString("\n")
    private fun checkRecordingStatus() {
        val zone = cardZone()
        val client = relayClient(uploadStatus, zone) ?: return
        uploadStatus.text = "Asking the laptop about zone $zone…"
        lifecycleScope.launch {
            uploadStatus.text = try {
                val reply = client.recordingStatus(zone)
                listOfNotNull(
                    "Laptop, zone ${reply.zone ?: zone}: ${reply.status ?: "no status"}",
                    reply.seconds?.let { "Video: ${String.format(Locale.US, "%.0f", it)} s" + (reply.id?.let { id -> " · id $id" } ?: "") },
                    reply.viewerUrl(relayUrl)?.let { "Open on the laptop: $it" },
                ).joinToString("\n")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                "Status unavailable: ${LiveRelayLoop.describe(e)}"
            }
        }
    }
    private fun rowButton(title: String, action: () -> Unit): Button = Button(this).apply {
        text = title
        setOnClickListener { action() }
        stylePill(this, false)
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            .apply { marginStart = dp(6) }
    }
    private fun rowText(text: String): TextView = TextView(this).apply {
        this.text = text
        textSize = 13f
        setTextColor(colors.text)
        layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
    }
    /** Phone videos still in the app's recordings folder, newest first, with Retry and Delete. */
    private fun refreshPhoneVideos() {
        if (!::phoneVideosList.isInitialized) return
        phoneVideosList.removeAllViews()
        val files = recordingsFolder()?.listFiles()?.filter { it.isFile && RecordingNames.isRecording(it.name) }
            ?.sortedByDescending { it.name } ?: emptyList()
        if (files.isEmpty()) {
            phoneVideosList.addView(rowText("None: every phone video has been sent and removed."))
            return
        }
        for (file in files) {
            phoneVideosList.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(rowText("${file.name}\n${megabytes(file.length())}"))
                addView(rowButton("Retry") { uploadPhoneVideo(file) })
                addView(rowButton("Delete") { confirmDelete(file) })
            })
        }
    }
    private fun confirmDelete(file: File) {
        AlertDialog.Builder(this).setTitle("Delete ${file.name}?")
            .setMessage("The video is removed from the phone for good. A copy already sent to the laptop is not affected.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Delete") { _, _ ->
                if (file != videoFile) file.delete()
                refreshPhoneVideos()
            }
            .show()
    }
    /** Meta AI's imported glasses videos, newest first, each with Send to laptop. Read only. */
    private fun showGlassesVideos() = withMediaAccess {
        val library = glassesVideos()
        lifecycleScope.launch {
            val videos = withContext(Dispatchers.IO) { runCatching { library.query(20) }.getOrDefault(emptyList()) }
            glassesVideosList.removeAllViews()
            if (videos.isEmpty()) {
                glassesVideosList.addView(rowText("No glasses videos yet. Record on the glasses, then open Meta AI so it imports them."))
                return@launch
            }
            val dates = DateTimeFormatter.ofPattern("d MMM HH:mm", Locale.getDefault())
            for (video in videos) {
                val thumb = ImageView(this@MainActivity).apply {
                    layoutParams = LinearLayout.LayoutParams(dp(80), dp(60)).apply { marginEnd = dp(8) }
                    scaleType = ImageView.ScaleType.CENTER_CROP
                    contentDescription = "Glasses video ${video.name}"
                }
                val added = Instant.ofEpochSecond(video.dateAddedSec).atZone(ZoneId.systemDefault()).format(dates)
                glassesVideosList.addView(LinearLayout(this@MainActivity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(0, dp(4), 0, dp(4))
                    addView(thumb)
                    addView(rowText("$added · ${minutesSeconds(video.durationMs / 1000)} · ${megabytes(video.sizeBytes)}" +
                        (if (glassesWatch.wasSent(video.id)) "\nSent in this session" else "")))
                    addView(rowButton("Send to laptop") { sendGlassesVideo(video) })
                })
                launch {
                    val picture = withContext(Dispatchers.IO) { library.thumbnail(video.id) }
                    if (picture != null) thumb.setImageBitmap(picture)
                }
            }
        }
    }
    private fun sendGlassesVideo(video: GlassesVideo) {
        val zone = cardZone()
        relayClient(uploadStatus, zone) ?: return
        glassesWatch.markSent(video.id)
        uploadGlassesVideo(video, zone)
    }
    // ---- Always-on recording: a foreground service keeps recording until Stop ----

    /** Opt-in: resumes by itself on opening only if the last choice was Start (remembered), never on a fresh install. */
    private fun maybeAutoStartAlwaysOn() {
        if (!automaticSetup || !alwaysOnAutoStart || pendingAlwaysOn) return
        if (AlwaysOnRules.autoStart(wanted = RecordingService.wanted(this), relayKeySet = !relayKey.isNullOrBlank(),
                recording = AlwaysOn.isRecording)) startAlwaysOn(auto = true)
    }
    /** Starts the service from the Activity in front; asks once for notifications and, for the phone, the camera. */
    private fun startAlwaysOn(auto: Boolean) {
        if (AlwaysOn.isRecording) return
        val key = relayKey
        if (key.isNullOrBlank()) {
            alwaysOnText.text = if (BuildConfig.DEBUG) "No relay key yet. Send it with adb: --es relay_key … (see README)." else "Always-on recording needs a debug build with a relay key."
            return
        }
        val zone = cardZone()
        if (!RelayClient.isZone(zone)) { alwaysOnText.text = "Zone names use a-z, 0-9, _ and - (for example kitchen)."; return }
        val source = AlwaysOnRules.source(cameraSource)
        val missing = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 33 && !notificationsAsked && !isGranted(Manifest.permission.POST_NOTIFICATIONS)) missing += Manifest.permission.POST_NOTIFICATIONS
        if (source == CameraSource.PHONE && !isGranted(Manifest.permission.CAMERA)) missing += Manifest.permission.CAMERA
        if (missing.isNotEmpty() && !notificationsAsked) {
            notificationsAsked = true
            pendingAlwaysOn = true
            permissions.launch(missing.toTypedArray())
            return
        }
        if (source == CameraSource.PHONE && !isGranted(Manifest.permission.CAMERA)) {
            alwaysOnText.text = "The phone camera needs the camera permission for always-on recording."
            return
        }
        // One camera user at a time: the live stream and a room recording stop first.
        if (liveLoop != null) stopLiveCamera("Live camera stopped: the always-on recording uses the camera.")
        if (recordJob != null) stopRecording("Record room stopped: the always-on recording uses the camera. Nothing was uploaded.")
        if (videoRecorder != null && source == CameraSource.PHONE) stopPhoneVideo()
        releaseSources()
        val config = AlwaysOn.Config(relayUrl, relayLanUrl, key, zone, source)
        try {
            RecordingService.start(this, config)
            alwaysOnText.text = (if (auto) "Starting automatically" else "Starting") + " · ${source.label} · zone $zone…"
        } catch (e: Exception) {
            alwaysOnText.text = "Always-on recording could not start (${e.javaClass.simpleName})."
        }
    }
    private fun renderAlwaysOn(status: AlwaysOnStatus) {
        if (!::alwaysOnButton.isInitialized) return
        val recording = AlwaysOn.isRecording
        alwaysOnButton.text = when {
            status.stopping -> "Stopping…"
            recording -> "Stop recording"
            else -> "Start recording"
        }
        val heading = when {
            status.stopping -> "Stopping: the last chunk goes to the laptop…"
            recording -> "Recording · " + RecordingService.summary(status)
            status.session != null -> "Always-on recording is off. Last session: ${status.session} (${status.chunksSent} chunks sent, ${status.chunksWaiting} still on the phone)."
            else -> status.waiting ?: "Always-on recording is off."
        }
        alwaysOnText.text = if (recording || status.stopping) heading + "\n" + RecordingService.details(status) else heading
    }

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
            AutoGlassesSetup.Step.OPEN_GLASSES_APP_UPDATE -> glasses.openGlassesAppUpdate()
            AutoGlassesSetup.Step.OPEN_FIRMWARE_UPDATE -> glasses.openFirmwareUpdate()
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
            autoGlasses.blockedBy == AutoGlassesSetup.GLASSES_APP_UPDATE ->
                "enable Developer Mode for these glasses in Meta AI, then tap the glasses to install this app's glasses app (Meta AI was opened once automatically; Install glasses app in Meta AI opens it again). Then come back. No glasses reset needed."
            autoGlasses.blockedBy == AutoGlassesSetup.FIRMWARE_UPDATE -> "the glasses firmware is too old for this app. Update it in Meta AI, then tap Connect / reconnect glasses."
            autoGlasses.blockedBy == AutoGlassesSetup.SDK_UPDATE -> "these glasses need a newer build of this app (Meta SDK too old)."
            autoGlasses.asleep -> "Glasses not awake: unfold or put them on, or open Meta AI. Reconnecting automatically."
            autoGlasses.dropStreak > 0 -> "the glasses dropped the session right after it started (${autoGlasses.dropStreak} in a row). Reconnecting automatically." +
                (if (autoGlasses.dropStreak >= AutoGlassesSetup.DROP_HINT_AFTER) " If it keeps happening, tap Install glasses app in Meta AI." else "")
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
            s.blocker == AutoGlassesSetup.GLASSES_APP_UPDATE -> paintChip(glassesChip, ChipLevel.BAD, "Glasses app missing")
            s.blocker != null -> paintChip(glassesChip, ChipLevel.BAD, "Glasses need update")
            automaticSetup && autoGlasses.asleep -> paintChip(glassesChip, ChipLevel.WAIT, "Glasses asleep")
            automaticSetup && autoGlasses.dropStreak >= AutoGlassesSetup.DROP_HINT_AFTER -> paintChip(glassesChip, ChipLevel.BAD, "Glasses keep dropping")
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
                configuredDay = DayClient(origin, suppliedToken, home, allowLocalHttp = BuildConfig.DEBUG)
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
                    screen.dayClient = configuredDay
                    controller = VoiceTurnController(client, audio, { work -> if (Looper.myLooper() == main.looper) work() else main.post(work) }, ::renderTurn)
                    // DEBUG only: a verified origin and token are remembered, encrypted, for the next launch.
                    if (rememberToken != null) backendMemory?.save(origin, rememberToken)
                    backendStatus.text = "Backend: reachable and authenticated at last check."
                    status.text = "Backend connection verified. Ask a question or test audio."
                    screen.refresh()
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
        // "Take me there": to the thing just found.
        val find = FindPhrases.parse(text)
        if (find == FindCommand.TakeMeThere && findFound) { findWhat?.let { startNavigation(it); return } }
        // "Take me to / where is / how do I get to <place>" and "stop" are guidance, not questions.
        when (val command = NavPhrases.parse(text, knownPlaces)) {
            is NavCommand.GoTo -> { startNavigation(command.place); return }
            NavCommand.Stop -> if (navGuide != null) { stopNavigation("Stopped."); return }
            null -> Unit
        }
        // "Find me / where is / where did I put / have you seen <thing>" that is not a named place: the relay looks.
        // Without a relay the question goes on to the backend as before.
        if (find is FindCommand.Find && navRelay() != null) { startFind(find.what); return }
        if (screen.ask(text)) return // the wearer demo answers by itself
        if (controller == null) { status.text = "Connect the backend first."; if (::wearer.isInitialized) wearer.setStatus("Not connected"); return }
        lastQuestion = text
        controller?.submit(text)
    }
    private fun renderTurn(turn: TurnState) {
        renderStatus(turn)
        if (::screen.isInitialized) screen.turnBusy = turn.phase in setOf(TurnPhase.ASKING, TurnPhase.FETCHING_SPEECH, TurnPhase.PLAYING, TurnPhase.FALLBACK)
        if (::wearer.isInitialized) wearer.setStatus(when (turn.phase) {
            TurnPhase.ASKING -> "…"; TurnPhase.ERROR -> "No answer. Try again."; else -> ""
        })
        if (::screen.isInitialized) turn.answer?.let(screen::answered)
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
    // ---- Debug panel (long press on "debug" under the map) ----

    /** What the panel shows: addresses, key and token prints, zones, last answers, glasses, recording. Never a secret. */
    private fun diagnosticsFacts(): DiagnosticsFacts {
        val s = glasses.state.value
        val glassesLines = listOf(
            "Registration ${s.registration} · ${s.connection} · ${s.deviceCount} device(s)",
            "Glasses: ${s.device ?: "not reported yet"}",
            "Camera ${s.camera} · consent: camera ${s.cameraPermission}, microphone ${s.microphonePermission}",
            "Sessions: ${s.sessionEnds} idle ends, ${s.sessionDrops} drops, ${s.sessionAsleep} asleep, " +
                "${s.sessionFailures} failed, ${s.sessionBlocks} blocked" + (s.blocker?.let { " (needs $it)" } ?: ""),
            "Last end or error: ${s.error ?: "none"}",
        )
        val a = AlwaysOn.status.value
        val waiting = ChunkQueue(File(filesDir, "chunks")).pending()
        val videos = recordingsFolder()?.listFiles()?.count { it.isFile && RecordingNames.isRecording(it.name) } ?: 0
        val recordingLines = mutableListOf<String>()
        recordingLines += if (AlwaysOn.isRecording) "Always-on: recording · " + RecordingService.summary(a)
            else "Always-on: off" + (a.session?.let { " · last session $it" } ?: "") + if (RecordingService.wanted(this)) " (wanted: starts with the app)" else ""
        if (AlwaysOn.isRecording) recordingLines += RecordingService.details(a).lines().filter { it.isNotBlank() }
        else {
            a.lastUpload?.let { recordingLines += "Last upload: $it" }
            a.uploadError?.let { recordingLines += "Upload error: $it" }
        }
        recordingLines += "Chunks waiting on the phone: ${waiting.size} (${megabytes(waiting.sumOf { it.bytes })})" +
            if (waiting.isNotEmpty() && !AlwaysOn.isRecording) ": tap Retry uploads" else ""
        recordingLines += "Phone videos not sent: $videos" + if (videos > 0) " (Retry each under Record video in setup)" else ""
        recordingLines += "Live camera ${if (liveLoop != null) "on" else "off"} · room recording ${if (recordJob != null) "on" else "off"} · " +
            "video ${if (videoRecorder != null) "recording" else "off"} · guidance ${navGuide?.place ?: "off"}"
        return DiagnosticsFacts(
            build = "${BuildConfig.VERSION_NAME} (${BuildConfig.COMMIT})" + if (BuildConfig.DEBUG) " debug" else "",
            relayUrl = relayUrl,
            relayLanUrl = relayLanUrl,
            relayKeyPrint = KeyPrint.of(relayKey),
            backendUrl = configuredOrigin ?: endpoint.text.toString().trim().ifEmpty { null },
            backendTokenPrint = configuredClient?.tokenPrint ?: "not connected",
            zone = cardZone(),
            venueZone = venueZone,
            glasses = glassesLines,
            recording = recordingLines,
            trace = HttpTrace.snapshot(),
            nowMs = System.currentTimeMillis(),
        )
    }

    private fun showDebugPanel() {
        if (debugDialog?.isShowing == true) return
        val report = TextView(this).apply {
            typeface = Typeface.MONOSPACE; textSize = 12f; setTextIsSelectable(true)
            setPadding(dp(16), dp(8), dp(16), dp(8))
        }
        val note = TextView(this).apply { textSize = 13f; setPadding(dp(16), 0, dp(16), dp(8)) }
        fun render() { report.text = Diagnostics.report(diagnosticsFacts()) }
        fun small(label: String, action: () -> Unit) = Button(this).apply {
            text = label; isAllCaps = false; setOnClickListener { action() }
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL; setPadding(dp(8), 0, dp(8), 0)
                addView(small("Refresh") { note.text = "Asking the relay and the backend…"; probeForDebug { render(); note.text = "Checked just now." } })
                addView(small("Retry uploads") { retryUploadsFromDebug { message -> note.text = message; render() } })
                addView(small("Copy") {
                    val clipboard = getSystemService(android.content.ClipboardManager::class.java)
                    clipboard?.setPrimaryClip(android.content.ClipData.newPlainText("Glasses app diagnostics", report.text))
                    note.text = "Copied (addresses, key prints and states only: no keys or tokens)."
                })
            })
            addView(note)
            addView(ScrollView(this@MainActivity).apply { addView(report) },
                LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        }
        render()
        val ticker = object : Runnable {
            override fun run() { if (debugDialog?.isShowing == true) { render(); main.postDelayed(this, 2_000) } }
        }
        debugDialog = AlertDialog.Builder(this)
            .setTitle("Debug")
            .setView(content)
            .setNegativeButton("Close", null)
            .setOnDismissListener { main.removeCallbacks(ticker); debugDialog = null }
            .show()
        main.postDelayed(ticker, 2_000)
    }

    /** Asks each relay address (USB and Wi-Fi) for the zone's places and the backend for one event, then [done]. */
    private fun probeForDebug(done: () -> Unit) {
        var pending = 1
        fun finished() { pending--; if (pending == 0) done() }
        val key = relayKey?.takeIf { it.isNotBlank() }
        val zone = venueZone?.takeIf { RelayClient.isZone(it) } ?: cardZone().takeIf { RelayClient.isZone(it) } ?: relayZone
        if (key != null) for (url in listOfNotNull(relayUrl, relayLanUrl).distinct()) {
            val client = try { RelayClient(url, key, zone, allowLocalHttp = BuildConfig.DEBUG) } catch (_: Exception) { continue }
            pending++
            lifecycleScope.launch {
                try { client.placeNames() } catch (e: CancellationException) { throw e } catch (_: Exception) { }
                finished()
            }
        }
        configuredClient?.let { client -> pending++; client.checkConnection { main.post { finished() } } }
        finished()
    }

    /** Retry uploads: the recording retries its waiting chunk now; otherwise the chunks it left are sent in order. */
    private fun retryUploadsFromDebug(done: (String) -> Unit) {
        if (AlwaysOn.retryUploadsNow()) { done("The recording is trying its waiting chunk again now."); return }
        if (debugDrainJob?.isActive == true) { done("Already sending the waiting chunks…"); return }
        val queue = ChunkQueue(File(filesDir, "chunks"))
        if (queue.pending().isEmpty()) { done("No chunks are waiting on the phone."); return }
        val key = relayKey?.takeIf { it.isNotBlank() } ?: run { done("No relay key yet: chunks stay on the phone."); return }
        val primary = try { RelayClient(relayUrl, key, relayZone, allowLocalHttp = BuildConfig.DEBUG) } catch (_: Exception) {
            done("Relay address not accepted."); return
        }
        val lan = relayLanUrl?.let { url -> try { RelayClient(url, key, relayZone, allowLocalHttp = BuildConfig.DEBUG) } catch (_: Exception) { null } }
        val relay = FailoverRelay(primary, lan)
        done("Sending ${queue.pending().size} waiting chunk(s)…")
        debugDrainJob = lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                ChunkDrain.drain(queue) { c -> relay.call { it.uploadChunk(c.zone, c.session, c.seq, c.final, c.file) } }
            }
            done("Sent ${result.sent}" + (if (result.refused > 0) ", ${result.refused} refused by the relay and dropped" else "") +
                (result.stoppedBy?.let { ", stopped: ${LiveRelayLoop.describe(it)}" } ?: "") + ". ${result.left} still on the phone.")
        }
    }

    // ---- The wearer's map and guidance ----

    /** The relay for the map and guidance: USB first, the Wi-Fi address when the cable is out. */
    private fun navRelay(): FailoverRelay<RelayClient>? {
        val key = relayKey?.takeIf { it.isNotBlank() } ?: return null
        val zone = venueZone?.takeIf { RelayClient.isZone(it) } ?: cardZone().takeIf { RelayClient.isZone(it) } ?: relayZone
        val settings = listOf(relayUrl, relayLanUrl, zone, key.hashCode()).joinToString("|")
        if (settings == navRelayFor) return navRelayCached
        val primary = try { RelayClient(relayUrl, key, zone, allowLocalHttp = BuildConfig.DEBUG) } catch (_: Exception) { return null }
        val lan = relayLanUrl?.let { url -> try { RelayClient(url, key, zone, allowLocalHttp = BuildConfig.DEBUG) } catch (_: Exception) { null } }
        navRelayFor = settings
        return FailoverRelay(primary, lan).also { navRelayCached = it }
    }

    /** Idle refresh (every 10 s): the zone's place names and, when the relay has it, the map. */
    private fun refreshMap() {
        if (!::navMap.isInitialized || navGuide != null) return
        val relay = navRelay() ?: return
        lifecycleScope.launch {
            try { relay.call { it.placeNames() }.takeIf { it.isNotEmpty() }?.let { knownPlaces = it } } catch (e: CancellationException) { throw e } catch (_: Exception) { }
            try {
                val reply = relay.call { it.navigate(null) }
                if (navGuide == null) renderMap(reply, null)
            } catch (e: CancellationException) { throw e } catch (_: Exception) { }
        }
    }

    private fun renderMap(reply: NavReply, target: String?) {
        lastMapReply = reply
        if (reply.places.isNotEmpty()) knownPlaces = (knownPlaces + reply.places.map { it.name }).distinct()
        val found = foundItem?.takeIf { f -> reply.places.none { it.name.equals(f.name, ignoreCase = true) } }
        navMap.show(reply.plan, reply.places + listOfNotNull(found), reply.position, reply.headingDeg, if (target != null) reply.route else emptyList(),
            target, if (!reply.ok) reply.error else null, foundItem?.name)
    }

    // ---- "Find me X" ----

    /**
     * Asks the relay's /find for [what] (in the venue zone) and says the answer through the selected audio
     * (the glasses'), on the wearer screen and as a ring on the map. When the relay is still looking it says
     * "I'll look for the X" and asks again every 3 s for up to a minute. "Take me there" then guides to it.
     */
    private fun startFind(what: String) {
        val relay = navRelay() ?: run {
            if (::wearer.isInitialized) wearer.setStatus("Set up the relay first")
            speak("I can't look for the $what: the laptop is not set up.")
            return
        }
        findJob?.cancel()
        findWhat = what; findFound = false; foundItem = null
        updateTakeMeThere(); redrawMap()
        if (::wearer.isInitialized) wearer.setStatus("Looking for the $what…")
        val search = FindSearch(what,
            fetch = { relay.call { it.find(what) } },
            say = { line -> speak(line); if (::wearer.isInitialized) wearer.setStatus(line) },
            shown = { reply ->
                if (reply.found) {
                    findFound = true
                    reply.position?.let { foundItem = NavPlace(what, it) }
                    updateTakeMeThere(); redrawMap()
                }
            },
            now = { SystemClock.elapsedRealtime() },
            sleep = { delay(it) },
            failure = { e -> findFailure(what, e) })
        findJob = lifecycleScope.launch { search.run() }
    }

    private fun findFailure(what: String, e: Exception): String {
        val relayFailure = e as? RelayFailure
        return when {
            relayFailure?.status == 404 -> "Finding things isn't set up on the laptop yet."
            relayFailure?.unreachable == true -> "I can't reach the laptop to look for the $what."
            else -> "I couldn't look for the $what."
        }
    }

    private fun updateTakeMeThere() {
        if (!::takeMeThere.isInitialized) return
        val what = findWhat
        takeMeThere.visibility = if (what != null && findFound) android.view.View.VISIBLE else android.view.View.GONE
        if (what != null) takeMeThere.text = "Take me there: $what"
    }

    /** The map again with the found thing, from the newest map reply (or just the thing when there is none yet). */
    private fun redrawMap() {
        if (!::navMap.isInitialized) return
        lastMapReply?.let { renderMap(it, navGuide?.place); return }
        navMap.show(emptyList(), listOfNotNull(foundItem), null, null, emptyList(), null, null, foundItem?.name)
    }

    /** Guides the wearer to [place]: map route, spoken directions on each new fix, until there or "stop". */
    private fun startNavigation(place: String) {
        val relay = navRelay() ?: run { if (::wearer.isInitialized) wearer.setStatus("Set up the relay first"); return }
        stopNavigation(null)
        val guide = NavGuide(place)
        navGuide = guide
        wearer.setStatus("To $place")
        speak("Going to $place.")
        // Directions follow new position fixes: the always-on recording sends them; otherwise the live
        // camera does (newest frame only, one at a time), started here and stopped with the guidance.
        if (!AlwaysOn.isRecording && liveLoop == null && recordJob == null && videoRecorder == null) {
            startLiveCamera()
            navStartedLive = liveLoop != null
        }
        navJob = lifecycleScope.launch {
            while (navGuide === guide) {
                try {
                    val reply = relay.call { it.navigate(place) }
                    renderMap(reply, place)
                    if (!reply.ok) wearer.setStatus(reply.error ?: "No directions yet")
                    guide.next(reply, SystemClock.elapsedRealtime())?.let { say -> speak(say); wearer.setStatus(say) }
                    if (guide.arrived) { stopNavigation(null); wearer.setStatus("You're there: $place"); break }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    wearer.setStatus("No directions: ${LiveRelayLoop.describe(e)}")
                }
                delay(NAV_POLL_MS)
            }
        }
    }

    private fun stopNavigation(message: String?) {
        val wasGuiding = navGuide != null
        navGuide = null
        navJob?.cancel(); navJob = null
        if (navStartedLive) { navStartedLive = false; stopLiveCamera("Live camera is off.") }
        if (wasGuiding && ::navMap.isInitialized) navMap.show(emptyList(), emptyList(), null, null, emptyList(), null, null)
        message?.let { speak(it); if (::wearer.isInitialized) wearer.setStatus(it) }
        if (wasGuiding) refreshMap()
    }

    /** A short spoken line on the selected audio output (the glasses' when they are the Bluetooth audio). */
    private fun speak(text: String) { audio.speakFallback(text) { } }

    override fun onStop() {
        // Meta registration and consent intentionally leave this Activity briefly.
        backendInterrupted = connectionCheck != null
        cancelConnectionCheck(); cancelDiagnostic()
        cancelEvidence(); stopRecognition(); displayJob?.cancel(); controller?.cancel(); audio.stop()
        screen.pause(); main.removeCallbacks(mapRefresher)
        cancelAutomaticRetry()
        stopLiveCamera("Live camera stopped because the app left the screen.")
        stopRecording("Recording stopped because the app left the screen. Nothing was uploaded; record again when ready.")
        // A phone video is stopped and finalised; it is then sent as usual. An armed glasses wait keeps running,
        // because Meta AI has to be opened to import the glasses video.
        stopPhoneVideo()
        // The always-on recording keeps the glasses session (and the camera) while the app is not in front.
        if (!glasses.isExternalFlowPending) { glasses.stopListening(); if (!AlwaysOn.isRecording) glasses.suspendLifecycle() }
        super.onStop()
    }
    override fun onResume() {
        super.onResume()
        screen.resume(); main.removeCallbacks(mapRefresher); main.post(mapRefresher)
        if (::glasses.isInitialized) glasses.onResume()
        updatePermissionStatus()
        // Leaving the foreground disconnects the glasses (onStop); automatic setup reconnects once per return.
        if (::glasses.isInitialized) {
            resumeInterruptedBackendCheck()
            cancelAutomaticRetry()
            autoGlasses.onForeground()
            continueAutomaticSetup()
        }
        if (::alwaysOnButton.isInitialized) maybeAutoStartAlwaysOn()
        // Back from Meta AI: look for a newly imported glasses video in case no change was reported.
        if (::autoSendGlasses.isInitialized && (glassesWatch.armed || autoSendGlasses.isChecked)) checkGlassesVideos()
    }
    /** A backend check cut short by leaving the app (e.g. Meta AI consent) is re-run with the in-memory client. */
    private fun resumeInterruptedBackendCheck() {
        val interrupted = backendInterrupted
        backendInterrupted = false
        if (automaticSetup && interrupted && controller == null && configuredClient != null &&
            token.text.isEmpty() && endpoint.text.toString().trim() == configuredOrigin) connectBackend()
    }
    override fun onDestroy() {
        debugDialog?.dismiss(); debugDialog = null
        navJob?.cancel(); navGuide = null
        findJob?.cancel(); findJob = null
        stopLiveCamera("Live camera is off.")
        stopRecording("Recording stopped.")
        videoRecorder?.release()
        unwatchGlassesVideos()
        // While the always-on recording runs, the glasses session stays for its service; otherwise it ends here.
        recognizer?.destroy(); GlassesHub.release(this, glasses, keepSession = AlwaysOn.isRecording); audio.close()
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
