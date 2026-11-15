package com.junction.glasses.core

import org.junit.Assert.*
import org.junit.Test

/** Which plain-HTTP addresses debug builds may use: loopback and the private LAN ranges only. */
class LocalHostsTest {
    @Test fun privateLanRangesAreRecognised() {
        for (host in listOf("10.0.0.5", "10.255.255.255", "172.16.0.1", "172.31.255.1", "192.168.1.20", "192.168.0.1")) {
            assertTrue(host, LocalHosts.isPrivateLan(host))
        }
        for (host in listOf("172.15.0.1", "172.32.0.1", "192.169.1.1", "8.8.8.8", "100.64.0.1", "192.168.1.300", "relay.example.com", "")) {
            assertFalse(host, LocalHosts.isPrivateLan(host))
        }
    }

    @Test fun cleartextOnlyInDebugAndOnlyToLoopbackOrLan() {
        assertTrue(LocalHosts.cleartextAllowed("127.0.0.1", allowLocalHttp = true))
        assertTrue(LocalHosts.cleartextAllowed("10.0.2.2", allowLocalHttp = true))
        assertTrue(LocalHosts.cleartextAllowed("192.168.1.20", allowLocalHttp = true))
        assertFalse(LocalHosts.cleartextAllowed("192.168.1.20", allowLocalHttp = false))
        assertFalse(LocalHosts.cleartextAllowed("127.0.0.1", allowLocalHttp = false))
        assertFalse(LocalHosts.cleartextAllowed("8.8.8.8", allowLocalHttp = true))
        assertFalse(LocalHosts.cleartextAllowed("relay.example.com", allowLocalHttp = true))
    }

    @Test fun backendClientsFollowTheSameRule() {
        HttpBackendClient("http://192.168.1.20:8000", "synthetic-token", allowLocalHttp = true)
        EvidenceClient("http://10.0.0.7:8000", "synthetic-token", allowLocalHttp = true)
        assertThrows(IllegalArgumentException::class.java) { HttpBackendClient("http://192.168.1.20:8000", "synthetic-token", allowLocalHttp = false) }
        assertThrows(IllegalArgumentException::class.java) { HttpBackendClient("http://8.8.8.8:8000", "synthetic-token", allowLocalHttp = true) }
    }
}
