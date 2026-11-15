package com.junction.glasses.core

import org.junit.Assert.*
import org.junit.Test

/** The wearer screen's automatic sampling: which camera, and the one line it shows. */
class SamplingTest {
    @Test fun glassesWhileTheirSessionIsUpElseThePhoneWhenAllowed() {
        assertEquals(Sampling.Camera.GLASSES, Sampling.camera("STARTED", phoneAllowed = true))
        assertEquals(Sampling.Camera.GLASSES, Sampling.camera("STARTED", phoneAllowed = false))
        assertEquals(Sampling.Camera.PHONE, Sampling.camera("DISCONNECTED", phoneAllowed = true))
        assertEquals("an idle drop falls back to the phone", Sampling.Camera.PHONE, Sampling.camera("STARTING", phoneAllowed = true))
        assertNull(Sampling.camera("DISCONNECTED", phoneAllowed = false))
    }

    @Test fun theLineSaysWhatIsFindingTheWearerAndWhenTheLastFixWas() {
        val base = Sampling.Inputs(keySet = true, camera = Sampling.Camera.GLASSES, glassesConnected = true)
        assertEquals("Finding you · glasses · no fix yet", Sampling.line(base))
        assertEquals("Found you · glasses · last fix 2 s ago", Sampling.line(base.copy(found = true, lastFixAgoS = 2)))
        assertEquals("Finding you · glasses · last fix 3 min ago", Sampling.line(base.copy(lastFixAgoS = 190)))
        assertEquals("Finding you · glasses not streaming, using phone camera · no fix yet",
            Sampling.line(base.copy(camera = Sampling.Camera.PHONE, glassesConnected = false)))
        assertEquals("Finding you · glasses · no fix yet · Glasses camera not streaming",
            Sampling.line(base.copy(error = "Glasses camera not streaming. Check the glasses camera consent in Meta AI and try again.")))
    }

    @Test fun theRealProblemComesFirst() {
        assertEquals("Can't find you: no relay key", Sampling.line(Sampling.Inputs(keySet = false, camera = null, glassesConnected = true)))
        assertEquals("Can't find you: relay not reachable · retrying", Sampling.line(Sampling.Inputs(keySet = true,
            camera = Sampling.Camera.GLASSES, glassesConnected = true, error = "Relay unreachable (ConnectException). Check the relay address, Wi-Fi or adb reverse.")))
        assertEquals("Can't find you: connect the glasses or allow the phone camera",
            Sampling.line(Sampling.Inputs(keySet = true, camera = null, glassesConnected = false)))
        assertEquals("Not looking: the live camera was stopped in setup", Sampling.line(Sampling.Inputs(keySet = true, camera = null,
            glassesConnected = true, idle = "Not looking: the live camera was stopped in setup")))
    }
}
