package com.junction.glasses.live

import android.content.ContentResolver
import android.content.ContentUris
import android.database.ContentObserver
import android.graphics.Bitmap
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.BaseColumns
import android.provider.MediaStore
import android.util.Size
import java.io.IOException
import java.io.InputStream

/**
 * Glasses videos that Meta AI imported onto the phone, read through MediaStore. Read only: the app
 * never writes, moves or deletes Meta AI's files. Needs READ_MEDIA_VIDEO (Android 13 and later).
 */
internal class GlassesVideoLibrary(private val resolver: ContentResolver) {
    private val collection: Uri = MediaStore.Video.Media.EXTERNAL_CONTENT_URI

    /** Meta AI videos, newest first. */
    fun query(limit: Int = 50): List<GlassesVideo> {
        val projection = arrayOf(
            BaseColumns._ID, MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.MediaColumns.RELATIVE_PATH,
            MediaStore.MediaColumns.DATE_ADDED, MediaStore.MediaColumns.DURATION, MediaStore.MediaColumns.SIZE,
        )
        val found = mutableListOf<GlassesVideo>()
        resolver.query(collection, projection, "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?", arrayOf("%Meta AI%"),
            "${MediaStore.MediaColumns.DATE_ADDED} DESC")?.use { rows ->
            val id = rows.getColumnIndexOrThrow(BaseColumns._ID)
            val name = rows.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
            val path = rows.getColumnIndexOrThrow(MediaStore.MediaColumns.RELATIVE_PATH)
            val added = rows.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED)
            val duration = rows.getColumnIndexOrThrow(MediaStore.MediaColumns.DURATION)
            val size = rows.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE)
            while (rows.moveToNext() && found.size < limit * 2) {
                found += GlassesVideo(rows.getLong(id), rows.getString(name) ?: "", rows.getString(path) ?: "",
                    rows.getLong(added), rows.getLong(duration), rows.getLong(size))
            }
        }
        return GlassesVideos.metaAiNewestFirst(found, limit)
    }

    /** The size MediaStore reports now, or null when the video is gone. */
    fun size(id: Long): Long? =
        resolver.query(uri(id), arrayOf(MediaStore.MediaColumns.SIZE), null, null, null)?.use { rows ->
            if (rows.moveToFirst()) rows.getLong(0) else null
        }

    fun thumbnail(id: Long): Bitmap? = try { resolver.loadThumbnail(uri(id), Size(160, 120), null) } catch (_: Exception) { null }

    /** A fresh read-only stream of the video. */
    fun open(id: Long): InputStream = resolver.openInputStream(uri(id)) ?: throw IOException("The glasses video can't be opened.")

    /** Calls [changed] on the main thread whenever videos are added or changed; unregister with [unwatch]. */
    fun watch(changed: () -> Unit): ContentObserver {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) { changed() }
        }
        resolver.registerContentObserver(collection, true, observer)
        return observer
    }

    fun unwatch(observer: ContentObserver) { resolver.unregisterContentObserver(observer) }

    private fun uri(id: Long): Uri = ContentUris.withAppendedId(collection, id)
}
