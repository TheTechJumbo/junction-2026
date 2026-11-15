package com.junction.family

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

/**
 * Glasses: the pairs this phone can watch. Only Seb's pair is live (enter the code his relay printed,
 * or open its pairing link); any other Meta glasses are added with their own code. Watching a pair shows
 * its glasses display live (the relay's /hud page, with the watch-only key): the arrow, the small map,
 * the answers and the reminders the wearer sees. Never the camera.
 */
class GlassesActivity : ComponentActivity() {
    private lateinit var book: GlassesBook
    private lateinit var root: FrameLayout
    private var live: WebView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        book = GlassesBook(prefsStore(getSharedPreferences("glasses", MODE_PRIVATE)), allowLocalHttp = BuildConfig.DEBUG)
        root = FrameLayout(this).apply { setBackgroundColor(NIGHT) }
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom); WindowInsetsCompat.CONSUMED
        }
        setContentView(root)
        onBackPressedDispatcher.addCallback(this) { if (live != null) showList() else finish() }
        pairFromLink(intent)
        if (live == null) showList()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        pairFromLink(intent)
    }

    override fun onDestroy() { live?.destroy(); super.onDestroy() }

    /** daylight://pair?c=DL1-… : connect that pair and watch it. */
    private fun pairFromLink(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme != "daylight" || data.host != "pair") return
        val code = data.getQueryParameter("c") ?: return
        val pair = book.connect(code)
        if (pair != null) watch(pair) else { showList(); say("That link is not a pairing code.") }
    }

    private fun showList() {
        live?.destroy(); live = null
        val column = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(16), dp(20), dp(24)) }
        column.addView(text("Glasses", 30f, SNOW, bold = true))
        column.addView(text("Watch what a pair of glasses shows, live: the way to the pill box, the answers, the reminders. Never the camera.", 15f, SOFT).apply { setPadding(0, dp(6), 0, dp(14)) })
        for (pair in book.pairs()) column.addView(row(pair), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(10) })
        column.addView(text("+  Add Meta glasses", 17f, NIGHT, bold = true).apply {
            gravity = Gravity.CENTER; setPadding(dp(16), dp(16), dp(16), dp(16))
            background = rounded(AMBER); isClickable = true; isFocusable = true
            contentDescription = "Add Meta glasses with a pairing code"
            setOnClickListener { askCode(null) }
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) })
        column.addView(text("Any Ray-Ban Meta glasses can be added: run the vision relay with them, then live/share.py prints the pair's code and link.", 13f, SOFT).apply { setPadding(0, dp(12), 0, 0) })
        root.removeAllViews()
        root.addView(ScrollView(this).apply { addView(column) })
    }

    private fun row(pair: GlassesPair): View {
        val state = when {
            !pair.available -> pair.note
            pair.connected -> "Live · tap to watch"
            else -> "Live · enter the code to watch"
        }
        return LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14)); background = rounded(PANEL)
            alpha = if (pair.available) 1f else 0.5f
            addView(View(context).apply { background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(if (pair.available) GREEN else SOFT) } },
                LinearLayout.LayoutParams(dp(12), dp(12)).apply { marginEnd = dp(14) })
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                addView(text(pair.name, 18f, SNOW, bold = true))
                addView(text(listOf(pair.owner, state).filter { it.isNotBlank() }.joinToString(" · "), 13f, SOFT))
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            isEnabled = pair.available; isClickable = pair.available; isFocusable = pair.available
            contentDescription = "${pair.name}. $state"
            setOnClickListener { if (pair.connected) watch(pair) else askCode(pair) }
            setOnLongClickListener {
                if (!pair.connected) return@setOnLongClickListener false
                AlertDialog.Builder(this@GlassesActivity).setTitle("Forget ${pair.name}?")
                    .setPositiveButton("Forget") { _, _ -> book.forget(pair.id); showList() }.setNegativeButton("Keep", null).show()
                true
            }
        }
    }

    private fun askCode(pair: GlassesPair?) {
        val field = EditText(this).apply { hint = "DL1-…"; isSingleLine = true }
        val frame = FrameLayout(this).apply { setPadding(dp(20), dp(8), dp(20), 0); addView(field) }
        AlertDialog.Builder(this)
            .setTitle(pair?.let { "Code for ${it.name}" } ?: "Add Meta glasses")
            .setMessage(pair?.note?.ifBlank { null } ?: "Paste the pairing code the glasses' relay printed.")
            .setView(frame)
            .setPositiveButton("Watch") { _, _ ->
                val connected = book.connect(field.text.toString(), pair?.id)
                if (connected != null) watch(connected) else say("That is not a pairing code for these glasses.")
            }
            .setNegativeButton("Cancel", null).show()
    }

    /** The pair's glasses display, live from its relay (watch-only key). */
    @SuppressLint("SetJavaScriptEnabled")
    private fun watch(pair: GlassesPair) {
        val url = pair.relayUrl ?: return; val key = pair.key ?: return
        book.selected = pair.id
        live?.destroy()
        val web = WebView(this).apply {
            setBackgroundColor(Color.BLACK)
            settings.javaScriptEnabled = true // the glasses display is a small JavaScript page
            settings.domStorageEnabled = false; settings.allowFileAccess = false; settings.allowContentAccess = false
            webViewClient = object : WebViewClient() {
                override fun onReceivedHttpError(view: WebView, request: android.webkit.WebResourceRequest, response: android.webkit.WebResourceResponse) {
                    if (request.isForMainFrame) say("${pair.name} is not sharing right now (${response.statusCode}).")
                }
                override fun onReceivedError(view: WebView, request: android.webkit.WebResourceRequest, error: android.webkit.WebResourceError) {
                    if (request.isForMainFrame) say("${pair.name} is offline: its relay or tunnel is not running.")
                }
            }
            loadUrl("$url/hud?k=${android.net.Uri.encode(key)}")
        }
        live = web
        val column = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(LinearLayout(this).apply {
            gravity = Gravity.CENTER_VERTICAL; setPadding(dp(12), dp(10), dp(16), dp(10)); setBackgroundColor(NIGHT)
            addView(text("‹ Glasses", 17f, AMBER, bold = true).apply {
                setPadding(dp(8), dp(8), dp(16), dp(8)); isClickable = true; contentDescription = "Back to the glasses list"
                setOnClickListener { showList() }
            })
            addView(text("● LIVE  ${pair.name}", 16f, SNOW, bold = true).apply { gravity = Gravity.END },
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        })
        column.addView(web, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        root.removeAllViews(); root.addView(column)
    }

    private fun say(message: String) = android.widget.Toast.makeText(this, message, android.widget.Toast.LENGTH_LONG).show()
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun rounded(color: Int) = GradientDrawable().apply { setColor(color); cornerRadius = dp(16).toFloat() }
    private fun text(value: String, sp: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
        text = value; setTextColor(color); setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        if (bold) typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
    }

    companion object {
        // Kaamos: blue-hour indigo, snow, the lamp's amber.
        private const val NIGHT = 0xFF0F1830.toInt()
        private const val PANEL = 0xFF1D2A4A.toInt()
        private const val SNOW = 0xFFEEF2F6.toInt()
        private const val SOFT = 0xFFA0B0CC.toInt()
        private const val AMBER = 0xFFF2B04A.toInt()
        private const val GREEN = 0xFF7FD6A0.toInt()

        fun prefsStore(prefs: SharedPreferences) = object : KeyValues {
            override fun get(key: String) = prefs.getString(key, null)
            override fun put(key: String, value: String?) { prefs.edit().apply { if (value == null) remove(key) else putString(key, value) }.apply() }
        }
    }
}
