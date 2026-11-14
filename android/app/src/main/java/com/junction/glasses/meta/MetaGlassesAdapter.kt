package com.junction.glasses.meta

import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.os.SystemClock
import android.util.Log
import androidx.activity.ComponentActivity
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import com.meta.wearable.dat.camera.Camera
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.removeCamera
import com.meta.wearable.dat.camera.types.PhotoData
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.StreamState
import com.meta.wearable.dat.camera.types.VideoQuality
import com.meta.wearable.dat.display.Display
import com.meta.wearable.dat.display.addDisplay
import com.meta.wearable.dat.display.types.DisplayState
import com.meta.wearable.dat.display.views.Direction
import com.meta.wearable.dat.display.views.TextStyle
import com.meta.wearable.dat.motion.Motion
import com.meta.wearable.dat.motion.addMotion
import com.meta.wearable.dat.motion.types.MotionConfiguration
import com.meta.wearable.dat.motion.types.MotionSamplingRate
import com.meta.wearable.dat.motion.types.MotionSource
import com.meta.wearable.dat.speech.Speech
import com.meta.wearable.dat.speech.addSpeech
import com.meta.wearable.dat.speech.removeSpeech
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import java.io.ByteArrayOutputStream
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/** Session-local state, with no transcript, token or image logging/persistence. */
data class MetaState(
    val initialized: Boolean = false,
    val registration: String = "UNAVAILABLE",
    val deviceCount: Int = 0,
    val cameraPermission: String = "UNKNOWN",
    val microphonePermission: String = "UNKNOWN",
    val connection: String = "DISCONNECTED",
    val camera: String = "STOPPED",
    val display: String = "NOT_REQUIRED",
    val displayEnabled: Boolean = false,
    val speech: String = "STOPPED",
    val motion: String = "STOPPED",
    val error: String? = null,
    /** Counts sessions that timed out, reported an error or could not be created; lets callers retry. */
    val sessionFailures: Int = 0,
    /** Counts sessions that ran at least [GlassesSessions.STABLE_MS] before the glasses ended them (idle ends). */
    val sessionEnds: Int = 0,
    /** Counts sessions the glasses ended within [GlassesSessions.STABLE_MS] of starting, or refused outright. */
    val sessionDrops: Int = 0,
    /** Counts sessions that failed because the glasses were asleep: off, folded, in the case or unreachable. */
    val sessionAsleep: Int = 0,
    /** Counts sessions refused for a reason retrying cannot fix; [blocker] names the latest one. */
    val sessionBlocks: Int = 0,
    /** A [SessionOutcome] name that needs the user (for example GLASSES_APP_UPDATE); cleared when a session starts. */
    val blocker: String? = null,
    /** The glasses the current or last session used, as the SDK reports them (name, compatibility, firmware). */
    val device: String? = null,
)
data class MotionReading(val timestampNs: Long, val x: Float, val y: Float, val z: Float)

/** How one glasses session ended. Decided by [GlassesSessions.classify]; no SDK types, so JVM tests cover it. */
internal enum class SessionOutcome {
    /** The glasses ended a session that had been running; routine after a few idle minutes. */
    IDLE_END,
    /** The glasses ended or refused the session within [GlassesSessions.STABLE_MS] of starting. */
    DROPPED,
    /**
     * The glasses were not awake: taken off, folded, in the case or out of reach (START_ERROR_DEVICE_UNAVAILABLE,
     * seen on RB Meta 0076 from 23:17 to 23:20 while they were off), or they never answered.
     */
    ASLEEP,
    /** The session could not be created, or a running one failed. */
    FAILED,
    /**
     * DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED: the glasses lack this app's Developer Mode glasses app
     * ("DevMode DWA") or have an old one. Firmware updates do not install it; Meta AI does.
     */
    GLASSES_APP_UPDATE,
    /** The glasses firmware is too old for this SDK. */
    FIRMWARE_UPDATE,
    /** This build's Meta SDK is too old for the glasses. */
    SDK_UPDATE;

    /** Retrying cannot help: the user has to act in Meta AI or install a newer build. */
    val needsUser: Boolean get() = this == GLASSES_APP_UPDATE || this == FIRMWARE_UPDATE || this == SDK_UPDATE
}

/** Pure session-end rules, kept apart from the SDK calls so they are unit tested. */
internal object GlassesSessions {
    /** A session shorter than this was dropped, not ended for being idle (idle ends come after minutes). */
    const val STABLE_MS = 15_000L
    /** Errors that describe one capability or a soft warning; the session itself keeps running. */
    private val NOT_FATAL = setOf("DWA_OUT_OF_STU_RANGE", "CAPABILITY_ALREADY_ADDED", "CAPABILITY_NOT_FOUND", "CAPABILITY_DENIED")
    private val ROUTINE_END = setOf("SESSION_ENDED_BY_DEVICE", "SESSION_IDLE")
    /** The glasses are off, folded, in the case or out of reach; the SDK may also name it *_UNAVAILABLE. */
    private val NOT_AWAKE = setOf("DEVICE_DISCONNECTED", "NO_ELIGIBLE_DEVICE")
    /** What the session start timeout records: the glasses never answered. */
    const val NO_ANSWER = "NO_ANSWER"

    fun endsSession(error: String): Boolean = error !in NOT_FATAL

    /**
     * [error] is the SDK's DeviceSessionError name (null when the session just stopped), [startedForMs]
     * how long it was STARTED (null when it never started), [compatibility] the device's DeviceCompatibility.
     */
    fun classify(error: String?, startedForMs: Long?, compatibility: String? = null): SessionOutcome = when {
        error != null && "DAT_APP" in error -> SessionOutcome.GLASSES_APP_UPDATE
        error == "INSUFFICIENT_SDK_VERSION" || compatibility == "SDK_UPDATE_REQUIRED" -> SessionOutcome.SDK_UPDATE
        compatibility == "DEVICE_UPDATE_REQUIRED" -> SessionOutcome.FIRMWARE_UPDATE
        error != null && (error in NOT_AWAKE || error == NO_ANSWER || error.endsWith("UNAVAILABLE")) -> SessionOutcome.ASLEEP
        startedForMs == null -> if (error == null || error in ROUTINE_END) SessionOutcome.DROPPED else SessionOutcome.FAILED
        startedForMs < STABLE_MS -> SessionOutcome.DROPPED
        error == null || error in ROUTINE_END -> SessionOutcome.IDLE_END
        else -> SessionOutcome.FAILED
    }

    fun message(outcome: SessionOutcome, error: String?, description: String?, startedForMs: Long?): String {
        val reason = listOfNotNull(description?.takeIf { it.isNotBlank() }, error?.takeIf { it != description && it != NO_ANSWER })
            .joinToString(" · ").ifEmpty { "no reason given" }
        return when (outcome) {
            SessionOutcome.GLASSES_APP_UPDATE -> "Enable Developer Mode for these glasses in Meta AI, then tap the glasses " +
                "(App info → Developer mode → the glasses row): Meta AI installs this app's glasses app in a few seconds. " +
                "Firmware updates do not install it, and no glasses reset is needed ($error)."
            SessionOutcome.FIRMWARE_UPDATE -> "The glasses firmware is too old for this app. Update the glasses in Meta AI."
            SessionOutcome.SDK_UPDATE -> "These glasses need a newer build of this app: its Meta SDK is too old ($reason)."
            SessionOutcome.IDLE_END -> "Glasses ended the idle session. They reconnect when needed."
            SessionOutcome.ASLEEP -> "Glasses not awake: unfold or put them on, or open Meta AI. Reconnecting automatically" +
                (if (error == NO_ANSWER) " (no answer in 20 s)." else " ($reason).")
            SessionOutcome.DROPPED -> if (startedForMs == null) "Glasses refused the session before it started: $reason."
                else "Glasses dropped the session ${startedForMs / 1000} s after it started: $reason."
            SessionOutcome.FAILED -> "Glasses session failed: $reason."
        }
    }
}

/**
 * Meta DAT 1.0.1 adapter. Construct during Activity.onCreate, before STARTED.
 * All commands run on Main. Camera, speech and motion require explicit user actions.
 * Caller stops this adapter when leaving the foreground; no covert/background capture.
 */
class MetaGlassesAdapter(private val activity: ComponentActivity, parentScope: CoroutineScope) {
    private val job = SupervisorJob(parentScope.coroutineContext[Job])
    private val observerErrors = CoroutineExceptionHandler { _, _ -> fail("Glasses connection interrupted. Reconnect to continue.") }
    private val scope = CoroutineScope(parentScope.coroutineContext + job + Dispatchers.Main.immediate + observerErrors)
    private val mutableState = MutableStateFlow(MetaState())
    val state: StateFlow<MetaState> = mutableState.asStateFlow()
    private val mutableTranscripts = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val transcripts: SharedFlow<String> = mutableTranscripts.asSharedFlow()
    private val mutableMotion = MutableStateFlow<MotionReading?>(null)
    val motion: StateFlow<MotionReading?> = mutableMotion.asStateFlow()
    private var session: DeviceSession? = null
    private var sessionJob: Job? = null
    private var sessionScope: CoroutineScope? = null
    private var cameraDeadline: Job? = null
    private var camera: Camera? = null
    private var display: Display? = null
    private var speech: Speech? = null
    private var speechCollectors: Job? = null
    private var cameraCollectors: Job? = null
    private var motionHandle: Motion? = null
    private var registrationPending = false
    /** Meta AI was opened to install the glasses app or firmware; cleared when this Activity resumes. */
    private var metaAiPagePending = false
    val isExternalFlowPending: Boolean get() = registrationPending || metaAiPagePending || permissionResult != null
    fun onResume() { registrationPending = false; metaAiPagePending = false; refreshPermissions() }
    private var permissionRefresh: Job? = null
    fun suspendLifecycle() { if (!isExternalFlowPending) disconnect() }
    private var permissionResult: CompletableDeferred<PermissionStatus>? = null
    private var cameraStarting = false
    private var speechStarting = false
    private var listenGeneration = 0L
    private var listeningRequested = false
    private var cameraGeneration = 0L
    private var motionRequested = false
    private val permissionLauncher = activity.registerForActivityResult(Wearables.RequestPermissionContract()) {
        permissionResult?.complete(it.getOrDefault(PermissionStatus.Denied))
        permissionResult = null
    }

    private fun fail(message: String) { mutableState.update { it.copy(error = message) } }
    /** Records how a session ended, with the time, so the card says why the glasses dropped. */
    private fun record(outcome: SessionOutcome, message: String) {
        val text = "${LocalTime.now().format(CLOCK)} · $message"
        lastFailedEndAt = if (outcome == SessionOutcome.IDLE_END) null else SystemClock.elapsedRealtime()
        mutableState.update {
            when (outcome) {
                SessionOutcome.IDLE_END -> it.copy(error = text, sessionEnds = it.sessionEnds + 1)
                SessionOutcome.DROPPED -> it.copy(error = text, sessionDrops = it.sessionDrops + 1)
                SessionOutcome.ASLEEP -> it.copy(error = text, sessionAsleep = it.sessionAsleep + 1)
                SessionOutcome.FAILED -> it.copy(error = text, sessionFailures = it.sessionFailures + 1)
                else -> it.copy(error = text, sessionBlocks = it.sessionBlocks + 1, blocker = outcome.name)
            }
        }
    }
    private fun sessionFailed(message: String) = record(SessionOutcome.FAILED, message)
    /**
     * When the last session failed, dropped or was refused (elapsedRealtime); null after an idle end or a
     * start. On-demand connects (camera, listening, the live camera's retries) wait [ON_DEMAND_GAP_MS] after
     * it, so a caller retrying every 2 s cannot override the automatic reconnect pacing or a refusal.
     */
    private var lastFailedEndAt: Long? = null
    private var reconnecting: Job? = null
    private var sessionStarted = false
    /** elapsedRealtime when the current session reached STARTED; null before that. */
    private var startedAt: Long? = null
    /** The first session-ending SDK error of the current session, by enum name, and its description. */
    private var sessionError: String? = null
    private var sessionErrorText: String? = null
    /** False until the current session reports a state other than STOPPED, so its initial value is not an end. */
    private var sessionActive = false
    /** Waits briefly after STOPPED for the error that explains it (the SDK may send it after the state). */
    private var stopGrace: Job? = null
    private fun runCommand(block: suspend CoroutineScope.() -> Unit) = scope.launch {
        try { block() } catch (e: CancellationException) { throw e }
        catch (_: Exception) { fail("Glasses operation failed. Check permissions and reconnect.") }
    }

    fun initialize() {
        if (state.value.initialized) return
        Wearables.initialize(activity).onSuccess {
            mutableState.update { it.copy(initialized = true, error = null) }
            refreshPermissions()
            runCommand { Wearables.registrationState.collect { value -> mutableState.update { it.copy(registration = value.name) } } }
            runCommand { Wearables.devices.collect { value -> mutableState.update { it.copy(deviceCount = value.size) } } }
            runCommand { Wearables.registrationErrorStream.collect { fail("Meta registration failed. Check Meta AI and developer mode.") } }
        }.onFailure { _, _ -> fail("Meta SDK initialization failed. Grant Bluetooth access and install Meta AI.") }
    }

    fun register() {
        if (!state.value.initialized) { fail("Initialize glasses first."); return }
        registrationPending = true
        Wearables.startRegistration(activity)
    }

    fun connect() {
        if (!state.value.initialized || state.value.registration != "REGISTERED") {
            fail("Register with Meta AI before connecting."); return
        }
        if (session != null) return
        Wearables.createSession(AutoDeviceSelector()).onSuccess { connected ->
            session = connected
            sessionStarted = false; startedAt = null; sessionError = null; sessionErrorText = null; sessionActive = false
            stopGrace?.cancel(); stopGrace = null
            val owner = SupervisorJob(job)
            sessionJob = owner
            val listeners = CoroutineScope(scope.coroutineContext + owner)
            sessionScope = listeners
            mutableState.update { it.copy(connection = "CONNECTING", error = null) }
            Log.i(TAG, "session created")
            listeners.launch { connected.deviceInfo.collect { info ->
                val firmware = info.firmwareInfo?.let { " · firmware $it" } ?: ""
                val device = "${info.name} · ${enumName(info.compatibility)}$firmware"
                if (device != state.value.device) Log.i(TAG, "device $device")
                mutableState.update { it.copy(device = device) }
            } }
            listeners.launch {
                connected.state.collect { status ->
                    Log.i(TAG, "state ${status.name}")
                    mutableState.update { it.copy(connection = status.name) }
                    if (status != DeviceSessionState.STOPPED) sessionActive = true
                    if (status == DeviceSessionState.STARTED && startedAt == null) {
                        sessionStarted = true; startedAt = SystemClock.elapsedRealtime(); lastFailedEndAt = null
                        mutableState.update { it.copy(blocker = null) }
                    }
                    if (status == DeviceSessionState.STARTED && state.value.displayEnabled && display == null) attachDisplay(connected, listeners)
                    if (status == DeviceSessionState.STOPPED && sessionActive && stopGrace == null) {
                        stopGrace = scope.launch { delay(STOP_GRACE_MS); finishSession(connected) }
                    }
                }
            }
            listeners.launch { connected.errors.collect { error ->
                val name = enumName(error)
                Log.i(TAG, "error $name")
                if (!GlassesSessions.endsSession(name)) { fail("Glasses: ${error.description}"); return@collect }
                if (sessionError == null) { sessionError = name; sessionErrorText = "${error.description}" }
                finishSession(connected)
            } }
            connected.start()
            listeners.launch {
                delay(20_000)
                if (!sessionStarted && session === connected) {
                    Log.i(TAG, "start timed out")
                    record(SessionOutcome.ASLEEP, GlassesSessions.message(SessionOutcome.ASLEEP, GlassesSessions.NO_ANSWER, null, null))
                    disconnect()
                }
            }
        }.onFailure { _, _ -> sessionFailed("Cannot create glasses session. Check pairing and SDK/device compatibility.") }
    }

    /**
     * Ends [connected] once, saying why: the SDK error if one came, else how long it ran. A session the
     * glasses refuse or drop within seconds is not an idle end, so it never feeds the quiet reconnect loop.
     */
    private fun finishSession(connected: DeviceSession) {
        if (session !== connected) return
        stopGrace?.cancel(); stopGrace = null
        val startedFor = startedAt?.let { SystemClock.elapsedRealtime() - it }
        val compatibility = try { enumName(connected.deviceInfo.value.compatibility) } catch (e: CancellationException) { throw e } catch (_: Exception) { null }
        val outcome = GlassesSessions.classify(sessionError, startedFor, compatibility)
        Log.i(TAG, "session ended: $outcome, error=${sessionError ?: "none"}, started for ${startedFor ?: "never"} ms, compatibility=$compatibility")
        record(outcome, GlassesSessions.message(outcome, sessionError, sessionErrorText, startedFor))
        disconnect()
    }

    /**
     * Opens Meta AI's page that installs or updates this app's Developer Mode glasses app: the fix for
     * DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED (no glasses reset needed). Reconnect after returning.
     */
    fun openGlassesAppUpdate() {
        if (!state.value.initialized) { fail("Initialize glasses first."); return }
        metaAiPagePending = true
        Log.i(TAG, "opening Meta AI glasses app update")
        try {
            Wearables.openDATGlassesAppUpdate(activity).onFailure { _, _ ->
                metaAiPagePending = false
                fail("Could not open Meta AI. In Meta AI open App info → Developer mode and tap the glasses to install the glasses app.")
            }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { metaAiPagePending = false; fail("Could not open Meta AI. In Meta AI open App info → Developer mode and tap the glasses.") }
    }

    /**
     * The Connect / reconnect button: drops any stale or stuck session and the recorded refusal, then
     * starts a fresh session, never adding one on top of an old one. Taps in a row restart it once.
     */
    fun reconnect() {
        Log.i(TAG, "manual reconnect")
        reconnecting?.cancel()
        val hadSession = session != null
        disconnect()
        lastFailedEndAt = null
        mutableState.update { it.copy(blocker = null) }
        reconnecting = scope.launch {
            // Give the SDK a moment to release the old session before asking for a new one.
            if (hadSession) delay(RECONNECT_GAP_MS)
            connect()
        }
    }

    /** False for a while after a failed, dropped or refused session, and while a refusal needs the user. */
    private fun onDemandConnectAllowed(): Boolean {
        if (state.value.blocker != null) return false
        val failedAt = lastFailedEndAt ?: return true
        return SystemClock.elapsedRealtime() - failedAt >= ON_DEMAND_GAP_MS
    }

    /** The message for an on-demand feature that found no session, keeping a recorded reason visible. */
    private fun notConnected(feature: String) {
        if (lastFailedEndAt == null && state.value.blocker == null) fail("Connect glasses before $feature.")
    }

    /** Opens Meta AI's glasses firmware update page (DeviceCompatibility DEVICE_UPDATE_REQUIRED). */
    fun openFirmwareUpdate() {
        if (!state.value.initialized) { fail("Initialize glasses first."); return }
        metaAiPagePending = true
        try {
            Wearables.openFirmwareUpdate(activity).onFailure { _, _ ->
                metaAiPagePending = false; fail("Could not open Meta AI. Update the glasses firmware in Meta AI.")
            }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { metaAiPagePending = false; fail("Could not open Meta AI. Update the glasses firmware in Meta AI.") }
    }

    /**
     * Connects when needed and waits up to [timeoutMs] for STARTED, so features keep working after the
     * glasses ended an idle session. False when not registered or the session does not start in time.
     */
    suspend fun ensureStarted(timeoutMs: Long = 20_000): Boolean {
        if (session != null && state.value.connection == "STARTED") return true
        if (session == null && !onDemandConnectAllowed()) return false
        connect()
        if (session == null) return false
        val reached = withTimeoutOrNull(timeoutMs) {
            state.first { it.connection == "STARTED" || it.connection == "DISCONNECTED" }
        }
        return reached?.connection == "STARTED" && session != null
    }

    /**
     * Asks Meta AI for glasses camera and then microphone consent through the same path camera and
     * listening use, skipping any already granted. Starts no camera or microphone stream. [onFinished]
     * runs on Main once both answers are settled, whatever they are; not if this adapter closes first.
     */
    fun requestConsent(onFinished: () -> Unit) {
        if (!state.value.initialized) { onFinished(); return }
        scope.launch {
            for (permission in listOf(Permission.CAMERA, Permission.MICROPHONE)) {
                try { permission(permission) }
                catch (_: CancellationException) { ensureActive() }
                catch (_: Exception) { fail("Glasses consent request failed. Camera and microphone ask again when used.") }
            }
            onFinished()
        }
    }

    /** Audio-first by default. Changing mode tears down the old hardware session. */
    fun setDisplayEnabled(enabled: Boolean) {
        if (state.value.displayEnabled == enabled) return
        disconnect()
        mutableState.update { it.copy(displayEnabled = enabled,
            display = if (enabled) "UNAVAILABLE" else "NOT_REQUIRED", error = null) }
    }

    private fun attachDisplay(connected: DeviceSession, listeners: CoroutineScope) {
        if (!connected.deviceInfo.value.isDisplayCapable()) {
            mutableState.update { it.copy(display = "UNSUPPORTED") }; return
        }
        connected.addDisplay().onSuccess { panel ->
            display = panel
            listeners.launch { panel.state.collect { status -> mutableState.update { it.copy(display = status.name) } } }
        }.onFailure { _, _ -> mutableState.update { it.copy(display = "UNAVAILABLE") } }
    }

    private fun recordPermission(permission: Permission, status: PermissionStatus?) {
        val label = when (status) {
            PermissionStatus.Granted -> "GRANTED"
            PermissionStatus.Denied -> "DENIED"
            else -> "UNKNOWN"
        }
        mutableState.update {
            when (permission) {
                Permission.CAMERA -> it.copy(cameraPermission = label)
                Permission.MICROPHONE -> it.copy(microphonePermission = label)
                else -> it
            }
        }
    }

    /** Read-only consent status; never launches a Meta permission dialog. */
    fun refreshPermissions() {
        if (!state.value.initialized) return
        permissionRefresh?.cancel()
        permissionRefresh = scope.launch {
            for (permission in listOf(Permission.CAMERA, Permission.MICROPHONE)) {
                val status = try {
                    Wearables.checkPermissionStatus(permission).getOrNull()
                } catch (e: CancellationException) { throw e }
                catch (_: Exception) { null }
                ensureActive()
                recordPermission(permission, status)
            }
        }
    }

    private suspend fun permission(permission: Permission): Boolean {
        val existing = Wearables.checkPermissionStatus(permission).getOrNull()
        recordPermission(permission, existing)
        if (existing == PermissionStatus.Granted) return true
        if (permissionResult != null) { fail("Complete the pending glasses permission request first."); return false }
        val result = CompletableDeferred<PermissionStatus>()
        permissionResult = result
        return try {
            permissionLauncher.launch(permission)
            val status = withTimeoutOrNull(60_000) { result.await() }
            recordPermission(permission, status)
            status == PermissionStatus.Granted
        } finally { if (permissionResult === result) permissionResult = null }
    }

    fun startCamera() = runCommand {
        if (camera != null || cameraStarting) return@runCommand
        cameraStarting = true
        val generation = ++cameraGeneration
        try {
            // Connect on demand: the glasses end idle sessions by themselves.
            val connected = (if (ensureStarted()) session else null)
                ?: run { notConnected("starting the camera"); return@runCommand }
            if (generation != cameraGeneration) return@runCommand
            if (!permission(Permission.CAMERA)) { fail("Glasses camera permission was not granted."); return@runCommand }
            if (session !== connected || generation != cameraGeneration) return@runCommand
            connected.addCamera(StreamConfiguration(videoQuality = VideoQuality.MEDIUM, frameRate = 15)).onSuccess { added ->
                camera = added
                mutableState.update { it.copy(error = null) }
                val collectorJob = SupervisorJob(sessionJob)
                cameraCollectors = collectorJob
                val collectors = CoroutineScope(scope.coroutineContext + collectorJob)
                collectors.launch { added.stream.state.collect { status -> mutableState.update { it.copy(camera = status.name) } } }
                collectors.launch { added.stream.errorStream.collect { fail("Glasses camera failed: ${it.description}") } }
                cameraDeadline?.cancel()
                cameraDeadline = scope.launch {
                    delay(60_000)
                    if (camera === added) stopCamera()
                }
                added.stream.start().onFailure { _, _ -> fail("Glasses camera could not start.") }
            }.onFailure { _, _ -> fail("Camera is unavailable on this glasses session.") }
        } finally { cameraStarting = false }
    }

    fun stopCamera() {
        cameraDeadline?.cancel(); cameraDeadline = null
        cameraGeneration++
        cameraCollectors?.cancel(); cameraCollectors = null
        camera?.stop(); session?.removeCamera(); camera = null
        mutableState.update { it.copy(camera = "STOPPED") }
    }

    /**
     * Live camera source: one photo from the already streaming glasses camera, which keeps running for
     * the next one (unlike [capturePhoto]); each call restarts the 60 s unused-camera deadline. Memory
     * only. Null when the camera is not streaming or the capture fails or times out.
     */
    suspend fun captureLiveBitmap(): Bitmap? {
        val active = camera ?: return null
        if (active.stream.state.value != StreamState.STREAMING) return null
        cameraDeadline?.cancel()
        cameraDeadline = scope.launch {
            delay(60_000)
            if (camera === active) stopCamera()
        }
        return try {
            val photo = withTimeoutOrNull(10_000) { active.stream.capturePhoto().getOrNull() } ?: return null
            withContext(Dispatchers.Default) {
                when (photo) {
                    is PhotoData.Bitmap -> photo.bitmap
                    is PhotoData.HEIC -> ImageDecoder.decodeBitmap(ImageDecoder.createSource(photo.data.duplicate())) { decoder, _, _ ->
                        decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                    }
                }
            }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { null }
    }

    /** JPEG in memory only. Caller must enforce its own event/evidence consent gate. */
    suspend fun capturePhoto(): ByteArray? = try {
        capturePhotoInternal()
    } catch (e: CancellationException) { throw e }
    catch (_: Exception) { fail("Photo capture unavailable. Reconnect and retry."); null }
    finally { stopCamera() }

    private suspend fun capturePhotoInternal(): ByteArray? {
        val active = camera ?: run { fail("Start the glasses camera first."); return null }
        if (active.stream.state.value != StreamState.STREAMING) { fail("Wait for the glasses camera to start."); return null }
        val photo = withTimeoutOrNull(10_000) { active.stream.capturePhoto().getOrNull() }
            ?: run { fail("Glasses photo capture failed or timed out."); return null }
        return withContext(Dispatchers.Default) {
            val bitmap = when (photo) {
                is PhotoData.Bitmap -> photo.bitmap
                is PhotoData.HEIC -> ImageDecoder.decodeBitmap(ImageDecoder.createSource(photo.data.duplicate())) { decoder, _, _ ->
                    decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                }
            } ?: return@withContext null
            ByteArrayOutputStream().use { out ->
                if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 85, out)) return@withContext null
                out.toByteArray().takeIf { it.size <= 5 * 1024 * 1024 }
            }
        }
    }

    fun startListening() = runCommand {
        if (speechStarting) return@runCommand
        speechStarting = true
        listeningRequested = true
        val generation = ++listenGeneration
        try {
            // Connect on demand: the glasses end idle sessions by themselves.
            val connected = (if (ensureStarted()) session else null)
                ?: run { notConnected("listening"); return@runCommand }
            if (generation != listenGeneration || !listeningRequested) return@runCommand
            if (!permission(Permission.MICROPHONE)) { fail("Glasses microphone permission was not granted."); return@runCommand }
            if (session !== connected || generation != listenGeneration || !listeningRequested) return@runCommand
            if (speech == null) connected.addSpeech().onSuccess { added ->
                speech = added
                val collectorJob = SupervisorJob(sessionJob)
                speechCollectors = collectorJob
                val collectors = CoroutineScope(scope.coroutineContext + collectorJob)
                collectors.launch { added.state.collect { status -> mutableState.update { it.copy(speech = status.name) } } }
                collectors.launch { added.errors.collect { error -> if (error != null) fail("Glasses speech unavailable: ${error.description}") } }
                collectors.launch {
                    added.transcriptions.collect { result ->
                        if (result != null && result.isFinal && result.text.isNotBlank() && listeningRequested && state.value.speech == "STARTED") {
                            mutableTranscripts.emit(result.text)
                        }
                    }
                }
            }.onFailure { _, _ -> mutableState.update { it.copy(speech = "UNAVAILABLE", error = "Glasses speech is unavailable. Use the phone microphone.") } }
            speech?.start()?.onSuccess { mutableState.update { it.copy(error = null) } }
                ?.onFailure { _, _ -> fail("Glasses speech could not start. Use the phone microphone.") }
        } finally { speechStarting = false }
    }
    fun stopListening() {
        listeningRequested = false
        listenGeneration++
        speechCollectors?.cancel(); speechCollectors = null
        speech?.stop(); session?.removeSpeech(); speech = null
        mutableState.update { it.copy(speech = "STOPPED") }
    }

    fun startMotion() {
        motionRequested = true
        if (motionHandle != null) { motionHandle?.start(); return }
        val connected = session?.takeIf { state.value.connection == "STARTED" }
            ?: run { fail("Connect glasses before enabling motion."); return }
        connected.addMotion(MotionConfiguration(samplingRate = MotionSamplingRate.HZ_5)).onSuccess { added ->
            motionHandle = added
            mutableState.update { it.copy(error = null) }
            sessionScope?.launch { added.state.collect { status -> mutableState.update { it.copy(motion = status.name) } } }
            sessionScope?.launch { added.errors.collect { error -> if (error != null) fail("Glasses motion unavailable: ${error.description}") } }
            sessionScope?.launch { added.samples.collect { sample ->
                if (motionRequested && sample.source == MotionSource.GLASSES) sample.accelerometer?.let {
                    mutableMotion.value = MotionReading(sample.timestampNs, it.x, it.y, it.z)
                }
            } }
            added.start()
        }.onFailure { _, _ -> mutableState.update { it.copy(motion = "UNAVAILABLE", error = "Motion is unavailable on these glasses.") } }
    }
    fun stopMotion() { motionRequested = false; motionHandle?.stop(); mutableMotion.value = null }

    suspend fun showAnswer(text: String): Boolean = try {
        showAnswerInternal(text)
    } catch (e: CancellationException) { throw e }
    catch (_: Exception) { fail("Display unavailable. Read the answer on the phone."); false }

    private suspend fun showAnswerInternal(text: String): Boolean {
        if (!state.value.displayEnabled) return false
        val panel = display ?: return false
        if (panel.state.value != DisplayState.STARTED) return false
        return withTimeoutOrNull(5_000) {
            panel.sendContent { flexBox(direction = Direction.COLUMN, padding = 16, gap = 8) {
                text("Recorded observations", style = TextStyle.HEADING)
                text(text.take(1200), style = TextStyle.BODY)
            } }.isSuccess
        } ?: false
    }

    fun disconnect() {
        reconnecting?.cancel(); reconnecting = null
        stopGrace?.cancel(); stopGrace = null
        cameraDeadline?.cancel(); cameraDeadline = null
        listeningRequested = false; motionRequested = false; listenGeneration++; cameraGeneration++
        permissionResult?.cancel(); permissionResult = null
        sessionJob?.cancel(); sessionJob = null; sessionScope = null
        speech?.stop(); motionHandle?.stop(); camera?.stop()
        val old = session; session = null
        old?.stop()
        speechCollectors = null; cameraCollectors = null
        speech = null; motionHandle = null; camera = null; display = null
        mutableMotion.value = null
        mutableState.update { it.copy(connection = "DISCONNECTED", camera = "STOPPED", display = if (it.displayEnabled) "UNAVAILABLE" else "NOT_REQUIRED", speech = "STOPPED", motion = "STOPPED") }
    }
    fun close() { disconnect(); job.cancel(); permissionLauncher.unregister() }

    private companion object {
        /** Logcat tag for session states, SDK error names and outcomes. No audio, images, tokens or addresses. */
        const val TAG = "GlassesSession"
        const val STOP_GRACE_MS = 750L
        const val RECONNECT_GAP_MS = 500L
        const val ON_DEMAND_GAP_MS = 10_000L
        val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss")
        /** The constant's name for SDK enums (DeviceSessionError, DeviceCompatibility); toString otherwise. */
        fun enumName(value: Any?): String = (value as? Enum<*>)?.name ?: value.toString()
    }
}
