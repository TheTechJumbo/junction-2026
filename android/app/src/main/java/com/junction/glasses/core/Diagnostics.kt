package com.junction.glasses.core

import java.security.MessageDigest
import java.util.Locale

/** A short fingerprint of a secret: the first 6 hex of its SHA-256, so a mismatch shows, never the secret. */
object KeyPrint {
    fun of(secret: String?): String {
        if (secret.isNullOrBlank()) return "not set"
        val digest = MessageDigest.getInstance("SHA-256").digest(secret.toByteArray(Charsets.UTF_8))
        return digest.take(3).joinToString("") { String.format(Locale.US, "%02x", it) }
    }
}

/**
 * The last answer from each server call the app makes, for the debug panel: HTTP status (0 when there
 * was no answer at all), when (elapsed ms) and a short note. Never URLs with keys, tokens or bodies.
 */
object HttpTrace {
    data class Entry(val status: Int, val atMs: Long, val note: String?)

    private val entries = LinkedHashMap<String, Entry>()

    @Synchronized
    fun record(label: String, status: Int, atMs: Long, note: String? = null) {
        entries.remove(label)
        entries[label] = Entry(status, atMs, note?.take(160))
    }

    @Synchronized
    fun snapshot(): Map<String, Entry> = LinkedHashMap(entries)

    @Synchronized
    fun clear() = entries.clear()

    /** "200", "403 (key or token mismatch?)", "no answer (cable out or wrong address?)". */
    fun describe(status: Int): String = when (status) {
        0 -> "no answer (cable out, Wi-Fi or wrong address?)"
        401, 403 -> "$status (key or token mismatch?)"
        404 -> "404 (not on this server yet?)"
        502, 503 -> "$status (server or worker not running?)"
        else -> status.toString()
    }

    fun age(nowMs: Long, atMs: Long): String {
        val s = ((nowMs - atMs) / 1000).coerceAtLeast(0)
        return when {
            s < 60 -> "${s}s ago"
            s < 3600 -> "${s / 60}m ${s % 60}s ago"
            else -> "${s / 3600}h ${(s % 3600) / 60}m ago"
        }
    }
}

/** What the debug panel shows, gathered by the Activity; [report] turns it into text with no secrets. */
data class DiagnosticsFacts(
    val build: String,
    val relayUrl: String,
    val relayLanUrl: String?,
    val relayKeyPrint: String,
    val backendUrl: String?,
    val backendTokenPrint: String,
    val zone: String,
    val venueZone: String?,
    val glasses: List<String>,
    val recording: List<String>,
    val trace: Map<String, HttpTrace.Entry>,
    val nowMs: Long,
)

object Diagnostics {
    fun report(f: DiagnosticsFacts): String = buildString {
        appendLine("Glasses app · debug · build ${f.build}")
        appendLine()
        appendLine("Relay: ${f.relayUrl}" + (f.relayLanUrl?.let { " (Wi-Fi fallback $it)" } ?: ""))
        appendLine("Relay key print: ${f.relayKeyPrint}")
        appendLine("Backend: ${f.backendUrl ?: "not set"}")
        appendLine("Backend token print: ${f.backendTokenPrint}")
        appendLine("Recording zone: ${f.zone} · map and guidance zone: ${f.venueZone ?: "${f.zone} (no venue zone set)"}")
        appendLine()
        appendLine("Last answers:")
        if (f.trace.isEmpty()) appendLine("  none yet")
        for ((label, e) in f.trace) {
            appendLine("  $label: ${HttpTrace.describe(e.status)}, ${HttpTrace.age(f.nowMs, e.atMs)}" + (e.note?.let { " · $it" } ?: ""))
        }
        appendLine()
        appendLine("Glasses:")
        f.glasses.forEach { appendLine("  $it") }
        appendLine()
        appendLine("Recording and uploads:")
        f.recording.forEach { appendLine("  $it") }
    }.trimEnd()
}
