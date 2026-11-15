package com.junction.family

import android.content.SharedPreferences
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** The home backend's address. It is not a secret (the token is), so it is kept in preferences. */
object BackendAddress {
    private val LOOPBACK = setOf("127.0.0.1", "localhost", "10.0.2.2")

    /**
     * Accepts `https://host[:port]` with no path, query or credentials. Plain `http` is allowed only
     * for loopback addresses in debug builds (a phone on USB with `adb reverse`, or the emulator).
     */
    fun parse(input: String, allowLoopbackHttp: Boolean): HttpUrl? {
        val text = input.trim().trimEnd('/')
        if (text.isEmpty()) return null
        val url = (if ("://" in text) text else "https://$text").toHttpUrlOrNull() ?: return null
        if (url.username.isNotEmpty() || url.password.isNotEmpty()) return null
        if (url.querySize > 0 || url.fragment != null || url.encodedPath != "/") return null
        if (url.scheme == "http" && !(allowLoopbackHttp && url.host in LOOPBACK)) return null
        return url
    }
}

class AddressStore(private val prefs: SharedPreferences, private val allowLoopbackHttp: Boolean) {
    @Volatile var address: HttpUrl? = prefs.getString(KEY, null)?.let { BackendAddress.parse(it, allowLoopbackHttp) }
        private set

    fun save(url: HttpUrl) {
        address = url
        prefs.edit().putString(KEY, url.toString()).apply()
    }

    private companion object { const val KEY = "backend_address" }
}
