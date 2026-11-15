package com.junction.family

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RelayAddressTest {
    @Test fun httpsAnywhere() {
        assertEquals("https://relay.example.org/", RelayAddress.parse("relay.example.org", allowLocalHttp = false).toString())
        assertEquals("https://relay.example.org:8443/", RelayAddress.parse(" https://relay.example.org:8443/ ", false).toString())
    }

    @Test fun localHttpOnlyInDebugBuilds() {
        assertEquals("http://127.0.0.1:8090/", RelayAddress.parse("http://127.0.0.1:8090", allowLocalHttp = true).toString())
        assertEquals("http://10.0.41.113:8090/", RelayAddress.parse("10.0.41.113:8090", allowLocalHttp = true).toString())
        assertEquals("http://192.168.1.20:8090/", RelayAddress.parse("http://192.168.1.20:8090", true).toString())
        assertEquals("http://172.20.0.5:8090/", RelayAddress.parse("http://172.20.0.5:8090", true).toString())
        assertNull(RelayAddress.parse("http://10.0.41.113:8090", allowLocalHttp = false))
        assertNull(RelayAddress.parse("http://127.0.0.1:8090", allowLocalHttp = false))
        assertNull(RelayAddress.parse("http://172.32.0.5:8090", true)) // not a private range
        assertNull(RelayAddress.parse("http://relay.example.org", true)) // a name: https only
        assertEquals("https://relay.example.org/", RelayAddress.parse("relay.example.org", true).toString())
    }

    @Test fun refusesPathsQueriesAndCredentials() {
        assertNull(RelayAddress.parse("https://relay.example.org/care", false))
        assertNull(RelayAddress.parse("https://relay.example.org/?k=secret", false))
        assertNull(RelayAddress.parse("https://user:pw@relay.example.org", false))
        assertNull(RelayAddress.parse("", false))
    }

    @Test fun keysAndRooms() {
        assertEquals("abc-DEF_123.x~", RelayAddress.key("  abc-DEF_123.x~ "))
        assertNull(RelayAddress.key("short"))
        assertNull(RelayAddress.key("has space in it"))
        assertNull(RelayAddress.key("semi;colon-key"))
        assertEquals("kitchen", RelayAddress.zone(""))
        assertEquals("living-room", RelayAddress.zone("Living-Room"))
        assertNull(RelayAddress.zone("../etc"))
    }
}
