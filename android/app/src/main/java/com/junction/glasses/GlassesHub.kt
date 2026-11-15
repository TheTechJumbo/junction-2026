package com.junction.glasses

import androidx.activity.ComponentActivity
import com.junction.glasses.meta.MetaGlassesAdapter
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

/**
 * The app process's glasses adapter. Without the always-on recording each new Activity gets a fresh
 * adapter that ends with it, as before. While the recording runs ([keepSession]), the same adapter and
 * its glasses session survive the Activity (screen off, other apps, the Activity closed): the service
 * keeps using it, and a new Activity attaches to it for Meta AI hand-offs and the card.
 */
internal object GlassesHub {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    @Volatile var current: MetaGlassesAdapter? = null
        private set

    fun forActivity(activity: ComponentActivity, keepSession: Boolean): MetaGlassesAdapter {
        val existing = current
        if (existing != null && keepSession) {
            existing.attach(activity)
            return existing
        }
        existing?.close()
        return MetaGlassesAdapter(activity, scope).also { current = it }
    }

    /** From the Activity's onDestroy: keeps the session for the recording, else closes the adapter. */
    fun release(activity: ComponentActivity, adapter: MetaGlassesAdapter, keepSession: Boolean) {
        if (keepSession) {
            adapter.detach(activity)
        } else {
            adapter.close()
            if (current === adapter) current = null
        }
    }
}
