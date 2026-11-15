package com.junction.family

import android.content.SharedPreferences
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * The laptop relay's address and access key, for the Live tab.
 *
 * The relay runs on a laptop in the home, so in debug builds plain `http` is allowed to loopback
 * (a phone on USB with `adb reverse tcp:8090 tcp:8090`, or the emulator) and to private LAN
 * addresses (10/8, 172.16/12, 192.168/16: the laptop on the same Wi-Fi). Release builds take
 * `https` only, as for the home service.
 */
object RelayAddress {
    private val LOOPBACK = setOf("127.0.0.1", "localhost", "10.0.2.2")
    private val IPV4 = Regex("""(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})""")
    private val KEY = Regex("[A-Za-z0-9._~-]{8,128}")
    private val ZONE = Regex("[a-z0-9_-]{1,32}")

    fun isPrivateLan(host: String): Boolean {
        val parts = IPV4.matchEntire(host)?.groupValues?.drop(1)?.map { it.toInt() } ?: return false
        if (parts.any { it > 255 }) return false
        val (a, b) = parts
        return a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168)
    }

    /** `https://host[:port]` (or local `http` in debug builds) with no path, query or credentials. */
    fun parse(input: String, allowLocalHttp: Boolean): HttpUrl? {
        val text = input.trim().trimEnd('/')
        if (text.isEmpty()) return null
        val guess = if ("://" in text) text else {  // no scheme: local http in debug builds, else https
            val host = "https://$text".toHttpUrlOrNull()?.host
            if (allowLocalHttp && host != null && (host in LOOPBACK || isPrivateLan(host))) "http://$text" else "https://$text"
        }
        val url = guess.toHttpUrlOrNull() ?: return null
        if (url.username.isNotEmpty() || url.password.isNotEmpty()) return null
        if (url.querySize > 0 || url.fragment != null || url.encodedPath != "/") return null
        if (url.scheme == "http" && !(allowLocalHttp && (url.host in LOOPBACK || isPrivateLan(url.host)))) return null
        return url
    }

    /** The relay's access key as the relay prints it: letters, digits and `-_.~`, nothing else. */
    fun key(input: String): String? = input.trim().takeIf { KEY.matches(it) }

    /** The key typed in the settings, or the saved one when the field was left empty; null when neither will do. */
    fun keyOrSaved(input: String, saved: String?): String? = if (input.isBlank()) saved?.let { key(it) } else key(input)

    /** A room name as the relay uses it (`kitchen`, `living-room`); blank means `kitchen`. */
    fun zone(input: String): String? = input.trim().lowercase().ifEmpty { "kitchen" }.takeIf { ZONE.matches(it) }
}

/**
 * Keeps the relay settings in the app's private preferences. The key has to be kept for the proxy to
 * add it to every request; it is the relay's own access key, not the home service's sign-in.
 */
class RelayStore(private val prefs: SharedPreferences, private val allowLocalHttp: Boolean) {
    @Volatile var settings: RelaySettings? = read()
        private set

    private fun read(): RelaySettings? {
        val address = prefs.getString(ADDRESS, null)?.let { RelayAddress.parse(it, allowLocalHttp) } ?: return null
        val key = prefs.getString(KEY, null)?.let { RelayAddress.key(it) } ?: return null
        val zone = RelayAddress.zone(prefs.getString(ZONE, null).orEmpty()) ?: "kitchen"
        return RelaySettings(address, key, zone)
    }

    fun save(next: RelaySettings) {
        settings = next
        prefs.edit().putString(ADDRESS, next.address.toString()).putString(KEY, next.key).putString(ZONE, next.zone).apply()
    }

    private companion object {
        const val ADDRESS = "relay_address"
        const val KEY = "relay_key"
        const val ZONE = "relay_zone"
    }
}
