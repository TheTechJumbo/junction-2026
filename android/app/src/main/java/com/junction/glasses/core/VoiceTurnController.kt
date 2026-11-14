package com.junction.glasses.core

class AudioInterrupted : Exception("Audio interrupted")

interface AudioOutput {
    fun playMp3(bytes: ByteArray, onComplete: (Result<Unit>) -> Unit): Cancelable
    fun speakFallback(text: String, onComplete: (Result<Unit>) -> Unit): Cancelable
}
enum class TurnPhase { IDLE, ASKING, FETCHING_SPEECH, PLAYING, FALLBACK, COMPLETE, ERROR, CANCELLED }
data class TurnState(val phase: TurnPhase, val question: String = "", val answer: Answer? = null,
    val message: String? = null)

/** All methods and callbacks are serialized by dispatch (normally the main-thread Handler).
 * A generation is invalidated before cancellation, including synchronous cancellation callbacks. */
class VoiceTurnController(private val backend: BackendClient, private val audio: AudioOutput,
    private val dispatch: (() -> Unit) -> Unit, private val onState: (TurnState) -> Unit) {
    private var generation = 0L
    private val pending = mutableListOf<Cancelable>()
    private var state = TurnState(TurnPhase.IDLE)
    fun submit(question: String) = dispatch {
        invalidate()
        val q = question.trim()
        if (q.isEmpty() || q.length > 2000) { publish(TurnState(TurnPhase.ERROR, message = "Ask a question between 1 and 2000 characters.")); return@dispatch }
        val id = generation
        publish(TurnState(TurnPhase.ASKING, q))
        track(id, backend.ask(q) { result -> dispatch answerResult@ {
            if (id != generation) return@answerResult
            result.fold(onSuccess = { answer ->
                publish(TurnState(TurnPhase.FETCHING_SPEECH, q, answer))
                fetchSpeech(id, answer)
            }, onFailure = { publish(TurnState(TurnPhase.ERROR, q, message = "Could not reach the answer service. Check the connection and session token.")) })
        } })
    }
    fun cancel() = dispatch { invalidate(); publish(state.copy(phase = TurnPhase.CANCELLED, message = null)) }
    fun replay() = dispatch {
        val answer = state.answer ?: return@dispatch
        invalidate()
        publish(state.copy(phase = TurnPhase.FETCHING_SPEECH, message = null))
        fetchSpeech(generation, answer)
    }
    private fun fetchSpeech(id: Long, answer: Answer) {
        if (answer.text.length > 2000) { fallback(id, answer); return }
        track(id, backend.speak(answer.text) { result -> dispatch {
            if (id != generation) return@dispatch
            result.fold(onSuccess = { bytes ->
                publish(state.copy(phase = TurnPhase.PLAYING))
                track(id, audio.playMp3(bytes) { played -> dispatch {
                    if (id == generation) {
                        if (played.isSuccess) publish(state.copy(phase = TurnPhase.COMPLETE))
                        else if (played.exceptionOrNull() is AudioInterrupted) publish(state.copy(phase = TurnPhase.CANCELLED, message = "Audio interrupted."))
                        else fallback(id, answer)
                    }
                } })
            }, onFailure = { fallback(id, answer) })
        } })
    }
    private fun fallback(id: Long, answer: Answer) {
        publish(state.copy(phase = TurnPhase.FALLBACK, message = "Using phone speech."))
        track(id, audio.speakFallback(answer.text) { result -> dispatch {
            if (id == generation) publish(state.copy(phase = if (result.isSuccess) TurnPhase.COMPLETE else TurnPhase.ERROR,
                message = if (result.isSuccess) null else "Audio unavailable. Read the answer on screen."))
        } })
    }
    private fun track(id: Long, operation: Cancelable) { if (id == generation) pending.add(operation) else operation.cancel() }
    private fun invalidate() { generation++; val old = pending.toList(); pending.clear(); old.forEach { it.cancel() } }
    private fun publish(next: TurnState) { state = next; onState(next) }
}
