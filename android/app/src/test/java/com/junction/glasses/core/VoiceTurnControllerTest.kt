package com.junction.glasses.core

import org.junit.Assert.*
import org.junit.Test

class VoiceTurnControllerTest {
    private class FakeBackend : BackendClient {
        val asks = mutableListOf<(Result<Answer>) -> Unit>()
        val speeches = mutableListOf<(Result<ByteArray>) -> Unit>()
        var cancelled = 0
        override fun ask(question: String, callback: (Result<Answer>) -> Unit): Cancelable { asks.add(callback); return Cancelable { cancelled++ } }
        override fun speak(text: String, callback: (Result<ByteArray>) -> Unit): Cancelable { speeches.add(callback); return Cancelable { cancelled++ } }
    }
    private class FakeAudio : AudioOutput {
        var played = 0
        var cancelled = 0
        val fallbacks = mutableListOf<String>()
        var done: ((Result<Unit>) -> Unit)? = null
        override fun playMp3(bytes: ByteArray, onComplete: (Result<Unit>) -> Unit): Cancelable { played++; done = onComplete; return Cancelable { cancelled++ } }
        override fun speakFallback(text: String, onComplete: (Result<Unit>) -> Unit): Cancelable { fallbacks.add(text); done = onComplete; return Cancelable { cancelled++ } }
    }
    @Test fun newerQuestionIgnoresOldAnswerAndOldSpeech() {
        val b = FakeBackend(); val a = FakeAudio(); val states = mutableListOf<TurnState>()
        val c = VoiceTurnController(b, a, { it() }, states::add)
        c.submit("old"); b.asks[0](Result.success(Answer("old answer", emptyList())))
        c.submit("new"); b.speeches[0](Result.success(byteArrayOf(1)))
        b.asks[0](Result.success(Answer("late old answer", emptyList())))
        assertEquals(0, a.played); assertEquals(TurnPhase.ASKING, states.last().phase)
        b.asks[1](Result.success(Answer("new answer", emptyList())))
        b.speeches[1](Result.success(byteArrayOf(1))); a.done!!(Result.success(Unit))
        assertEquals("new answer", states.last().answer!!.text)
        assertEquals(TurnPhase.COMPLETE, states.last().phase)
        assertTrue(b.cancelled >= 2)
    }
    @Test fun cancelStopsPlaybackAndIgnoresCompletion() {
        val b = FakeBackend(); val a = FakeAudio(); val states = mutableListOf<TurnState>()
        val c = VoiceTurnController(b, a, { it() }, states::add)
        c.submit("question"); b.asks[0](Result.success(Answer("answer", emptyList())))
        b.speeches[0](Result.success(byteArrayOf(1))); c.cancel(); a.done!!(Result.success(Unit))
        assertEquals(1, a.cancelled); assertEquals(TurnPhase.CANCELLED, states.last().phase)
    }
    @Test fun speechFailureFallsBackAndReplayDoesNotRepeatAsk() {
        val b = FakeBackend(); val a = FakeAudio(); val states = mutableListOf<TurnState>()
        val c = VoiceTurnController(b, a, { it() }, states::add)
        c.submit("question"); b.asks[0](Result.success(Answer("observation only", emptyList())))
        b.speeches[0](Result.failure(BackendFailure("http", 503)))
        assertEquals(listOf("observation only"), a.fallbacks)
        a.done!!(Result.failure(Exception("private details")))
        assertEquals(TurnPhase.ERROR, states.last().phase)
        assertFalse(states.last().message!!.contains("private"))
        c.replay(); assertEquals(1, b.asks.size); assertEquals(2, b.speeches.size)
    }
    @Test fun askFailureNeverSpeaksAndBlankInputNeverCallsBackend() {
        val b = FakeBackend(); val a = FakeAudio(); val states = mutableListOf<TurnState>()
        val c = VoiceTurnController(b, a, { it() }, states::add)
        c.submit("  "); assertTrue(b.asks.isEmpty())
        c.submit("q"); b.asks[0](Result.failure(BackendFailure("http", 401)))
        assertTrue(b.speeches.isEmpty()); assertEquals(TurnPhase.ERROR, states.last().phase)
    }
    @Test fun interruptedAudioDoesNotRestartThroughFallback() {
        val b = FakeBackend(); val a = FakeAudio(); val states = mutableListOf<TurnState>()
        val c = VoiceTurnController(b, a, { it() }, states::add)
        c.submit("question"); b.asks[0](Result.success(Answer("answer", emptyList())))
        b.speeches[0](Result.success(byteArrayOf(1))); a.done!!(Result.failure(AudioInterrupted()))
        assertTrue(a.fallbacks.isEmpty()); assertEquals(TurnPhase.CANCELLED, states.last().phase)
    }
    @Test fun longAnswerStaysReadableAndUsesLocalSpeechOnly() {
        val b = FakeBackend(); val a = FakeAudio(); val states = mutableListOf<TurnState>()
        val c = VoiceTurnController(b, a, { it() }, states::add)
        c.submit("question"); b.asks[0](Result.success(Answer("a".repeat(2001), emptyList())))
        assertTrue(b.speeches.isEmpty()); assertEquals(2001, a.fallbacks.single().length)
        assertEquals(2001, states.last().answer!!.text.length)
    }

}
