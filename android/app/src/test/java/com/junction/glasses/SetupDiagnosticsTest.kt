package com.junction.glasses

import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import com.junction.glasses.core.*
import com.junction.glasses.meta.MetaGlassesAdapter
import android.widget.CheckBox
import kotlinx.coroutines.Job
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.RecordedRequest
import java.util.concurrent.LinkedBlockingQueue
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.android.controller.ActivityController
import org.robolectric.annotation.Config
import org.robolectric.annotation.LooperMode
import java.util.concurrent.TimeUnit

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [32])
@LooperMode(LooperMode.Mode.PAUSED)
class SetupDiagnosticsTest {
    private lateinit var owner: ActivityController<MainActivity>
    private lateinit var activity: MainActivity
    private lateinit var server: MockWebServer
    private var backgrounded = false
    // Backend probes are answered in queue order; the wearer screen's own /timeline refresh is answered apart.
    private val probes = LinkedBlockingQueue<MockResponse>()
    @Before fun setup() {
        MainActivity.automaticSetup = false // manual setup controls only; see AutomaticSetupActivityTest
        MainActivity.alwaysOnAutoStart = false
        MainActivity.autoSampling = false
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                if (request.path?.startsWith("/timeline") == true) MockResponse().setResponseCode(404)
                else probes.poll(5, TimeUnit.SECONDS) ?: MockResponse().setResponseCode(503)
        }
        server.start()
        owner = Robolectric.buildActivity(MainActivity::class.java).setup()
        activity = owner.get()
        (field("endpoint").get(activity) as EditText).setText(server.url("/").toString())
        (field("token").get(activity) as EditText).setText("synthetic-setup-token")
    }
    @After fun cleanup() {
        if (!backgrounded) owner.pause().stop()
        owner.destroy(); server.shutdown(); drain()
        MainActivity.automaticSetup = true
        MainActivity.alwaysOnAutoStart = true
        MainActivity.autoSampling = true
    }
    @Test fun connectionRequiresAuthenticatedResponseAndCanRecheckWithoutRetypingToken() {
        probes.add(page())
        button("Connect backend").performClick()
        assertNull(field("controller").get(activity))
        assertTrue(text("backendStatus").contains("checking"))
        await { text("backendStatus").contains("authenticated") }
        assertNotNull(field("controller").get(activity))
        assertEquals("", (field("token").get(activity) as EditText).text.toString())
        val request = server.takeRequest(1, TimeUnit.SECONDS)!!
        assertEquals("/events?limit=1", request.path)
        assertEquals("Bearer synthetic-setup-token", request.getHeader("Authorization"))
        probes.add(page()); button("Connect backend").performClick()
        await { text("backendStatus").contains("authenticated") }
        assertNotNull(field("controller").get(activity))
    }
    @Test fun rejectedTokenNeverEnablesQuestions() {
        probes.add(MockResponse().setResponseCode(401))
        button("Connect backend").performClick()
        await { text("backendStatus").contains("token rejected") }
        assertNull(field("controller").get(activity))
        assertNull(field("evidenceClient").get(activity))
    }
    @Test fun stopPreventsDelayedProbeFromConnecting() {
        probes.add(page().setBodyDelay(250, TimeUnit.MILLISECONDS))
        button("Connect backend").performClick()
        assertNotNull(server.takeRequest(1, TimeUnit.SECONDS))
        button("Stop").performClick()
        Thread.sleep(350); drain()
        assertNull(field("controller").get(activity))
        assertTrue(text("backendStatus").contains("cancelled"))
    }
    @Test fun backgroundPreventsDelayedProbeFromConnecting() {
        probes.add(page().setBodyDelay(250, TimeUnit.MILLISECONDS))
        button("Connect backend").performClick()
        assertNotNull(server.takeRequest(1, TimeUnit.SECONDS))
        owner.pause().stop(); backgrounded = true
        Thread.sleep(350); drain()
        assertNull(field("controller").get(activity))
        assertTrue(text("backendStatus").contains("cancelled"))
    }
    @Test fun missingGlassesTestsExplainNextStepAndNeverClaimSuccess() {
        button("Test display").performClick(); drain()
        assertTrue(text("diagnosticStatus").contains("no display test needed"))
        button("Test camera — capture and discard").performClick(); drain()
        assertTrue(text("diagnosticStatus").contains("no photo captured"))
        assertTrue(text("permissionStatus").contains("not allowed"))
    }
    @Test fun reconnectClearsOldObservationsBeforeProbeCompletes() {
        field("latestEvidence").set(activity, listOf(Observation("old-event", "pillbox_open", "sensor", "2026-11-14T10:00:00Z", "kitchen", null, 1.0, null)))
        (field("answerView").get(activity) as TextView).text = "Old server answer"
        probes.add(page())
        button("Connect backend").performClick()
        assertTrue((field("latestEvidence").get(activity) as List<*>).isEmpty())
        assertFalse(text("answerView").contains("Old server"))
        await { text("backendStatus").contains("authenticated") }
    }
    @Test fun askCancelsPendingHardwareDiagnostic() {
        val pending = Job()
        field("diagnosticJob").set(activity, pending)
        (field("diagnosticStatus").get(activity) as TextView).text = "Display: sending test…"
        button("Ask").performClick()
        assertTrue(pending.isCancelled)
        assertTrue(text("diagnosticStatus").contains("cancelled"))
    }
    @Test fun gen2DefaultsToAudioWithOptionalDisplayExplicitlyEnabled() {
        val adapter = field("glasses").get(activity) as MetaGlassesAdapter
        assertFalse(adapter.state.value.displayEnabled)
        assertEquals("NOT_REQUIRED", adapter.state.value.display)
        assertEquals(View.GONE, button("Test display").visibility)
        val option = descendants(activity.window.decorView).filterIsInstance<CheckBox>().first()
        option.isChecked = true; drain()
        assertTrue(adapter.state.value.displayEnabled)
        assertEquals(View.VISIBLE, button("Test display").visibility)
        option.isChecked = false; drain()
        assertEquals("NOT_REQUIRED", adapter.state.value.display)
        assertEquals(View.GONE, button("Test display").visibility)
    }
    @Test fun repeatingDirectionsRequestsFreshEstimateInsteadOfOldAudio() {
        val questions = mutableListOf<String>()
        val backend = object : BackendClient {
            override fun ask(question: String, callback: (Result<Answer>) -> Unit): Cancelable {
                questions += question; return Cancelable {}
            }
            override fun speak(text: String, callback: (Result<ByteArray>) -> Unit): Cancelable = error("No old direction audio expected")
        }
        val audio = object : AudioOutput {
            override fun playMp3(bytes: ByteArray, onComplete: (Result<Unit>) -> Unit): Cancelable = error("No audio expected")
            override fun speakFallback(text: String, onComplete: (Result<Unit>) -> Unit): Cancelable = error("No audio expected")
        }
        field("controller").set(activity, VoiceTurnController(backend, audio, { it() }, {}))
        (field("question").get(activity) as EditText).setText("Where is the bathroom?")
        button("Ask").performClick(); button("Replay answer").performClick()
        assertEquals(listOf("Where is the bathroom?", "Where is the bathroom?"), questions)
    }
    private fun page() = MockResponse().setHeader("Content-Type", "application/json")
        .setBody("""{"events":[],"next_cursor":null}""")
    private fun field(name: String) = MainActivity::class.java.getDeclaredField(name).apply { isAccessible = true }
    private fun text(name: String) = (field(name).get(activity) as TextView).text.toString()
    private fun drain() { shadowOf(Looper.getMainLooper()).idle() }
    private fun await(condition: () -> Boolean) {
        val end = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        while (!condition() && System.nanoTime() < end) { Thread.sleep(10); drain() }
        assertTrue("Expected setup state not reached", condition())
    }
    private fun button(label: String) = descendants(activity.window.decorView)
        .filterIsInstance<Button>().first { it.text.toString() == label }
    private fun descendants(view: View): Sequence<View> = sequence {
        yield(view)
        if (view is ViewGroup) for (i in 0 until view.childCount) yieldAll(descendants(view.getChildAt(i)))
    }

    @Test fun holdingDebugOpensAPanelWithKeyPrintsAndNoSecrets() {
        field("relayKey").set(activity, "synthetic-relay-key")
        val debug = descendants(activity.window.decorView).filterIsInstance<TextView>()
            .first { it.contentDescription?.toString()?.startsWith("Debug panel") == true }
        assertTrue(debug.performLongClick())
        drain()
        val dialog = org.robolectric.shadows.ShadowAlertDialog.getLatestAlertDialog()
        assertNotNull(dialog)
        assertTrue(dialog.isShowing)
        val report = descendants(dialog.window!!.decorView).filterIsInstance<TextView>()
            .first { it.text.toString().contains("Relay key print") }.text.toString()
        assertTrue(report.contains("Relay key print: ${KeyPrint.of("synthetic-relay-key")}"))
        assertTrue(report.contains("Glasses:"))
        assertTrue(report.contains("Chunks waiting on the phone"))
        assertFalse(report.contains("synthetic-relay-key"))
        assertFalse(report.contains("synthetic-setup-token"))
        dialog.dismiss(); drain()
    }

    @Test fun theDebugPanelChangesSettingsWithoutEverShowingAKey() {
        field("relayKey").set(activity, "synthetic-relay-key")
        descendants(activity.window.decorView).filterIsInstance<TextView>()
            .first { it.contentDescription?.toString()?.startsWith("Debug panel") == true }.performLongClick()
        drain()
        val panel = org.robolectric.shadows.ShadowAlertDialog.getLatestAlertDialog()
        descendants(panel.window!!.decorView).filterIsInstance<Button>().first { it.text.toString() == "Change settings" }.performClick()
        drain()
        val settings = org.robolectric.shadows.ShadowAlertDialog.getLatestAlertDialog()
        assertNotSame(panel, settings)
        val fields = descendants(settings.window!!.decorView).filterIsInstance<EditText>().toList()
        assertEquals(7, fields.size)
        assertTrue("keys and tokens are never filled in", fields.none { it.text.toString().contains("synthetic") })
        fields[1].setText("http://192.168.1.20:8090")
        fields[3].setText("test-zone")
        fields[4].setText("venue-a")
        fields[5].setText("") // leave the backend alone
        settings.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick()
        drain()
        assertEquals("http://192.168.1.20:8090", field("relayLanUrl").get(activity))
        assertEquals("test-zone", field("relayZone").get(activity))
        assertEquals("synthetic-relay-key", field("relayKey").get(activity))
        assertEquals("venue-a", activity.getSharedPreferences("venue", android.content.Context.MODE_PRIVATE).getString("zone", null))
        val note = descendants(panel.window!!.decorView).filterIsInstance<TextView>().map { it.text.toString() }
        assertTrue(note.toString(), note.any { it.startsWith("Saved: relay, map zone") })
        panel.dismiss(); drain()
    }
}
