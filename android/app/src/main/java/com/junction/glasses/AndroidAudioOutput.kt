package com.junction.glasses

import android.content.Context
import android.media.*
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import com.junction.glasses.core.AudioInterrupted
import com.junction.glasses.core.AudioOutput
import com.junction.glasses.core.Cancelable
import java.util.Locale
import java.util.UUID

/** Audio is kept in memory. Android's selected media route controls phone/Bluetooth output. */
class AndroidAudioOutput(context: Context) : AudioOutput {
    private val main = Handler(Looper.getMainLooper())
    private val manager = context.getSystemService(AudioManager::class.java)
    private var player: MediaPlayer? = null
    private var source: MemoryAudio? = null
    private var focus: AudioFocusRequest? = null
    private var finished: ((Result<Unit>) -> Unit)? = null
    private var ttsReady = false
    private var ttsCallback: ((Result<Unit>) -> Unit)? = null
    private var ttsId: String? = null
    private val tts = TextToSpeech(context.applicationContext) { status ->
        ttsReady = status == TextToSpeech.SUCCESS
    }
    init {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) {}
            override fun onDone(id: String?) = completeTts(id, Result.success(Unit))
            @Deprecated("Android compatibility")
            override fun onError(id: String?) = completeTts(id, Result.failure(IllegalStateException("Speech unavailable")))
        })
    }
    private fun completeTts(id: String?, result: Result<Unit>) { main.post {
        if (id == ttsId) { val cb = ttsCallback; ttsCallback = null; ttsId = null; releaseFocus(); cb?.invoke(result) }
    } }
    private fun acquireFocus(): Boolean {
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setOnAudioFocusChangeListener({ change ->
                if (change < 0) {
                    val cb = finished ?: ttsCallback
                    stop()
                    cb?.invoke(Result.failure(AudioInterrupted()))
                }
            }, main).build()
        focus = request
        return manager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }
    private fun releaseFocus() { focus?.let(manager::abandonAudioFocusRequest); focus = null }
    override fun playMp3(bytes: ByteArray, onComplete: (Result<Unit>) -> Unit): Cancelable {
        stop()
        if (!acquireFocus()) { releaseFocus(); onComplete(Result.failure(AudioInterrupted())); return Cancelable {} }
        val current = MediaPlayer()
        player = current
        finished = onComplete
        fun complete(result: Result<Unit>) {
            if (player !== current) return
            val cb = finished
            stop()
            cb?.invoke(result)
        }
        try {
            source = MemoryAudio(bytes.copyOf())
            current.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            current.setDataSource(source!!)
            current.setOnPreparedListener {
                if (player === current) try { it.start() }
                catch (_: Exception) { complete(Result.failure(IllegalStateException("Audio unavailable"))) }
            }
            current.setOnCompletionListener { complete(Result.success(Unit)) }
            current.setOnErrorListener { _, _, _ -> complete(Result.failure(IllegalStateException("Audio unavailable"))); true }
            current.prepareAsync()
        } catch (_: Exception) { complete(Result.failure(IllegalStateException("Audio unavailable"))) }
        return Cancelable { if (player === current) stop() }
    }
    override fun speakFallback(text: String, onComplete: (Result<Unit>) -> Unit): Cancelable {
        stop()
        val voice = if (!ttsReady) null else tts.voices?.firstOrNull { !it.isNetworkConnectionRequired && it.locale.language == Locale.getDefault().language }
        if (!ttsReady || voice == null || !acquireFocus()) {
            releaseFocus(); onComplete(Result.failure(IllegalStateException("Offline phone speech unavailable"))); return Cancelable {}
        }
        tts.voice = voice
        tts.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
        val id = UUID.randomUUID().toString()
        ttsId = id; ttsCallback = onComplete
        if (tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, id) == TextToSpeech.ERROR) {
            completeTts(id, Result.failure(IllegalStateException("Speech unavailable")))
        }
        return Cancelable { if (ttsId == id) stop() }
    }
    fun stop() {
        finished = null; ttsCallback = null; ttsId = null
        player?.release(); player = null
        source?.close(); source = null
        tts.stop(); releaseFocus()
    }
    fun close() { stop(); tts.shutdown() }
    private class MemoryAudio(private var data: ByteArray) : MediaDataSource() {
        @Synchronized override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
            if (position < 0 || position >= data.size) return -1
            val count = minOf(size, data.size - position.toInt())
            data.copyInto(buffer, offset, position.toInt(), position.toInt() + count)
            return count
        }
        @Synchronized override fun getSize(): Long = data.size.toLong()
        @Synchronized override fun close() { data.fill(0); data = byteArrayOf() }
    }
}
