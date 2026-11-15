package com.junction.glasses.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Pairing codes (as live/share.py prints them) and the glasses list. */
class GlassesTest {
    private class Memory : KeyValues {
        val map = mutableMapOf<String, String>()
        override fun get(key: String) = map[key]
        override fun put(key: String, value: String?) { if (value == null) map.remove(key) else map[key] = value }
    }

    // Made by share.pairing_code("https://abc-def.trycloudflare.com/", "v1ew&key=1", "Seb's Ray-Ban Meta").
    private val sebCode = "TU1-dT1odHRwcyUzQSUyRiUyRmFiYy1kZWYudHJ5Y2xvdWRmbGFyZS5jb20maz12MWV3JTI2a2V5JTNEMSZuPVNlYiUyN3MlMjBSYXktQmFuJTIwTWV0YQ"

    @Test fun readsTheCodeTheRelayPrints() {
        val pair = Pairing.parse(sebCode)!!
        assertEquals("https://abc-def.trycloudflare.com", pair.relayUrl)
        assertEquals("v1ew&key=1", pair.key)
        assertEquals("Seb's Ray-Ban Meta", pair.name)
        assertEquals(pair, Pairing.parse("tuttu://pair?c=$sebCode"))
        assertEquals(pair.copy(), Pairing.parse(Pairing.encode("https://abc-def.trycloudflare.com/", "v1ew&key=1", "Seb's Ray-Ban Meta")))
    }

    @Test fun refusesWhatIsNotACodeOrNotHttps() {
        assertNull(Pairing.parse("hello"))
        assertNull(Pairing.parse("TU1-%%%"))
        assertNull(Pairing.parse(Pairing.encode("http://relay.example", "k", "x")))
        assertNull(Pairing.parse(Pairing.encode("http://127.0.0.1:8090", "k", "x")))
        assertNotNull(Pairing.parse(Pairing.encode("http://127.0.0.1:8090", "k", "x"), allowLocalHttp = true))
        assertNull(Pairing.parse(Pairing.encode("https://relay.example", "", "x")))
    }

    @Test fun sebsCodeFillsInSebsPairAndOtherCodesAddPairs() {
        val book = GlassesBook(Memory())
        assertFalse(book.pairs().first { it.id == "seb" }.connected)
        assertTrue(book.pairs().filter { it.id != "seb" }.none { it.available }) // only Seb's pair is live
        val seb = book.connect(sebCode)!!
        assertEquals("seb", seb.id); assertTrue(seb.connected)
        assertEquals(seb, book.current())
        val other = book.connect(Pairing.encode("https://other.example", "key2", "Mum's glasses"))!!
        assertEquals("Mum's glasses", other.name)
        assertEquals(Pairing.DIRECTORY.size + 1, book.pairs().size)
        assertNull(book.connect(sebCode, id = "anna")) // a pair that is not set up cannot be connected
        book.forget(other.id)
        assertNull(book.current())
        assertEquals(Pairing.DIRECTORY.size, book.pairs().size)
    }
}
