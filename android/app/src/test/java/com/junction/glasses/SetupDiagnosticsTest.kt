package com.junction.glasses

import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import com.junction.glasses.core.Observation
import kotlinx.coroutines.Job
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
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
    @Before fun setup() {
        server = MockWebServer(); server.start()
        owner = Robolectric.buildActivity(MainActivity::class.java).setup()
        activity = owner.get()
        (field("endpoint").get(activity) as EditText).setText(server.url("/").toString())
        (field("token").get(activity) as EditText).setText("synthetic-setup-token")
    }
    @After fun cleanup() {
        if (!backgrounded) owner.pause().stop()
        owner.destroy(); server.shutdown(); drain()
    }
    @Test fun connectionRequiresAuthenticatedResponseAndCanRecheckWithoutRetypingToken() {
        server.enqueue(page())
        button("Connect backend").performClick()
        assertNull(field("controller").get(activity))
        assertTrue(text("backendStatus").contains("checking"))
        await { text("backendStatus").contains("authenticated") }
        assertNotNull(field("controller").get(activity))
        assertEquals("", (field("token").get(activity) as EditText).text.toString())
        val request = server.takeRequest(1, TimeUnit.SECONDS)!!
        assertEquals("/events?limit=1", request.path)
        assertEquals("Bearer synthetic-setup-token", request.getHeader("Authorization"))
        server.enqueue(page()); button("Connect backend").performClick()
        await { text("backendStatus").contains("authenticated") }
        assertNotNull(field("controller").get(activity))
    }
    @Test fun rejectedTokenNeverEnablesQuestions() {
        server.enqueue(MockResponse().setResponseCode(401))
        button("Connect backend").performClick()
        await { text("backendStatus").contains("token rejected") }
        assertNull(field("controller").get(activity))
        assertNull(field("evidenceClient").get(activity))
    }
    @Test fun stopPreventsDelayedProbeFromConnecting() {
        server.enqueue(page().setBodyDelay(250, TimeUnit.MILLISECONDS))
        button("Connect backend").performClick()
        assertNotNull(server.takeRequest(1, TimeUnit.SECONDS))
        button("Stop").performClick()
        Thread.sleep(350); drain()
        assertNull(field("controller").get(activity))
        assertTrue(text("backendStatus").contains("cancelled"))
    }
    @Test fun backgroundPreventsDelayedProbeFromConnecting() {
        server.enqueue(page().setBodyDelay(250, TimeUnit.MILLISECONDS))
        button("Connect backend").performClick()
        assertNotNull(server.takeRequest(1, TimeUnit.SECONDS))
        owner.pause().stop(); backgrounded = true
        Thread.sleep(350); drain()
        assertNull(field("controller").get(activity))
        assertTrue(text("backendStatus").contains("cancelled"))
    }
    @Test fun missingGlassesTestsExplainNextStepAndNeverClaimSuccess() {
        button("Test display").performClick(); drain()
        assertTrue(text("diagnosticStatus").contains("test not sent"))
        button("Test camera — capture and discard").performClick(); drain()
        assertTrue(text("diagnosticStatus").contains("no photo captured"))
        assertTrue(text("permissionStatus").contains("not allowed"))
    }
    @Test fun reconnectClearsOldObservationsBeforeProbeCompletes() {
        field("latestEvidence").set(activity, listOf(Observation("old-event", "pillbox_open", "sensor", "2026-11-14T10:00:00Z", "kitchen", null, 1.0, null)))
        (field("answerView").get(activity) as TextView).text = "Old server answer"
        server.enqueue(page())
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
}
