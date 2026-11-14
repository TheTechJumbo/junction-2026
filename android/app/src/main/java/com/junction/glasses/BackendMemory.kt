package com.junction.glasses

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * DEBUG builds only: remembers the backend origin and session token across app restarts. Each value
 * is sealed with AES/GCM under a non-exportable Android Keystore key (field name as associated data)
 * and stored in private preferences; app backup stays disabled. Nothing is logged. Any failure is
 * silent and simply means nothing is remembered, as in release builds.
 */
internal class BackendMemory(
    private val prefs: SharedPreferences,
    private val key: () -> SecretKey,
    private val newCipher: () -> Cipher = { Cipher.getInstance(TRANSFORMATION) },
) {
    fun save(origin: String, token: String): Boolean = try {
        val sealedOrigin = seal(ORIGIN, origin)
        val sealedToken = seal(TOKEN, token)
        prefs.edit().putString(ORIGIN, sealedOrigin).putString(TOKEN, sealedToken).apply()
        true
    } catch (_: Exception) { false }

    /** The remembered origin and token, or null when nothing (readable) is stored. */
    fun load(): Pair<String, String>? {
        val origin = prefs.getString(ORIGIN, null) ?: return null
        val token = prefs.getString(TOKEN, null) ?: return null
        return try { open(ORIGIN, origin) to open(TOKEN, token) } catch (_: Exception) { null }
    }

    fun forget() { prefs.edit().clear().apply() }

    private fun seal(field: String, value: String): String {
        val cipher = newCipher()
        cipher.init(Cipher.ENCRYPT_MODE, key())
        cipher.updateAAD(field.toByteArray(Charsets.UTF_8))
        val sealed = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        return ENCODER.encodeToString(cipher.iv) + SEPARATOR + ENCODER.encodeToString(sealed)
    }

    private fun open(field: String, stored: String): String {
        val parts = stored.split(SEPARATOR)
        require(parts.size == 2)
        val cipher = newCipher()
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, DECODER.decode(parts[0])))
        cipher.updateAAD(field.toByteArray(Charsets.UTF_8))
        return String(cipher.doFinal(DECODER.decode(parts[1])), Charsets.UTF_8)
    }

    companion object {
        private const val FILE = "debug_backend_memory"
        private const val KEY_ALIAS = "junction_debug_backend_memory"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val TAG_BITS = 128
        private const val ORIGIN = "origin"
        private const val TOKEN = "token"
        private const val SEPARATOR = ":"
        private val ENCODER = Base64.getEncoder()
        private val DECODER = Base64.getDecoder()

        /** The Keystore is only touched when something is saved or read back. */
        fun androidKeystore(context: Context) =
            BackendMemory(context.getSharedPreferences(FILE, Context.MODE_PRIVATE), { keystoreKey() })

        private fun keystoreKey(): SecretKey {
            val store = KeyStore.getInstance("AndroidKeyStore")
            store.load(null)
            val existing = store.getKey(KEY_ALIAS, null)
            if (existing is SecretKey) return existing
            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            generator.init(
                KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
            return generator.generateKey()
        }
    }
}
