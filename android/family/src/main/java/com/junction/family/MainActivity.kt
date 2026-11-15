package com.junction.family

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.text.method.HideReturnsTransformationMethod
import android.text.method.PasswordTransformationMethod
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.CheckBox
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.activity.enableEdgeToEdge
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewAssetLoader
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Daylight for the family: the dashboard from `/dashboard`, packaged in the app and shown in a
 * WebView, with its data requests made natively (see [ApiProxy]).
 *
 * A second tab, Live 3D, shows the laptop relay's caretaker page (`/care`): the room as a Gaussian
 * splat with the wearer moving in it, the named places, and what they are doing. Its requests go to
 * the relay through [RelayProxy], which adds the relay's key.
 */
class MainActivity : ComponentActivity() {
    private lateinit var container: FrameLayout
    private lateinit var webView: WebView
    private lateinit var store: AddressStore
    private lateinit var relayStore: RelayStore
    private lateinit var content: FrameLayout
    private lateinit var todayTab: TextView
    private lateinit var liveTab: TextView
    private var liveView: WebView? = null
    private var showingLive = false
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var pageShown = false

    private val proxy by lazy {
        ApiProxy(
            OkHttpClient.Builder()
                .connectTimeout(8, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS)
                .followRedirects(false).followSslRedirects(false).build(),
        ) { store.address }
    }
    private val relayProxy by lazy {
        RelayProxy(
            OkHttpClient.Builder()  // the room's splat is tens of megabytes: allow it time to arrive
                .connectTimeout(8, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS)
                .followRedirects(false).followSslRedirects(false).build(),
        ) { relayStore.settings }
    }
    private val assets by lazy {
        WebViewAssetLoader.Builder()
            // Mounted at "/" so /dashboard/x maps to assets/dashboard/x; only /dashboard/ paths are
            // ever passed to it (see shouldInterceptRequest), everything else goes to ApiProxy.
            .addPathHandler("/", WebViewAssetLoader.AssetsPathHandler(this)).build()
    }
    // The page can change colour with the real sky (and with the chosen look), so the system bars
    // follow it: read the page background now and then, only while the app is in front.
    private val followPage = object : Runnable {
        override fun run() { syncBarsWithPage(); main.postDelayed(this, 20_000) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        val splash = installSplashScreen()
        super.onCreate(savedInstanceState)
        splash.setKeepOnScreenCondition { !pageShown }
        main.postDelayed({ pageShown = true }, 1_500) // never hold the splash longer than this
        enableEdgeToEdge()

        store = AddressStore(getSharedPreferences("daylight", MODE_PRIVATE), BuildConfig.DEBUG)
        relayStore = RelayStore(getSharedPreferences("daylight", MODE_PRIVATE), BuildConfig.DEBUG)
        webView = createWebView()
        content = FrameLayout(this).apply {
            addView(webView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        }
        todayTab = tabButton(getString(R.string.tab_today)) { showToday() }
        liveTab = tabButton(getString(R.string.tab_live)) { showLive() }
        liveTab.setOnLongClickListener { askForRelay(); true }
        val tabs = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(NIGHT)
            addView(todayTab, LinearLayout.LayoutParams(0, dp(48), 1f))
            addView(liveTab, LinearLayout.LayoutParams(0, dp(48), 1f))
        }
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(content, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
            addView(tabs, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        container = FrameLayout(this).apply {
            setBackgroundColor(NIGHT)
            addView(column, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        }
        markTabs()
        // Keep the page clear of the status bar, navigation bar, cutouts and the keyboard; the
        // container's colour (matched to the page) shows behind the bars.
        ViewCompat.setOnApplyWindowInsetsListener(container) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            WindowInsetsCompat.CONSUMED
        }
        setContentView(container)

        onBackPressedDispatcher.addCallback(this) {
            // Back leaves the Live tab first, then closes an open dialog in the page, then leaves the app.
            if (showingLive) showToday()
            else webView.evaluateJavascript(CLOSE_DIALOG) { closed -> if (closed != "true") finish() }
        }

        if (savedInstanceState == null || webView.restoreState(savedInstanceState) == null) webView.loadUrl(START_URL)
        if (savedInstanceState?.getBoolean(STATE_LIVE) == true && relayStore.settings != null) showLive()
        if (intent?.action == ACTION_CHANGE_RELAY) askForRelay()
        else if (store.address == null || intent?.action == ACTION_CHANGE_ADDRESS) askForAddress()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == ACTION_CHANGE_ADDRESS) askForAddress()
        if (intent.action == ACTION_CHANGE_RELAY) askForRelay()
    }

    override fun onResume() {
        super.onResume()
        webView.onResume(); webView.resumeTimers()
        if (showingLive) liveView?.onResume()
        main.post(followPage)
    }

    override fun onPause() {
        // Stop the clock, the scene's animations and timers while nobody is looking.
        main.removeCallbacks(followPage)
        webView.onPause(); liveView?.onPause(); webView.pauseTimers()
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
        outState.putBoolean(STATE_LIVE, showingLive)
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        content.removeAllViews()
        webView.destroy()
        liveView?.destroy()
        super.onDestroy()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun createWebView() = WebView(this).apply {
        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true) // chrome://inspect, debug builds only
        setBackgroundColor(NIGHT)
        settings.apply {
            javaScriptEnabled = true // the dashboard is a JavaScript app
            domStorageEnabled = true // remembers the chosen look
            allowFileAccess = false
            allowContentAccess = false
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            setSupportZoom(false)
            builtInZoomControls = false
            mediaPlaybackRequiresUserGesture = true
        }
        webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                val url = request.url
                if (url.scheme != "https" || url.host != WebViewAssetLoader.DEFAULT_DOMAIN) return null
                if (url.path.orEmpty().startsWith("/dashboard/")) return assets.shouldInterceptRequest(url)
                val result = proxy.handle(request.method, url.encodedPath.orEmpty(), url.encodedQuery, request.requestHeaders)
                return WebResourceResponse(result.mimeType, result.encoding, result.status, result.reason, result.headers, result.body)
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                if (url.host == WebViewAssetLoader.DEFAULT_DOMAIN) return false
                if (url.scheme == "https" || url.scheme == "http") {
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, url)) }
                }
                return true
            }

            override fun onPageCommitVisible(view: WebView, url: String) {
                pageShown = true
                syncBarsWithPage()
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                // The page's renderer was killed (memory pressure or a crash): start again cleanly.
                recreate()
                return true
            }
        }
    }

    // ---- Live 3D: the relay's caretaker page ----
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private fun tabButton(label: String, onClick: () -> Unit) = TextView(this).apply {
        text = label
        gravity = Gravity.CENTER
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        isClickable = true
        isFocusable = true
        setOnClickListener { onClick() }
    }

    private fun markTabs() {
        for ((tab, on) in listOf(todayTab to !showingLive, liveTab to showingLive)) {
            tab.setTextColor(if (on) Color.WHITE else 0xB3FFFFFF.toInt())
            tab.paint.isFakeBoldText = on
            tab.setBackgroundColor(if (on) 0xFF1B2A4A.toInt() else NIGHT)
            tab.isSelected = on
            tab.invalidate()
        }
    }

    private fun showToday() {
        if (!showingLive) return
        showingLive = false
        liveView?.apply { onPause(); visibility = View.GONE }
        webView.visibility = View.VISIBLE
        markTabs()
        syncBarsWithPage()
    }

    private fun showLive() {
        val settings = relayStore.settings ?: return askForRelay()
        val view = liveView ?: createLiveView().also {
            liveView = it
            content.addView(it, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            it.loadUrl(RelayProxy.startUrl(ORIGIN, settings.zone))
        }
        showingLive = true
        view.visibility = View.VISIBLE
        view.onResume()
        webView.visibility = View.INVISIBLE // kept alive, so the dashboard is as it was on the way back
        markTabs()
        syncBarsWithPage()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun createLiveView() = WebView(this).apply {
        setBackgroundColor(LIVE_BACKGROUND)
        settings.apply {
            javaScriptEnabled = true // the 3D room is a JavaScript page (three.js and Spark)
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            setSupportZoom(false)
            builtInZoomControls = false
            mediaPlaybackRequiresUserGesture = true
        }
        webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                val url = request.url
                // Only the app's own origin is answered here: /relay/... goes to the laptop with the key.
                // The page's libraries (three.js, Spark) load from their CDN as in a browser.
                if (url.scheme != "https" || url.host != WebViewAssetLoader.DEFAULT_DOMAIN) return null
                val result = relayProxy.handle(request.method, url.encodedPath.orEmpty(), url.encodedQuery)
                return WebResourceResponse(result.mimeType, result.encoding, result.status, result.reason, result.headers, result.body)
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                if (url.host == WebViewAssetLoader.DEFAULT_DOMAIN) return false
                if (url.scheme == "https" || url.scheme == "http") runCatching { startActivity(Intent(Intent.ACTION_VIEW, url)) }
                return true
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                recreate() // the 3D page is heavy: on a renderer crash, start again cleanly
                return true
            }
        }
    }

    private fun askForRelay() {
        val now = relayStore.settings
        fun field(hintText: String, value: String, type: Int) = EditText(this).apply {
            setSingleLine() // first: it replaces the text's transformation, which would undo a password field's dots
            inputType = type
            hint = hintText
            setText(value)
        }
        val address = field(getString(R.string.relay_address_hint), now?.address?.toString()?.trimEnd('/')
            ?: if (BuildConfig.DEBUG) "http://127.0.0.1:8090" else "", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        // The key is never shown: the field starts empty (left empty, the saved key is kept), shows dots while typing,
        // and Show key reveals only what is typed in it now.
        val key = field(getString(if (now != null) R.string.relay_key_hint_saved else R.string.relay_key_hint), "",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD).apply {
            transformationMethod = PasswordTransformationMethod.getInstance()
            importantForAutofill = android.view.View.IMPORTANT_FOR_AUTOFILL_NO
        }
        val showKey = CheckBox(this).apply {
            text = getString(R.string.relay_key_show)
            setOnCheckedChangeListener { _, on ->
                key.transformationMethod = if (on) HideReturnsTransformationMethod.getInstance() else PasswordTransformationMethod.getInstance()
                key.setSelection(key.text.length)
            }
        }
        val room = field(getString(R.string.relay_zone_hint), now?.zone ?: "kitchen", InputType.TYPE_CLASS_TEXT)
        val padding = dp(20)
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding / 2, padding, 0)
            addView(address); addView(key); addView(showKey); addView(room)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.relay_title)
            .setMessage(R.string.relay_message)
            .setView(form)
            .setPositiveButton(R.string.address_save) { _, _ ->
                val url = RelayAddress.parse(address.text.toString(), BuildConfig.DEBUG)
                val secret = RelayAddress.keyOrSaved(key.text.toString(), now?.key)
                val zone = RelayAddress.zone(room.text.toString())
                if (url == null || secret == null || zone == null) {
                    Toast.makeText(this, R.string.relay_invalid, Toast.LENGTH_LONG).show()
                    askForRelay()
                    return@setPositiveButton
                }
                relayStore.save(RelaySettings(url, secret, zone))
                liveView?.loadUrl(RelayProxy.startUrl(ORIGIN, zone))
                showLive()
            }
            .setNegativeButton(R.string.address_later, null)
            .show()
    }

    private fun syncBarsWithPage() {
        if (showingLive) return applyBarColor(LIVE_BACKGROUND)
        webView.evaluateJavascript(PAGE_BACKGROUND) { value ->
            val color = parseCssColor(value.trim('"')) ?: return@evaluateJavascript
            if (!showingLive) applyBarColor(color)
        }
    }

    private fun applyBarColor(color: Int) {
        container.setBackgroundColor(color)
        val light = (Color.red(color) * 299 + Color.green(color) * 587 + Color.blue(color) * 114) / 1000 > 150
        WindowCompat.getInsetsController(window, container).apply {
            isAppearanceLightStatusBars = light
            isAppearanceLightNavigationBars = light
        }
    }

    private fun askForAddress() {
        val field = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            hint = getString(R.string.address_hint)
            setText(store.address?.toString() ?: if (BuildConfig.DEBUG) "http://127.0.0.1:8000" else "")
            setSingleLine()
        }
        val padding = (20 * resources.displayMetrics.density).toInt()
        val frame = FrameLayout(this).apply { setPadding(padding, padding / 2, padding, 0); addView(field) }
        AlertDialog.Builder(this)
            .setTitle(R.string.address_title)
            .setMessage(R.string.address_message)
            .setView(frame)
            .setPositiveButton(R.string.address_save) { _, _ ->
                val url = BackendAddress.parse(field.text.toString(), BuildConfig.DEBUG)
                if (url == null) {
                    Toast.makeText(this, R.string.address_invalid, Toast.LENGTH_LONG).show()
                    askForAddress()
                } else store.save(url)
            }
            .setNegativeButton(R.string.address_later, null)
            .show()
    }

    companion object {
        const val ACTION_CHANGE_ADDRESS = "com.junction.family.CHANGE_ADDRESS"
        const val ACTION_CHANGE_RELAY = "com.junction.family.CHANGE_RELAY"
        private const val ORIGIN = "https://${WebViewAssetLoader.DEFAULT_DOMAIN}"
        private const val START_URL = "$ORIGIN/dashboard/index.html?nosw"
        private const val STATE_LIVE = "live_tab"
        private const val NIGHT = 0xFF0F1830.toInt()
        private const val LIVE_BACKGROUND = 0xFF05070A.toInt()
        private const val CLOSE_DIALOG =
            "(() => { const open = [...document.querySelectorAll('dialog[open]')].pop(); if (open) { open.close(); return true; } return false; })()"
        private const val PAGE_BACKGROUND =
            "(() => { const b = getComputedStyle(document.body).backgroundColor; return b && b !== 'rgba(0, 0, 0, 0)' ? b : getComputedStyle(document.documentElement).backgroundColor; })()"

        /** Parses the `rgb(r, g, b)` / `rgba(r, g, b, a)` strings that getComputedStyle returns. */
        fun parseCssColor(css: String): Int? {
            val parts = Regex("rgba?\\(([^)]+)\\)").find(css)?.groupValues?.get(1)?.split(',')?.map { it.trim() } ?: return null
            if (parts.size < 3) return null
            val (r, g, b) = parts.take(3).map { it.toFloatOrNull()?.toInt()?.coerceIn(0, 255) ?: return null }
            val alpha = parts.getOrNull(3)?.toFloatOrNull() ?: 1f
            if (alpha < 0.5f) return null
            return Color.rgb(r, g, b)
        }
    }
}
