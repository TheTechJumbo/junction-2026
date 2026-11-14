package com.junction.glasses.meta

import android.graphics.Bitmap
import android.graphics.BitmapFactory
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
    val connection: String = "DISCONNECTED",
    val camera: String = "STOPPED",
    val display: String = "UNAVAILABLE",
    val speech: String = "STOPPED",
    val motion: String = "STOPPED",
    val error: String? = null,
)
data class MotionReading(val timestampNs: Long, val x: Float, val y: Float, val z: Float)

/**
 * Meta DAT 1.0.1 adapter. Construct during Activity.onCreate, before STARTED.
 * All commands run on Main. Camera, speech and motion require explicit user actions.
 * Caller stops this adapter when leaving the foreground; no covert/background capture.
 */
class MetaGlassesAdapter(private val activity: ComponentActivity, parentScope: CoroutineScope) {
    private val job = SupervisorJob(parentScope.coroutineContext[Job])
    private val scope = CoroutineScope(parentScope.coroutineContext + job + Dispatchers.Main.immediate)
    private val mutableState = MutableStateFlow(MetaState())
    val state: StateFlow<MetaState> = mutableState.asStateFlow()
    private val mutableTranscripts = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val transcripts: SharedFlow<String> = mutableTranscripts.asSharedFlow()
    private val mutableMotion = MutableStateFlow<MotionReading?>(null)
    val motion: StateFlow<MotionReading?> = mutableMotion.asStateFlow()
    private var session: DeviceSession? = null
    private var sessionJob: Job? = null
    private var sessionScope: CoroutineScope? = null
    private var camera: Camera? = null
    private var display: Display? = null
    private var speech: Speech? = null
    private var speechCollectors: Job? = null
    private var cameraCollectors: Job? = null
    private var motionHandle: Motion? = null
    private var registrationPending = false
    val isExternalFlowPending: Boolean get() = registrationPending || permissionResult != null
    fun onResume() { registrationPending = false }
    fun suspendLifecycle() { if (!isExternalFlowPending) disconnect() }
    private var permissionResult: CompletableDeferred<PermissionStatus>? = null
    private var cameraStarting = false
    private var speechStarting = false
    private var listenGeneration = 0L
    private var listeningRequested = false
    private var cameraGeneration = 0L
    private val permissionLauncher = activity.registerForActivityResult(Wearables.RequestPermissionContract()) {
        permissionResult?.complete(it.getOrDefault(PermissionStatus.Denied))
        permissionResult = null
    }

    private fun fail(message: String) { mutableState.update { it.copy(error = message) } }
    private fun runCommand(block: suspend CoroutineScope.() -> Unit) = scope.launch {
        try { block() } catch (e: CancellationException) { throw e }
        catch (_: Exception) { fail("Glasses operation failed. Check permissions and reconnect.") }
    }

    fun initialize() {
        if (state.value.initialized) return
        Wearables.initialize(activity).onSuccess {
            mutableState.update { it.copy(initialized = true, error = null) }
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
            val owner = SupervisorJob(job)
            sessionJob = owner
            val listeners = CoroutineScope(scope.coroutineContext + owner)
            sessionScope = listeners
            mutableState.update { it.copy(connection = "CONNECTING", error = null) }
            listeners.launch {
                connected.state.collect { status ->
                    mutableState.update { it.copy(connection = status.name) }
                    if (status == DeviceSessionState.STARTED && display == null) attachDisplay(connected, listeners)
                    if (status == DeviceSessionState.STOPPED) disconnect()
                }
            }
            listeners.launch { connected.errors.collect { fail("Glasses session failed: ${it.description}"); disconnect() } }
            connected.start()
            listeners.launch {
                delay(20_000)
                if (state.value.connection != "STARTED") { fail("Glasses connection timed out. Put on the glasses and retry."); disconnect() }
            }
        }.onFailure { _, _ -> fail("Cannot create glasses session. Check pairing and SDK/device compatibility.") }
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

    private suspend fun permission(permission: Permission): Boolean {
        if (Wearables.checkPermissionStatus(permission).getOrDefault(PermissionStatus.Denied) == PermissionStatus.Granted) return true
        if (permissionResult != null) { fail("Complete the pending glasses permission request first."); return false }
        val result = CompletableDeferred<PermissionStatus>()
        permissionResult = result
        return try {
            permissionLauncher.launch(permission)
            withTimeoutOrNull(60_000) { result.await() } == PermissionStatus.Granted
        } finally { if (permissionResult === result) permissionResult = null }
    }

    fun startCamera() = runCommand {
        if (camera != null || cameraStarting) return@runCommand
        val connected = session?.takeIf { state.value.connection == "STARTED" }
            ?: run { fail("Connect glasses before starting the camera."); return@runCommand }
        cameraStarting = true
        val generation = ++cameraGeneration
        try {
            if (!permission(Permission.CAMERA)) { fail("Glasses camera permission was not granted."); return@runCommand }
            if (session !== connected || generation != cameraGeneration) return@runCommand
            connected.addCamera(StreamConfiguration(videoQuality = VideoQuality.MEDIUM, frameRate = 15)).onSuccess { added ->
                camera = added
                val collectorJob = SupervisorJob(sessionJob)
                cameraCollectors = collectorJob
                val collectors = CoroutineScope(scope.coroutineContext + collectorJob)
                collectors.launch { added.stream.state.collect { status -> mutableState.update { it.copy(camera = status.name) } } }
                collectors.launch { added.stream.errorStream.collect { fail("Glasses camera failed: ${it.description}") } }
                added.stream.start().onFailure { _, _ -> fail("Glasses camera could not start.") }
            }.onFailure { _, _ -> fail("Camera is unavailable on this glasses session.") }
        } finally { cameraStarting = false }
    }

    fun stopCamera() {
        cameraGeneration++
        cameraCollectors?.cancel(); cameraCollectors = null
        camera?.stop(); session?.removeCamera(); camera = null
        mutableState.update { it.copy(camera = "STOPPED") }
    }

    /** JPEG in memory only. Caller must enforce its own event/evidence consent gate. */
    suspend fun capturePhoto(): ByteArray? {
        val active = camera ?: run { fail("Start the glasses camera first."); return null }
        if (active.stream.state.value != StreamState.STREAMING) { fail("Wait for the glasses camera to start."); return null }
        val photo = withTimeoutOrNull(10_000) { active.stream.capturePhoto().getOrNull() }
            ?: run { fail("Glasses photo capture failed or timed out."); return null }
        return withContext(Dispatchers.Default) {
            val bitmap = when (photo) {
                is PhotoData.Bitmap -> photo.bitmap
                is PhotoData.HEIC -> BitmapFactory.decodeByteArray(photo.data, 0, photo.data.size)
            } ?: return@withContext null
            ByteArrayOutputStream().use { out ->
                if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 85, out)) return@withContext null
                out.toByteArray().takeIf { it.size <= 5 * 1024 * 1024 }
            }
        }
    }

    fun startListening() = runCommand {
        if (speechStarting) return@runCommand
        val connected = session?.takeIf { state.value.connection == "STARTED" }
            ?: run { fail("Connect glasses before listening."); return@runCommand }
        speechStarting = true
        listeningRequested = true
        val generation = ++listenGeneration
        try {
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
            speech?.start()?.onFailure { _, _ -> fail("Glasses speech could not start. Use the phone microphone.") }
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
        if (motionHandle != null) { motionHandle?.start(); return }
        val connected = session?.takeIf { state.value.connection == "STARTED" }
            ?: run { fail("Connect glasses before enabling motion."); return }
        connected.addMotion(MotionConfiguration(samplingRate = MotionSamplingRate.HZ_5)).onSuccess { added ->
            motionHandle = added
            sessionScope?.launch { added.state.collect { status -> mutableState.update { it.copy(motion = status.name) } } }
            sessionScope?.launch { added.errors.collect { error -> if (error != null) fail("Glasses motion unavailable: ${error.description}") } }
            sessionScope?.launch { added.samples.collect { sample ->
                if (sample.source == MotionSource.GLASSES) sample.accelerometer?.let {
                    mutableMotion.value = MotionReading(sample.timestampNs, it.x, it.y, it.z)
                }
            } }
            added.start()
        }.onFailure { _, _ -> mutableState.update { it.copy(motion = "UNAVAILABLE", error = "Motion is unavailable on these glasses.") } }
    }
    fun stopMotion() { motionHandle?.stop(); mutableMotion.value = null }

    suspend fun showAnswer(text: String): Boolean {
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
        listeningRequested = false; listenGeneration++; cameraGeneration++
        permissionResult?.cancel(); permissionResult = null
        sessionJob?.cancel(); sessionJob = null; sessionScope = null
        speech?.stop(); motionHandle?.stop(); camera?.stop()
        val old = session; session = null
        old?.stop()
        speechCollectors = null; cameraCollectors = null
        speech = null; motionHandle = null; camera = null; display = null
        mutableMotion.value = null
        mutableState.update { it.copy(connection = "DISCONNECTED", camera = "STOPPED", display = "UNAVAILABLE", speech = "STOPPED", motion = "STOPPED") }
    }
    fun close() { disconnect(); job.cancel(); permissionLauncher.unregister() }
}
