package com.junction.glasses.meta

import android.graphics.Bitmap
import android.graphics.ImageDecoder
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
    /** Counts started sessions the glasses ended themselves (they do so after a few idle minutes). */
    val sessionEnds: Int = 0,
)
data class MotionReading(val timestampNs: Long, val x: Float, val y: Float, val z: Float)

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
    val isExternalFlowPending: Boolean get() = registrationPending || permissionResult != null
    fun onResume() { registrationPending = false; refreshPermissions() }
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
    private fun sessionFailed(message: String) {
        mutableState.update { it.copy(error = message, sessionFailures = it.sessionFailures + 1) }
    }
    /** Routine, not an error: the glasses end a started session after a few idle minutes. */
    private fun sessionEnded() {
        mutableState.update { it.copy(error = "Glasses ended the idle session. They reconnect when needed.", sessionEnds = it.sessionEnds + 1) }
    }
    private var sessionStarted = false
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
            sessionStarted = false
            val owner = SupervisorJob(job)
            sessionJob = owner
            val listeners = CoroutineScope(scope.coroutineContext + owner)
            sessionScope = listeners
            mutableState.update { it.copy(connection = "CONNECTING", error = null) }
            listeners.launch {
                connected.state.collect { status ->
                    mutableState.update { it.copy(connection = status.name) }
                    if (status == DeviceSessionState.STARTED) sessionStarted = true
                    if (status == DeviceSessionState.STARTED && state.value.displayEnabled && display == null) attachDisplay(connected, listeners)
                    if (status == DeviceSessionState.STOPPED) { if (sessionStarted) sessionEnded(); disconnect() }
                }
            }
            listeners.launch { connected.errors.collect {
                val description = "${it.description}"
                if (description.contains("ended by device", ignoreCase = true)) sessionEnded()
                else sessionFailed("Glasses session failed: $description")
                disconnect()
            } }
            connected.start()
            listeners.launch {
                delay(20_000)
                if (!sessionStarted) { sessionFailed("Glasses connection timed out. Put on the glasses and retry."); disconnect() }
            }
        }.onFailure { _, _ -> sessionFailed("Cannot create glasses session. Check pairing and SDK/device compatibility.") }
    }

    /**
     * Connects when needed and waits up to [timeoutMs] for STARTED, so features keep working after the
     * glasses ended an idle session. False when not registered or the session does not start in time.
     */
    suspend fun ensureStarted(timeoutMs: Long = 20_000): Boolean {
        if (session != null && state.value.connection == "STARTED") return true
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
                ?: run { fail("Connect glasses before starting the camera."); return@runCommand }
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
                ?: run { fail("Connect glasses before listening."); return@runCommand }
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
}
