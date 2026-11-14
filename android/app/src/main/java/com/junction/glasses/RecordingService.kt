package com.junction.glasses

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.BatteryManager
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.junction.glasses.live.AlwaysOn
import com.junction.glasses.live.AlwaysOnRecorder
import com.junction.glasses.live.AlwaysOnRules
import com.junction.glasses.live.AlwaysOnStatus
import com.junction.glasses.live.BatteryReading
import com.junction.glasses.live.CameraSource
import com.junction.glasses.live.ChunkQueue
import com.junction.glasses.live.FailoverRelay
import com.junction.glasses.live.GlassesCamera
import com.junction.glasses.live.PhoneCamera
import com.junction.glasses.live.RelayClient
import kotlinx.coroutines.launch
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The always-on recording as a foreground service, so it keeps going with the screen off or in other
 * apps until Stop (on the card or in the notification). It holds the glasses session through
 * GlassesHub (or binds the phone camera to its own lifecycle), keeps the CPU awake while recording, and
 * shows a persistent notification: frames sent, glasses state, last upload, battery. Started by the
 * Activity while it is in front (Android 14 requires that for the camera type); the relay settings
 * come through [AlwaysOn.pending], never in an Intent.
 */
class RecordingService : LifecycleService() {
    private var wakeLock: PowerManager.WakeLock? = null
    private var lastNotifiedAt = 0L

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) stopRecording() else startRecording()
        return START_NOT_STICKY
    }

    private fun startRecording() {
        if (AlwaysOn.recorder != null) return
        val config = AlwaysOn.pending ?: run { stopSelf(); return }
        AlwaysOn.pending = null
        val glasses = GlassesHub.current
        val types = ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC or when (config.source) {
            CameraSource.GLASSES -> ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
            CameraSource.PHONE -> ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        }
        createChannel()
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(AlwaysOnStatus(recording = true, source = config.source, zone = config.zone)), types)
        } catch (e: Exception) {
            Log.w(TAG, "could not start in the foreground: ${e.javaClass.simpleName}")
            AlwaysOn.status.value = AlwaysOnStatus(waiting = "Always-on recording could not start (${e.javaClass.simpleName}). Open the app and tap Start.")
            stopSelf(); return
        }
        val camera = when (config.source) {
            CameraSource.GLASSES -> glasses?.let { GlassesCamera(it) }
            CameraSource.PHONE -> PhoneCamera(this, this)
        } ?: run {
            AlwaysOn.status.value = AlwaysOnStatus(waiting = "The glasses are not set up yet. Open the app.")
            stopForegroundAndSelf(); return
        }
        val primary = RelayClient(config.relayUrl, config.key, config.zone, allowLocalHttp = BuildConfig.DEBUG)
        val lan = config.lanUrl?.let { url -> try { RelayClient(url, config.key, config.zone, allowLocalHttp = BuildConfig.DEBUG) } catch (_: Exception) { null } }
        val session = "aon-" + LocalDateTime.now().format(SESSION_TIME)
        val recorder = AlwaysOnRecorder(
            scope = lifecycleScope,
            camera = camera,
            relay = FailoverRelay(primary, lan),
            queue = ChunkQueue(File(filesDir, "chunks")),
            zone = config.zone,
            session = session,
            source = config.source,
            glassesWaiting = {
                if (config.source != CameraSource.GLASSES) null
                else glasses?.state?.value?.let { AlwaysOnRules.glassesWaiting(it.registration, it.connection, it.camera) }
            },
            battery = { readBattery() },
        )
        AlwaysOn.recorder = recorder
        acquireWakeLock()
        Log.i(TAG, "always-on recording started: ${config.source}, zone ${config.zone}, session $session, LAN fallback ${lan != null}")
        recorder.start()
        lifecycleScope.launch {
            recorder.status.collect { status ->
                AlwaysOn.status.value = status
                val now = SystemClock.elapsedRealtime()
                if (now - lastNotifiedAt >= NOTIFY_EVERY_MS || !status.recording) {
                    lastNotifiedAt = now
                    getSystemService(NotificationManager::class.java)?.notify(NOTIFICATION_ID, notification(status))
                }
            }
        }
    }

    private fun stopRecording() {
        AlwaysOn.stoppedByUser = true
        val recorder = AlwaysOn.recorder ?: run { stopForegroundAndSelf(); return }
        lifecycleScope.launch {
            recorder.stop()
            Log.i(TAG, "always-on recording stopped: ${recorder.status.value.chunksSent} chunks sent, ${recorder.status.value.chunksWaiting} waiting")
            if (AlwaysOn.recorder === recorder) AlwaysOn.recorder = null
            stopForegroundAndSelf()
        }
    }

    private fun stopForegroundAndSelf() {
        releaseWakeLock()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        if (AlwaysOn.recorder != null) AlwaysOn.status.value = AlwaysOn.status.value.copy(recording = false, stopping = false)
        AlwaysOn.recorder = null
        releaseWakeLock()
        super.onDestroy()
    }

    private fun readBattery(): BatteryReading? {
        val sticky = ContextCompat.registerReceiver(this, null, IntentFilter(Intent.ACTION_BATTERY_CHANGED), ContextCompat.RECEIVER_NOT_EXPORTED)
            ?: return null
        val level = sticky.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = sticky.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        val tenths = sticky.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)
        return BatteryReading(
            percent = if (level >= 0 && scale > 0) level * 100 / scale else null,
            temperatureC = if (tenths != Int.MIN_VALUE) tenths / 10.0 else null,
        )
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        wakeLock = getSystemService(PowerManager::class.java)?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "junction:always-on")?.apply {
            setReferenceCounted(false)
            acquire(MAX_WAKE_MS)
        }
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(NotificationChannel(CHANNEL_ID, "Always-on recording", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shows while the room is being recorded for the laptop, with a Stop button."
            })
        }
    }

    private fun notification(status: AlwaysOnStatus): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, RecordingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setContentTitle("Recording the room for the laptop")
            .setContentText(summary(status))
            .setStyle(NotificationCompat.BigTextStyle().bigText(details(status)))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(android.R.drawable.ic_media_pause, "Stop", stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    companion object {
        private const val TAG = "AlwaysOn"
        const val ACTION_START = "com.junction.glasses.action.START_RECORDING"
        const val ACTION_STOP = "com.junction.glasses.action.STOP_RECORDING"
        private const val CHANNEL_ID = "always_on_recording"
        private const val NOTIFICATION_ID = 42
        private const val NOTIFY_EVERY_MS = 2_000L
        /** A safety limit on the wake lock; a recording longer than this keeps going, with the screen able to doze. */
        private const val MAX_WAKE_MS = 12 * 60 * 60 * 1000L
        private val SESSION_TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss", Locale.US)

        /** One line: what the notification shows collapsed. */
        internal fun summary(s: AlwaysOnStatus): String = listOfNotNull(
            s.source.label,
            "${s.framesCaptured} frames",
            "${s.chunksSent} chunks sent",
            s.batteryPercent?.let { "battery $it%" },
        ).joinToString(" · ")

        /** The notification's and the card's details; never the key. */
        internal fun details(s: AlwaysOnStatus): String = listOfNotNull(
            if (s.heatPaused) "Paused: the phone is at ${s.temperatureC?.let { String.format(Locale.US, "%.1f", it) }} °C (above ${AlwaysOnRules.HOT_C.toInt()} °C). Recording resumes below ${AlwaysOnRules.COOL_C.toInt()} °C." else null,
            s.waiting,
            "${s.source.label} · zone ${s.zone}" + (s.session?.let { " · $it" } ?: ""),
            "${s.framesCaptured} frames · ${s.chunksSent} chunks sent · ${s.chunksWaiting} waiting" +
                (if (s.bytesWaiting > 0) String.format(Locale.US, " (%.1f MB)", s.bytesWaiting / (1024.0 * 1024.0)) else "") +
                (if (s.chunksDropped > 0) " · ${s.chunksDropped} dropped" else ""),
            s.lastUpload?.let { "Last upload: $it" + if (s.viaLan) " (Wi-Fi)" else "" },
            s.uploadError,
            s.instantFix?.let { "Instant fix: $it" },
            listOfNotNull(s.batteryPercent?.let { "Battery $it%" }, s.temperatureC?.let { String.format(Locale.US, "%.1f °C", it) })
                .joinToString(" · ").ifEmpty { null },
        ).joinToString("\n")

        /** Starts the recording from the Activity (in front): the settings go through [AlwaysOn.pending]. */
        internal fun start(context: Context, config: AlwaysOn.Config) {
            AlwaysOn.pending = config
            AlwaysOn.stoppedByUser = false
            ContextCompat.startForegroundService(context, Intent(context, RecordingService::class.java).setAction(ACTION_START))
        }

        internal fun stop(context: Context) {
            AlwaysOn.stoppedByUser = true
            context.startService(Intent(context, RecordingService::class.java).setAction(ACTION_STOP))
        }
    }
}
