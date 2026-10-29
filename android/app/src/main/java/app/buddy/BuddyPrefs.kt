package app.buddy

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Buddy's settings on the phone. The server token is encrypted with a key held in the Android Keystore. */
class BuddyPrefs(context: Context) {
    private val prefs = context.getSharedPreferences("buddy", Context.MODE_PRIVATE)

    var link: ServerLink?
        get() {
            val base = prefs.getString("server", null) ?: return null
            val token = prefs.getString("token", null)?.let(TokenCipher::decrypt) ?: return null
            return ServerLink(base, token)
        }
        set(value) {
            prefs.edit()
                .putString("server", value?.baseUrl)
                .putString("token", value?.token?.let(TokenCipher::encrypt))
                .apply()
        }

    /** Talk mode: keep listening after each reply instead of one question per tap. */
    var handsFree: Boolean
        get() = prefs.getBoolean("handsFree", true)
        set(value) = prefs.edit().putBoolean("handsFree", value).apply()

    /** Listen-in mode: the language people around you are speaking. */
    var roomLanguage: String
        get() = prefs.getString("roomLanguage", AUTO_LANGUAGE) ?: AUTO_LANGUAGE
        set(value) = prefs.edit().putString("roomLanguage", value).apply()

    /** Talk mode: hear you through the earbud microphone instead of the phone's. */
    var earbudMic: Boolean
        get() = prefs.getBoolean("earbudMic", true)  // with earbuds in, hear you through them
        set(value) = prefs.edit().putBoolean("earbudMic", value).apply()

    /** Listen-in microphone: "auto" (earbuds while the phone is in a pocket), "phone" or "earbuds". */
    var listenMic: String
        get() = prefs.getString("listenMic", null)
            ?: if (prefs.getBoolean("earbudMicListenIn", false)) "earbuds" else "auto"
        set(value) = prefs.edit().putString("listenMic", value).apply()

    /** When earbuds connect: "listen" (Listen-in, say "Hey Buddy" to talk), "talk" or "off". */
    var earbudsStart: String
        get() = prefs.getString("earbudsStart", "listen") ?: "listen"
        set(value) = prefs.edit().putString("earbudsStart", value).apply()

    /** Speech recognition: "phone" (Google's, on the phone) or "server" (Whisper on your server). */
    var speechEngine: String
        get() = prefs.getString("speechEngine", "phone") ?: "phone"
        set(value) = prefs.edit().putString("speechEngine", value).apply()

    /** Send the phone's location to your server when Buddy is used, for "near me" searches. */
    var shareLocation: Boolean
        get() = prefs.getBoolean("shareLocation", false)
        set(value) = prefs.edit().putBoolean("shareLocation", value).apply()

    /** How this phone is labelled on the server, e.g. "Fold 8" or "Work phone". */
    var deviceName: String
        get() = prefs.getString("deviceName", "") ?: ""
        set(value) = prefs.edit().putString("deviceName", value.trim()).apply()

    /** Buddy's voice: a text-to-speech voice name, or "" for the phone's default. */
    var voiceName: String
        get() = prefs.getString("voiceName", "") ?: ""
        set(value) = prefs.edit().putString("voiceName", value).apply()

    /** Send this phone's calendars to Buddy. */
    var shareCalendar: Boolean
        get() = prefs.getBoolean("shareCalendar", false)
        set(value) = prefs.edit().putBoolean("shareCalendar", value).apply()

    /** Work (Exchange/Outlook) calendars: send only busy times, never titles. */
    var workBusyOnly: Boolean
        get() = prefs.getBoolean("workBusyOnly", true)
        set(value) = prefs.edit().putBoolean("workBusyOnly", value).apply()
}

private object TokenCipher {
    private const val ALIAS = "buddy-token"
    private const val TRANSFORM = "AES/GCM/NoPadding"
    private const val IV_BYTES = 12

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }

    fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val sealed = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(sealed, Base64.NO_WRAP)
    }

    fun decrypt(stored: String): String? = runCatching {
        val bytes = Base64.decode(stored, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, IV_BYTES))
        String(cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES), Charsets.UTF_8)
    }.getOrNull()
}
