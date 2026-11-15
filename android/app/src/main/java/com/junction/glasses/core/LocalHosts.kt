package com.junction.glasses.core

/**
 * The hosts a debug build may reach over plain HTTP: loopback (adb reverse, the emulator's host)
 * and private LAN addresses (10/8, 172.16/12, 192.168/16), so the phone can reach the laptop on the
 * same Wi-Fi when the USB cable is out. Release builds never pass allowLocalHttp and stay HTTPS-only.
 */
object LocalHosts {
    private val LOOPBACK = setOf("localhost", "127.0.0.1", "::1", "10.0.2.2")
    private val IPV4 = Regex("""(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})""")

    fun isLoopback(host: String): Boolean = host in LOOPBACK

    fun isPrivateLan(host: String): Boolean {
        val parts = IPV4.matchEntire(host)?.groupValues?.drop(1)?.map { it.toInt() } ?: return false
        if (parts.any { it > 255 }) return false
        val (a, b) = parts
        return a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168)
    }

    /** Plain HTTP is allowed only with [allowLocalHttp] (debug builds) and only to these hosts. */
    fun cleartextAllowed(host: String, allowLocalHttp: Boolean): Boolean =
        allowLocalHttp && (isLoopback(host) || isPrivateLan(host))
}
