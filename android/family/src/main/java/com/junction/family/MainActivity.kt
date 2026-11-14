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
import android.view.ViewGroup
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
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
 */
class MainActivity : ComponentActivity() {
    private lateinit var container: FrameLayout
    private lateinit var webView: WebView
    private lateinit var store: AddressStore
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var pageShown = false

    private val proxy by lazy {
        ApiProxy(
            OkHttpClient.Builder()
                .connectTimeout(8, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS)
                .followRedirects(false).followSslRedirects(false).build(),
        ) { store.address }
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
        webView = createWebView()
        container = FrameLayout(this).apply {
            setBackgroundColor(NIGHT)
            addView(webView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        }
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
            // Back closes an open dialog in the page first, then leaves the app.
            webView.evaluateJavascript(CLOSE_DIALOG) { closed -> if (closed != "true") finish() }
        }

        if (savedInstanceState == null || webView.restoreState(savedInstanceState) == null) webView.loadUrl(START_URL)
        if (store.address == null || intent?.action == ACTION_CHANGE_ADDRESS) askForAddress()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == ACTION_CHANGE_ADDRESS) askForAddress()
    }

    override fun onResume() {
        super.onResume()
        webView.onResume(); webView.resumeTimers()
        main.post(followPage)
    }

    override fun onPause() {
        // Stop the clock, the scene's animations and timers while nobody is looking.
        main.removeCallbacks(followPage)
        webView.onPause(); webView.pauseTimers()
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        container.removeView(webView)
        webView.destroy()
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

    private fun syncBarsWithPage() {
        webView.evaluateJavascript(PAGE_BACKGROUND) { value ->
            val color = parseCssColor(value.trim('"')) ?: return@evaluateJavascript
            container.setBackgroundColor(color)
            val light = (Color.red(color) * 299 + Color.green(color) * 587 + Color.blue(color) * 114) / 1000 > 150
            WindowCompat.getInsetsController(window, container).apply {
                isAppearanceLightStatusBars = light
                isAppearanceLightNavigationBars = light
            }
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
        private const val START_URL = "https://${WebViewAssetLoader.DEFAULT_DOMAIN}/dashboard/index.html?nosw"
        private const val NIGHT = 0xFF0F1830.toInt()
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
