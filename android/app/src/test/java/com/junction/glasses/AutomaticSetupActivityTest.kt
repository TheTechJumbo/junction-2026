package com.junction.glasses

import android.Manifest
import android.app.Activity
import android.app.Application
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions
import com.junction.glasses.live.RelayClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
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
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.spec.SecretKeySpec

/**
 * Launch-time setup through real Activity callbacks. Bluetooth is never granted here, so the Meta SDK
 * is not initialised; the glasses decisions are covered by AutoGlassesSetupTest.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [32])
@LooperMode(LooperMode.Mode.PAUSED)
class AutomaticSetupActivityTest {
    private lateinit var server: MockWebServer
    private var owner: ActivityController<MainActivity>? = null
    private val activity: MainActivity get() = owner!!.get()

    // The always-on recording would start a foreground service; it has its own JVM tests (AlwaysOnPartsTest).
    @Before fun setup() { MainActivity.alwaysOnAutoStart = false; server = MockWebServer(); server.start() }
    @After fun cleanup() {
        owner?.pause()?.stop()?.destroy()
        drain(); server.shutdown()
        MainActivity.automaticSetup = true
        MainActivity.alwaysOnAutoStart = true
        MainActivity.backendMemoryOverride = null
    }

    @Test fun debugExtrasConnectBackendWithoutShowingOrKeepingTheToken() {
        MainActivity.automaticSetup = false
        server.enqueue(page())
        launch(debugIntent("synthetic-intent-token"))
        await { text("backendStatus").contains("authenticated") }
        assertNotNull(field("controller").get(activity))
        assertEquals("Bearer synthetic-intent-token", server.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("Authorization"))
        assertEquals(server.url("/").toString(), (field("endpoint").get(activity) as EditText).text.toString())
        assertEquals("", (field("token").get(activity) as EditText).text.toString())
        assertNoVisibleText("synthetic-intent-token")
        assertFalse(activity.intent.hasExtra(DebugBackendLaunch.EXTRA_API_TOKEN))
        assertFalse(activity.intent.hasExtra(DebugBackendLaunch.EXTRA_BACKEND_URL))
        assertEquals("Backend connected", text("backendChip"))
        assertEquals("Glasses need Bluetooth", text("glassesChip"))
        assertEquals("Mic not allowed", text("micChip"))
    }

    @Test fun resentDebugExtrasReconnectTheSameActivity() {
        MainActivity.automaticSetup = false
        server.enqueue(page()); server.enqueue(page())
        launch(debugIntent("synthetic-first-token"))
        await { text("backendStatus").contains("authenticated") }
        assertEquals("Bearer synthetic-first-token", server.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("Authorization"))
        val first = activity
        val adapter = field("glasses").get(first)
        owner!!.newIntent(debugIntent("synthetic-second-token"))
        assertEquals("Bearer synthetic-second-token", server.takeRequest(5, TimeUnit.SECONDS)!!.getHeader("Authorization"))
        await { text("backendStatus").contains("authenticated") && field("controller").get(activity) != null }
        assertSame(first, activity)
        assertSame(adapter, field("glasses").get(activity))
        assertEquals("", (field("token").get(activity) as EditText).text.toString())
        assertNoVisibleText("synthetic-second-token")
    }

    @Test fun restoredActivityNeverReusesDebugExtras() {
        MainActivity.automaticSetup = false
        owner = Robolectric.buildActivity(MainActivity::class.java, debugIntent("synthetic-stale-token")).setup(Bundle())
        drain()
        assertEquals("Backend: not checked.", text("backendStatus"))
        assertEquals(0, server.requestCount)
        assertEquals("", (field("token").get(activity) as EditText).text.toString())
        assertFalse(activity.intent.hasExtra(DebugBackendLaunch.EXTRA_API_TOKEN))
    }

    @Test fun releaseBuildsIgnoreDebugExtrasEntirely() {
        val intent = debugIntent("synthetic-release-token")
        assertNull(DebugBackendLaunch.read(intent, debug = false))
        assertNull(DebugBackendLaunch.read(Intent(), debug = true))
        val parsed = DebugBackendLaunch.read(intent, debug = true)!!
        assertEquals("synthetic-release-token", parsed.apiToken)
        assertEquals(server.url("/").toString(), parsed.backendUrl)
        assertFalse(parsed.toString().contains("synthetic-release-token"))
        val stripped = DebugBackendLaunch.withoutExtras(intent)
        assertFalse(stripped.hasExtra(DebugBackendLaunch.EXTRA_API_TOKEN))
        assertFalse(stripped.hasExtra(DebugBackendLaunch.EXTRA_BACKEND_URL))
        assertEquals(intent.component, stripped.component)
        assertTrue(intent.hasExtra(DebugBackendLaunch.EXTRA_API_TOKEN))
        val relay = Intent(app(), MainActivity::class.java).putExtra(DebugBackendLaunch.EXTRA_RELAY_KEY, "synthetic-relay-key")
        assertNull(DebugBackendLaunch.read(relay, debug = false))
        val relayParsed = DebugBackendLaunch.read(relay, debug = true)!!
        assertTrue(relayParsed.hasRelay)
        assertFalse(relayParsed.hasBackend)
        assertFalse(relayParsed.toString().contains("synthetic-relay-key"))
        assertFalse(DebugBackendLaunch.withoutExtras(relay).hasExtra(DebugBackendLaunch.EXTRA_RELAY_KEY))
    }

    @Test fun launchAsksForPhonePermissionsOnceAndDenialDoesNotLoop() {
        MainActivity.automaticSetup = true
        launch(Intent(app(), MainActivity::class.java))
        val request = shadowOf(activity).lastRequestedPermission
        assertNotNull(request)
        assertEquals(setOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO),
            request.requestedPermissions.toSet())
        val denied = IntArray(request.requestedPermissions.size) { PackageManager.PERMISSION_DENIED }
        activity.activityResultRegistry.dispatchResult(request.requestCode, Activity.RESULT_OK, Intent()
            .putExtra(RequestMultiplePermissions.EXTRA_PERMISSIONS, request.requestedPermissions)
            .putExtra(RequestMultiplePermissions.EXTRA_PERMISSION_GRANT_RESULTS, denied))
        drain()
        owner!!.pause().resume(); drain()
        assertSame(request, shadowOf(activity).lastRequestedPermission)
        assertTrue(text("setupStatus").contains("not all allowed"))
        assertTrue(text("setupStatus").contains("needs Bluetooth access"))
    }

    @Test fun rememberedBackendReconnectsOnLaunchAndForgetClearsIt() {
        MainActivity.automaticSetup = true
        val memory = testMemory("remembered")
        MainActivity.backendMemoryOverride = memory
        assertTrue(memory.save(server.url("/").toString(), "synthetic-remembered-token"))
        server.enqueue(page())
        launch(Intent(app(), MainActivity::class.java))
        await { text("backendStatus").contains("authenticated") }
        assertEquals("Bearer synthetic-remembered-token", server.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("Authorization"))
        assertEquals("", (field("token").get(activity) as EditText).text.toString())
        assertNoVisibleText("synthetic-remembered-token")
        button("Forget saved backend").performClick()
        assertNull(memory.load())
        assertNotNull(field("controller").get(activity))
    }

    @Test fun debugExtrasWinOverRememberedBackendAndReplaceItAfterSuccess() {
        MainActivity.automaticSetup = true
        val memory = testMemory("replaced")
        MainActivity.backendMemoryOverride = memory
        assertTrue(memory.save("http://127.0.0.1:9", "synthetic-old-token"))
        server.enqueue(page())
        launch(debugIntent("synthetic-new-token"))
        await { text("backendStatus").contains("authenticated") }
        assertEquals("Bearer synthetic-new-token", server.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("Authorization"))
        assertEquals(1, server.requestCount)
        assertEquals(server.url("/").toString() to "synthetic-new-token", memory.load())
    }

    @Test fun relayExtrasAreRememberedStrippedAndTheKeyIsNeverShown() {
        MainActivity.automaticSetup = true
        val memory = testMemory("relay")
        MainActivity.backendMemoryOverride = memory
        launch(Intent(app(), MainActivity::class.java)
            .putExtra(DebugBackendLaunch.EXTRA_RELAY_KEY, "synthetic-relay-key")
            .putExtra(DebugBackendLaunch.EXTRA_RELAY_ZONE, "kitchen"))
        assertEquals(Triple(RelayClient.DEFAULT_URL, "synthetic-relay-key", "kitchen"), memory.loadRelay())
        assertFalse(activity.intent.hasExtra(DebugBackendLaunch.EXTRA_RELAY_KEY))
        assertTrue(text("liveDetails").contains("key set"))
        assertNoVisibleText("synthetic-relay-key")
        assertNull(field("liveSources").get(activity))
        button("Forget saved backend").performClick()
        assertNull(memory.loadRelay())
    }

    @Test fun liveCameraWithoutRelayKeyExplainsAndCreatesNoCamera() {
        MainActivity.automaticSetup = false
        launch(Intent(app(), MainActivity::class.java))
        button("Start live camera").performClick()
        assertNull(field("liveSources").get(activity))
        assertNull(field("liveLoop").get(activity))
        assertTrue(text("liveDetails").contains("No relay key"))
    }

    @Test fun liveCameraAsksForCameraPermissionBeforeCreatingAnyCamera() {
        MainActivity.automaticSetup = false
        launch(Intent(app(), MainActivity::class.java).putExtra(DebugBackendLaunch.EXTRA_RELAY_KEY, "synthetic-relay-key"))
        button("Phone").performClick() // the glasses are the default camera
        button("Start live camera").performClick()
        assertNull(field("liveSources").get(activity))
        assertNull(field("liveLoop").get(activity))
        assertTrue(field("pendingLiveCamera").getBoolean(activity))
        assertEquals(listOf(Manifest.permission.CAMERA), shadowOf(activity).lastRequestedPermission.requestedPermissions.toList())
        assertNoVisibleText("synthetic-relay-key")
    }

    @Test fun recordRoomWithoutRelayKeyExplainsAndRecordsNothing() {
        MainActivity.automaticSetup = false
        launch(Intent(app(), MainActivity::class.java))
        button("Record room").performClick()
        assertNull(field("recordJob").get(activity))
        assertNull(field("liveSources").get(activity))
        assertTrue(text("recordStatus").contains("No relay key"))
        assertEquals(RelayClient.DEFAULT_ZONE, (field("zoneField").get(activity) as EditText).text.toString())
    }

    @Test fun recordVideoWithThePhoneAsksForTheCameraFirstAndCreatesNoRecorder() {
        MainActivity.automaticSetup = false
        launch(Intent(app(), MainActivity::class.java))
        button("Phone").performClick() // the glasses are the default camera
        button("Record video").performClick()
        assertNull(field("videoRecorder").get(activity))
        assertEquals(listOf(Manifest.permission.CAMERA), shadowOf(activity).lastRequestedPermission.requestedPermissions.toList())
        assertTrue(text("recordStatus").contains("camera permission"))
    }

    @Test fun recordVideoWithTheGlassesNeedsTheRelayKeyBeforeWatchingForVideos() {
        MainActivity.automaticSetup = false
        launch(Intent(app(), MainActivity::class.java))
        assertEquals("the glasses are the default camera", "GLASSES", field("cameraSource").get(activity).toString())
        button("Record video").performClick()
        assertTrue(text("recordStatus").contains("No relay key"))
        assertNull(field("glassesObserver").get(activity))
        assertEquals("Record video", button("Record video").text.toString())
    }

    @Test fun backendMemoryKeepsOnlyCiphertextAndNeedsTheSameKey() {
        val prefs = app().getSharedPreferences("test_backend_memory_direct", Context.MODE_PRIVATE)
        val memory = BackendMemory(prefs, { testKey(1) }, { softwareCipher() })
        assertNull(memory.load())
        assertTrue(memory.save("http://127.0.0.1:8000", "synthetic-sealed-token"))
        assertEquals("http://127.0.0.1:8000" to "synthetic-sealed-token", memory.load())
        val stored = prefs.all.values.joinToString()
        assertFalse(stored.contains("synthetic-sealed-token"))
        assertFalse(stored.contains("127.0.0.1"))
        assertNull(BackendMemory(prefs, { testKey(2) }, { softwareCipher() }).load())
        memory.forget()
        assertNull(memory.load())
    }

    private fun app(): Application = RuntimeEnvironment.getApplication()
    /** Robolectric has no AndroidKeyStore; the same AES/GCM code runs with a software key here. */
    private fun testMemory(name: String) =
        BackendMemory(app().getSharedPreferences("test_backend_memory_$name", Context.MODE_PRIVATE), { testKey(1) }, { softwareCipher() })
    private fun testKey(seed: Int) = SecretKeySpec(ByteArray(32) { (it + seed).toByte() }, "AES")
    private fun softwareCipher(): Cipher = Cipher.getInstance("AES/GCM/NoPadding", "SunJCE")
    private fun launch(intent: Intent) {
        owner = Robolectric.buildActivity(MainActivity::class.java, intent).setup()
        drain()
    }
    private fun debugIntent(token: String) = Intent(app(), MainActivity::class.java)
        .putExtra(DebugBackendLaunch.EXTRA_BACKEND_URL, server.url("/").toString())
        .putExtra(DebugBackendLaunch.EXTRA_API_TOKEN, token)
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
    private fun assertNoVisibleText(secret: String) {
        val shown = descendants(activity.window.decorView).filterIsInstance<TextView>().map { it.text.toString() }.toList()
        assertTrue(shown.isNotEmpty())
        assertTrue(shown.none { it.contains(secret) })
    }
    private fun button(label: String): Button = descendants(activity.window.decorView)
        .filterIsInstance<Button>().first { it.text.toString() == label }
    private fun descendants(view: View): Sequence<View> = sequence {
        yield(view)
        if (view is ViewGroup) for (i in 0 until view.childCount) yieldAll(descendants(view.getChildAt(i)))
    }
}
