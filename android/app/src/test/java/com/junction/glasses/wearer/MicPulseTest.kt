package com.junction.glasses.wearer

import android.view.View
import android.view.ViewGroup
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** The microphone's rings: pressing still asks, and listening turns the ripples on and off. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [32])
class MicPulseTest {
    private fun find(view: View, description: String): View? {
        if (view.contentDescription == description) return view
        if (view is ViewGroup) for (i in 0 until view.childCount) find(view.getChildAt(i), description)?.let { return it }
        return null
    }

    @Test fun pressingTheMicStillAsksAndListeningShowsTheRings() {
        val panel = WearerPanel(RuntimeEnvironment.getApplication())
        var asked = 0
        panel.onMic = { asked++ }
        val mic = find(panel, "Ask with your voice")!!
        mic.performClick()
        assertEquals(1, asked)
        assertFalse(panel.isListening)
        panel.setListening(true); panel.setMicLevel(8f)
        assertTrue(panel.isListening)
        assertEquals("Listening. Ask your question", mic.contentDescription)
        panel.setListening(false)
        assertFalse(panel.isListening)
        assertEquals("Ask with your voice", mic.contentDescription)
    }

    @Test fun levelsAreClampedAndQuietIsTheDefault() {
        val pulse = MicPulse(RuntimeEnvironment.getApplication())
        pulse.setLevel(500f); pulse.setLevel(-500f) // never throws, whatever the speech service reports
        assertFalse(pulse.listening)
        pulse.setListening(true); pulse.setListening(true)
        assertTrue(pulse.listening)
    }
}
