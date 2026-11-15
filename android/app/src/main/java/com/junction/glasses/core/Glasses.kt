package com.junction.glasses.core

import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder
import java.util.Base64

/**
 * A pair of glasses as the apps list them: a name, and the vision relay that sees through them
 * ([relayUrl] and its [key]; null until the pair is connected with its code). [available] is false for
 * pairs that are listed but not set up yet.
 */
data class GlassesPair(
    val id: String,
    val name: String,
    val owner: String,
    val relayUrl: String? = null,
    val key: String? = null,
    val available: Boolean = true,
    val note: String = "",
) {
    val connected get() = relayUrl != null && key != null
}

/**
 * Pairing codes, as `ml/recon_change/live/share.py` prints them: `DL1-` then base64url (no padding) of
 * `u=<relay address>&k=<key>&n=<name>`. The key in a shared code is the relay's watch-only key; the
 * wearer's own phone gets the owner code (the full key). Same file in the family app (com.junction.family).
 */
object Pairing {
    const val PREFIX = "DL1-"

    fun encode(url: String, key: String, name: String): String {
        fun e(v: String) = URLEncoder.encode(v, "UTF-8").replace("+", "%20")
        val raw = "u=${e(url.trimEnd('/'))}&k=${e(key)}&n=${e(name)}"
        return PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(raw.toByteArray())
    }

    /** The pair a code stands for, or null when it is not a code, or its address is not https
     *  (plain http only for this device or the emulator's host, and only when [allowLocalHttp]). */
    fun parse(code: String, allowLocalHttp: Boolean = false): GlassesPair? {
        val body = code.trim().let { if (it.startsWith("daylight://")) it.substringAfter("c=", "") else it }
        if (!body.startsWith(PREFIX)) return null
        val raw = runCatching { String(Base64.getUrlDecoder().decode(body.removePrefix(PREFIX))) }.getOrNull() ?: return null
        val fields = raw.split('&').mapNotNull { part ->
            val (k, v) = part.split('=', limit = 2).takeIf { it.size == 2 } ?: return@mapNotNull null
            k to runCatching { URLDecoder.decode(v, "UTF-8") }.getOrNull()
        }.toMap()
        val url = fields["u"]?.trimEnd('/') ?: return null
        val key = fields["k"]?.takeIf { it.isNotBlank() && '\n' !in it && '\r' !in it } ?: return null
        val uri = runCatching { URI(url) }.getOrNull() ?: return null
        val host = uri.host ?: return null
        val local = host == "127.0.0.1" || host == "localhost" || host == "10.0.2.2"
        if (uri.scheme != "https" && !(uri.scheme == "http" && local && allowLocalHttp)) return null
        val name = fields["n"]?.takeIf { it.isNotBlank() } ?: "Glasses at $host"
        return GlassesPair(id = "pair-$host", name = name, owner = "", relayUrl = url, key = key)
    }

    /** The pairs everyone sees. Only Seb's is live (during Junction 2026); its code comes from share.py. */
    val DIRECTORY = listOf(
        GlassesPair("seb", "Seb's Ray-Ban Meta", "Seb · Junction 2026 team", note = "Live at Junction 2026. Enter the code Seb's relay printed."),
        GlassesPair("anna", "Anna's Ray-Ban Meta", "Example household", available = false, note = "Not set up yet"),
        GlassesPair("clinic", "Memory clinic loan pair", "Pilot partner", available = false, note = "Not set up yet"),
        GlassesPair("display", "Meta Ray-Ban Display", "Next pair", available = false, note = "Coming soon"),
    )
}

/** Somewhere to keep strings (SharedPreferences in the app, a map in tests). */
interface KeyValues {
    fun get(key: String): String?
    fun put(key: String, value: String?)
}

/** The glasses this phone knows: the directory, with codes filled in, then pairs added by code. */
class GlassesBook(private val store: KeyValues, private val allowLocalHttp: Boolean = false) {
    private fun saved(): Map<String, String> = store.get("codes").orEmpty().lines().filter { '=' in it }
        .associate { it.substringBefore('=') to it.substringAfter('=') }
    private fun save(codes: Map<String, String>) = store.put("codes", codes.entries.joinToString("\n") { "${it.key}=${it.value}" })

    fun pairs(): List<GlassesPair> {
        val codes = saved()
        val listed = Pairing.DIRECTORY.map { d ->
            val p = codes[d.id]?.let { Pairing.parse(it, allowLocalHttp) }
            if (p != null && d.available) d.copy(relayUrl = p.relayUrl, key = p.key) else d
        }
        val added = codes.filterKeys { id -> Pairing.DIRECTORY.none { it.id == id } }.mapNotNull { (id, code) ->
            Pairing.parse(code, allowLocalHttp)?.copy(id = id, owner = "Added with a code")
        }
        return listed + added
    }

    /** Connects a listed pair (or, with [id] null, any pair) with its code. Null when the code is not valid. */
    fun connect(code: String, id: String? = null): GlassesPair? {
        val pair = Pairing.parse(code, allowLocalHttp) ?: return null
        // A code named like a listed pair (Seb's) fills that pair in; any other code adds a new pair.
        val target = id ?: Pairing.DIRECTORY.firstOrNull { it.available && it.name.equals(pair.name, ignoreCase = true) }?.id ?: pair.id
        if (Pairing.DIRECTORY.any { it.id == target && !it.available }) return null
        save(saved() + (target to code.trim().let { if (it.startsWith("daylight://")) it.substringAfter("c=") else it }))
        selected = target
        return pairs().first { it.id == target }
    }

    fun forget(id: String) {
        save(saved() - id)
        if (selected == id) selected = null
    }

    var selected: String?
        get() = store.get("selected")
        set(value) = store.put("selected", value)

    fun current(): GlassesPair? = pairs().firstOrNull { it.id == selected && it.connected }
}
