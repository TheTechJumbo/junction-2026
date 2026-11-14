package com.junction.glasses

import android.Manifest
import android.content.Intent
import android.content.pm.ResolveInfo
import android.content.pm.ServiceInfo
import android.os.Bundle
import android.os.Looper
import android.speech.SpeechRecognizer
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import com.junction.glasses.core.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.android.controller.ActivityController
import org.robolectric.annotation.Config
import org.robolectric.annotation.LooperMode
import org.robolectric.shadows.ShadowSpeechRecognizer

/** Exercises actual Activity callbacks; fake network/audio never leave this process. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [32])
@LooperMode(LooperMode.Mode.PAUSED)
class ActivityLifecycleTest {
    private lateinit var owner: ActivityController<MainActivity>
    private lateinit var activity: MainActivity
    private val questions = mutableListOf<String>()
    private var cancels = 0
    private var paused = false
    private var stopped = false

    @Before fun setUp() {
        // These tests drive the manual controls; an automatic launch permission request would
        // otherwise be in flight and cancel the microphone request they exercise.
        MainActivity.automaticSetup = false
        val app = RuntimeEnvironment.getApplication()
        shadowOf(app).grantPermissions(Manifest.permission.RECORD_AUDIO)
        shadowOf(app.packageManager).addResolveInfoForIntent(
            Intent("android.speech.RecognitionService"),
            ResolveInfo().apply { serviceInfo = ServiceInfo().apply {
                packageName = "test.recognizer"; name = "TestRecognitionService"
            } },
        )
        owner = Robolectric.buildActivity(MainActivity::class.java).setup()
        activity = owner.get()
        val backend = object : BackendClient {
            override fun ask(question: String, callback: (Result<Answer>) -> Unit): Cancelable {
                questions += question
                return Cancelable { cancels++ }
            }
            override fun speak(text: String, callback: (Result<ByteArray>) -> Unit): Cancelable =
                error("No answer callback is delivered in lifecycle tests")
        }
        val audio = object : AudioOutput {
            override fun playMp3(bytes: ByteArray, onComplete: (Result<Unit>) -> Unit): Cancelable =
                error("No audio expected")
            override fun speakFallback(text: String, onComplete: (Result<Unit>) -> Unit): Cancelable =
                error("No audio expected")
        }
        field("controller").set(activity, VoiceTurnController(backend, audio, { it() }, {}))
        drain()
    }

    @After fun tearDown() {
        if (!paused) owner.pause()
        if (!stopped) owner.stop()
        owner.destroy()
        drain()
        MainActivity.automaticSetup = true
    }

    @Test fun stopRejectsAlreadyQueuedRecognitionAndErrorCallbacks() {
        val speech = listen()
        button("Stop").performClick()
        val previousStatus = (field("status").get(activity) as TextView).text.toString()
        speech.triggerOnResults(result("stale question"))
        speech.triggerOnError(SpeechRecognizer.ERROR_NETWORK)
        drain()
        assertTrue(questions.isEmpty())
        assertEquals(previousStatus, (field("status").get(activity) as TextView).text.toString())
    }

    @Test fun replacedRecognizerCannotSubmitButCurrentRecognizerCan() {
        val old = listen()
        val current = listen()
        old.triggerOnResults(result("old question"))
        drain()
        assertTrue(questions.isEmpty())
        current.triggerOnResults(result("current question"))
        drain()
        assertEquals(listOf("current question"), questions)
        current.triggerOnResults(result("duplicate callback"))
        drain()
        assertEquals(listOf("current question"), questions)
    }

    @Test fun backgroundingCancelsTurnAndRejectsLateRecognition() {
        val speech = listen()
        speech.triggerOnResults(result("active question"))
        drain()
        assertEquals(listOf("active question"), questions)
        background()
        assertEquals(1, cancels)
        speech.triggerOnResults(result("late background question"))
        drain()
        assertEquals(listOf("active question"), questions)
    }

    @Test fun pendingPermissionIntentIsClearedByStop() {
        shadowOf(RuntimeEnvironment.getApplication()).denyPermissions(Manifest.permission.RECORD_AUDIO)
        button("Use phone microphone").performClick()
        assertTrue(field("pendingPhoneListen").getBoolean(activity))
        button("Stop").performClick()
        assertFalse(field("pendingPhoneListen").getBoolean(activity))
        assertTrue(questions.isEmpty())
    }

    @Test fun pendingPermissionIntentIsClearedByBackground() {
        shadowOf(RuntimeEnvironment.getApplication()).denyPermissions(Manifest.permission.RECORD_AUDIO)
        button("Use phone microphone").performClick()
        assertTrue(field("pendingPhoneListen").getBoolean(activity))
        background()
        assertFalse(field("pendingPhoneListen").getBoolean(activity))
        assertTrue(questions.isEmpty())
    }

    private fun listen(): ShadowSpeechRecognizer {
        button("Use phone microphone").performClick()
        drain()
        return shadowOf(ShadowSpeechRecognizer.getLatestSpeechRecognizer())
    }
    private fun background() { owner.pause(); paused = true; owner.stop(); stopped = true; drain() }
    private fun field(name: String) = MainActivity::class.java.getDeclaredField(name).apply { isAccessible = true }
    private fun drain() { shadowOf(Looper.getMainLooper()).idle() }
    private fun result(text: String) = Bundle().apply {
        putStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION, arrayListOf(text))
    }
    private fun button(text: String): Button = descendants(activity.window.decorView)
        .filterIsInstance<Button>().first { it.text.toString() == text }
    private fun descendants(view: View): Sequence<View> = sequence {
        yield(view)
        if (view is ViewGroup) for (i in 0 until view.childCount) yieldAll(descendants(view.getChildAt(i)))
    }
}
