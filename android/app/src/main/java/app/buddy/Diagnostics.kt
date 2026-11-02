package app.buddy

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Base64
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.TimeUnit
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Diagnostics: events and measurements (never audio, never words) to find what makes Buddy feel slow
 * or deaf: the microphone, Android putting the app to sleep, the network, or the server.
 * On by default; Settings → "Collect diagnostics" turns it off (and the server can too).
 * Self-contained on purpose: this file plus one-line hooks, so it's easy to switch off or revert.
 */
object Diag {
    private val buffer = ArrayDeque<JSONObject>()
    private const val MAX = 2000
    @Volatile var serverOff = false
    @Volatile var enabled: () -> Boolean = { true }

    fun on(): Boolean = enabled() && !serverOff

    fun event(category: String, event: String, vararg data: Pair<String, Any?>) {
        if (!on()) return
        val d = JSONObject()
        for ((k, v) in data) if (v != null) d.put(k, v)
        val e = JSONObject().put("at", System.currentTimeMillis()).put("category", category).put("event", event).put("data", d)
        synchronized(buffer) {
            buffer.addLast(e)
            while (buffer.size > MAX) buffer.removeFirst()  // offline for long: keep the newest
        }
    }

    /** Send what's waiting (in batches); kept if the connection is down. */
    fun flush(send: (JSONObject) -> Boolean) {
        if (!on()) { synchronized(buffer) { buffer.clear() }; return }
        while (true) {
            val batch = synchronized(buffer) { buffer.take(200) }
            if (batch.isEmpty()) return
            if (!send(JSONObject().put("type", "diag").put("events", JSONArray(batch)))) return
            synchronized(buffer) { repeat(batch.size) { if (buffer.isNotEmpty()) buffer.removeFirst() } }
        }
    }

    /** Loudness in dB (0-90) from a frame's RMS: the same scale the server's checks use. */
    fun db(rms: Double): Double = if (rms <= 1.0) 0.0 else 20 * log10(rms)
}

/** Watches the network, audio devices, power state and the connection; flushes and pings every minute. */
class DiagMonitor(private val context: Context, private val send: (JSONObject) -> Boolean) {
    private val main = Handler(Looper.getMainLooper())
    private var offlineSince = 0L
    private var lastPower = ""
    private val connectivity = context.getSystemService(ConnectivityManager::class.java)
    private val audio = context.getSystemService(AudioManager::class.java)

    private val netCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
            val net = netName(caps)
            if (net != lastNet) { lastNet = net; Diag.event("network", "changed", "net" to net) }
        }
        override fun onLost(network: Network) { lastNet = "none"; Diag.event("network", "lost") }
    }
    @Volatile private var lastNet = "?"

    private val deviceCallback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>) {
            added.filter { it.isSource }.forEach { Diag.event("mic", "device_added", "type" to deviceName(it.type)) }
        }
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>) {
            removed.filter { it.isSource }.forEach {
                val name = deviceName(it.type)
                Diag.event("mic", if (name.startsWith("bluetooth")) "bt_disconnected" else "device_removed", "type" to name)
            }
        }
    }

    private val tick = object : Runnable {
        override fun run() {
            power()
            Diag.flush(send)
            if (Diag.on()) send(JSONObject().put("type", "diag_ping").put("t", SystemClock.elapsedRealtime()))
            main.postDelayed(this, 60_000)
        }
    }

    fun start() {
        runCatching { connectivity?.registerDefaultNetworkCallback(netCallback) }
        runCatching { audio?.registerAudioDeviceCallback(deviceCallback, main) }
        main.postDelayed(tick, 5_000)
    }

    fun stop() {
        main.removeCallbacksAndMessages(null)
        runCatching { connectivity?.unregisterNetworkCallback(netCallback) }
        runCatching { audio?.unregisterAudioDeviceCallback(deviceCallback) }
        Diag.flush(send)
    }

    fun pong(t: Long) {
        if (t > 0) Diag.event("network", "ping", "rtt_ms" to (SystemClock.elapsedRealtime() - t), "net" to lastNet)
    }

    fun connection(online: Boolean) {
        val now = SystemClock.elapsedRealtime()
        if (!online && offlineSince == 0L) {
            offlineSince = now
            Diag.event("network", "disconnected", "net" to lastNet)
        } else if (online && offlineSince != 0L) {
            Diag.event("network", "reconnected", "offline_ms" to (now - offlineSince), "net" to lastNet)
            offlineSince = 0L
            Diag.flush(send)
        }
    }

    private fun power() {
        val pm = context.getSystemService(PowerManager::class.java) ?: return
        val state = listOf(pm.isPowerSaveMode, pm.isDeviceIdleMode, pm.isIgnoringBatteryOptimizations(context.packageName),
            pm.isInteractive).joinToString()
        if (state == lastPower) return
        lastPower = state
        Diag.event("background", "power_state", "battery_saver" to pm.isPowerSaveMode, "doze" to pm.isDeviceIdleMode,
            "unrestricted" to pm.isIgnoringBatteryOptimizations(context.packageName), "screen_on" to pm.isInteractive)
    }

    companion object {
        fun netName(caps: NetworkCapabilities): String = when {
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "mobile"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            else -> "other"
        }

        fun deviceName(type: Int): String = when (type) {
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "bluetooth_sco"
            AudioDeviceInfo.TYPE_BLE_HEADSET -> "bluetooth_le"
            AudioDeviceInfo.TYPE_WIRED_HEADSET -> "wired"
            AudioDeviceInfo.TYPE_USB_HEADSET -> "usb"
            AudioDeviceInfo.TYPE_BUILTIN_MIC -> "phone"
            else -> "other_$type"
        }
    }
}

/** The server's diagnostics: health summary, setup test, speech test, export. */
object DiagApi {
    private val http = OkHttpClient.Builder().callTimeout(60, TimeUnit.SECONDS).build()

    suspend fun post(link: ServerLink, path: String, body: JSONObject): JSONObject = withContext(Dispatchers.IO) {
        http.newCall(Request.Builder().url(link.baseUrl + path).header("Authorization", "Bearer ${link.token}")
            .post(body.toString().toRequestBody("application/json".toMediaType())).build()).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) error(runCatching { JSONObject(text).optString("detail") }.getOrNull()?.ifBlank { null }
                ?: "server said ${r.code}")
            JSONObject(text)
        }
    }

    suspend fun pingMs(link: ServerLink): Long = withContext(Dispatchers.IO) {
        val started = SystemClock.elapsedRealtime()
        http.newCall(Request.Builder().url(link.baseUrl + "/health").build()).execute().close()
        SystemClock.elapsedRealtime() - started
    }

    /** Download the export and hand it to an app (Files, Drive, email...). */
    suspend fun share(context: Context, link: ServerLink, format: String) {
        val bytes = withContext(Dispatchers.IO) {
            http.newCall(Request.Builder().url("${link.baseUrl}/api/diagnostics/export?days=30&format=$format")
                .header("Authorization", "Bearer ${link.token}").build()).execute().use { r ->
                if (!r.isSuccessful) error("server said ${r.code}")
                r.body?.bytes() ?: ByteArray(0)
            }
        }
        val file = withContext(Dispatchers.IO) {
            val dir = File(context.cacheDir, "diagnostics").apply { mkdirs() }
            dir.listFiles()?.forEach { it.delete() }
            File(dir, "buddy-diagnostics.$format").apply { writeBytes(bytes) }
        }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
        context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType(
            if (format == "json") "application/json" else "text/csv").putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), "Share diagnostics").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/** 'Test my setup': 3 s of quiet, a test sentence, the server's speech to text, a ping, and checks with fixes. */
@Composable
fun SetupTestCard(link: ServerLink?) {
    var step by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<JSONObject?>(null) }
    var running by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    Column(Modifier.fillMaxWidth().background(Color(0xFF151C33), RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("Test my setup", color = Color.White, fontWeight = FontWeight.SemiBold)
        Text("About 15 seconds: checks the background noise, your voice level, the link to your server, speech to text " +
            "and the phone's settings. Try it at home, outside, and with each pair of earbuds.", color = Color(0xFF8A93B2), fontSize = 12.sp)
        OutlinedButton(enabled = !running && link != null, onClick = {
            running = true; result = null
            scope.launch {
                val service = BuddyService.instance
                val wasListening = BuddyState.ui.value.listening
                service?.stopListening()
                try {
                    step = "Stay quiet for 3 seconds…"
                    delay(400)
                    val quiet = withContext(Dispatchers.IO) { SetupRecorder.record(3_000) }
                    step = "Now say: \"Buddy, what's the weather in Lima tomorrow?\""
                    val spoken = withContext(Dispatchers.IO) { SetupRecorder.record(4_500) }
                    step = "Checking with your server…"
                    val noise = Diag.db(quiet.rms)
                    val speech = Diag.db(spoken.speechRms(quiet.rms))
                    val ping = runCatching { DiagApi.pingMs(link!!) }.getOrNull()
                    val stt = runCatching {
                        DiagApi.post(link!!, "/api/diagnostics/stt", JSONObject()
                            .put("pcm", Base64.encodeToString(spoken.pcm, Base64.NO_WRAP)).put("lang", "auto"))
                    }.getOrNull()
                    val heard = stt?.optString("text").orEmpty().lowercase()
                    val pm = context.getSystemService(PowerManager::class.java)
                    val body = JSONObject().put("noise_db", noise).put("speech_db", speech).put("snr_db", speech - noise)
                        .put("clipped", spoken.clipped).put("mic", if (headsetMic(context)) "headset" else "phone")
                        .put("battery_ok", pm?.isIgnoringBatteryOptimizations(context.packageName) == true)
                        .put("notifications_ok", NotificationManagerCompat.getEnabledListenerPackages(context)
                            .contains(context.packageName))
                    if (ping != null) body.put("rtt_ms", ping)
                    if (stt != null) body.put("stt_ms", stt.optLong("ms")).put("engine", stt.optString("engine"))
                        .put("heard_ok", "weather" in heard || "lima" in heard)
                    result = DiagApi.post(link!!, "/api/diagnostics/test", body)
                    step = ""
                } catch (e: Exception) {
                    step = "Test failed: ${e.message}"
                } finally {
                    running = false
                    if (wasListening) service?.startListening(Mode.TALK)
                }
            }
        }) { Text(if (running) "Testing…" else "Start the test") }
        if (step.isNotBlank()) Text(step, color = Color(0xFFFFC46B), fontSize = 14.sp)
        result?.let { r ->
            Text("${r.optInt("passed")} of ${r.optInt("total")} good · ${r.optString("summary")}", color = Color.White, fontSize = 14.sp)
            val checks = r.optJSONArray("checks") ?: JSONArray()
            for (i in 0 until checks.length()) {
                val c = checks.getJSONObject(i)
                val ok = c.optBoolean("ok")
                Text("${if (ok) "✓" else "⚠"} ${c.optString("name")}: ${c.optString("value")}",
                    color = if (ok) Color(0xFF7BE0A5) else Color(0xFFFFC46B), fontSize = 13.sp)
                if (!ok && c.optString("fix").isNotBlank()) Text("   ${c.optString("fix")}", color = Color(0xFF8A93B2), fontSize = 12.sp)
            }
        }
    }
}

private fun headsetMic(context: Context): Boolean =
    context.getSystemService(AudioManager::class.java)?.getDevices(AudioManager.GET_DEVICES_INPUTS)?.any {
        it.type in setOf(AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_USB_HEADSET)
    } == true

/** A short recording for the setup test (16 kHz mono), with its loudness. */
private object SetupRecorder {
    class Take(val pcm: ByteArray, val rms: Double, val frames: List<Double>, val clipped: Boolean) {
        /** The speech frames' loudness: frames well above the quiet part. */
        fun speechRms(noise: Double): Double {
            val loud = frames.filter { it > max(noise * 2, 200.0) }
            return if (loud.isEmpty()) rms else sqrt(loud.sumOf { it * it } / loud.size)
        }
    }

    @SuppressLint("MissingPermission")  // only reachable once the microphone is allowed
    fun record(ms: Long): Take {
        val rate = 16_000
        val minBuf = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, rate, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, max(minBuf, rate))
        val out = ByteArrayOutputStream()
        val frames = mutableListOf<Double>()
        var peak = 0
        val frame = ShortArray(512)
        rec.startRecording()
        val end = SystemClock.elapsedRealtime() + ms
        try {
            while (SystemClock.elapsedRealtime() < end) {
                val n = rec.read(frame, 0, frame.size)
                if (n <= 0) continue
                var sum = 0.0
                for (i in 0 until n) {
                    val v = frame[i].toInt()
                    sum += v.toDouble() * v
                    peak = max(peak, kotlin.math.abs(v))
                    out.write(v and 0xff); out.write((v shr 8) and 0xff)
                }
                frames += sqrt(sum / n)
            }
        } finally {
            runCatching { rec.stop() }
            rec.release()
        }
        val all = frames.ifEmpty { listOf(0.0) }
        return Take(out.toByteArray(), sqrt(all.sumOf { it * it } / all.size), frames, peak >= 32_000)
    }
}
