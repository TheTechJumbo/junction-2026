package com.junction.family

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BackendAddressTest {
    @Test fun acceptsHttpsHostsAndAddsTheScheme() {
        assertEquals("https://home.example.org/", BackendAddress.parse("home.example.org", false).toString())
        assertEquals("https://home.example.org:8443/", BackendAddress.parse(" https://home.example.org:8443/ ", false).toString())
    }

    @Test fun refusesPathsQueriesAndCredentials() {
        assertNull(BackendAddress.parse("https://home.example.org/api", false))
        assertNull(BackendAddress.parse("https://home.example.org/?token=x", false))
        assertNull(BackendAddress.parse("https://user:pass@home.example.org", false))
        assertNull(BackendAddress.parse("", false))
    }

    @Test fun plainHttpOnlyForLoopbackInDebug() {
        assertNull(BackendAddress.parse("http://home.example.org", true))
        assertNull(BackendAddress.parse("http://127.0.0.1:8000", false))
        assertEquals("http://127.0.0.1:8000/", BackendAddress.parse("http://127.0.0.1:8000", true).toString())
        assertEquals("http://10.0.2.2:8000/", BackendAddress.parse("http://10.0.2.2:8000", true).toString())
    }
}
