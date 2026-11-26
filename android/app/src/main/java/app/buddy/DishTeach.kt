package app.buddy

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import org.tensorflow.lite.Interpreter
import java.io.File
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Dishes the owner taught Buddy on the phone. Each "view" is 1,024 numbers describing what the camera saw (from
 * DishEmbedder), never the picture. They stay on this phone, in its no-backup folder: nothing is sent anywhere.
 */
class DishBook(context: Context) {
    companion object {
        const val MATCH = 0.6f        // how alike a view must be to suggest a taught dish (cosine; 1 is identical)
        const val MAX_VIEWS = 8       // views kept per dish (the newest)
        const val MAX_DISHES = 60
    }

    private val file = File(context.noBackupFilesDir, "taught_dishes.json")
    private val dishes = LinkedHashMap<String, MutableList<FloatArray>>()

    init {
        runCatching {
            if (file.exists()) {
                val all = JSONObject(file.readText()).getJSONArray("dishes")
                for (i in 0 until all.length()) {
                    val d = all.getJSONObject(i)
                    val views = d.getJSONArray("views")
                    dishes[d.getString("name")] = MutableList(views.length()) { decode(views.getString(it)) }
                }
            }
        }
    }

    @Synchronized
    fun names(): List<Pair<String, Int>> = dishes.map { it.key to it.value.size }

    /** Adds what the camera sees now as one more view of this dish; returns how many views it has. */
    @Synchronized
    fun teach(name: String, view: FloatArray): Int {
        val clean = name.trim().split(Regex("\\s+")).joinToString(" ").take(40).replaceFirstChar { it.uppercase() }
        val key = dishes.keys.firstOrNull { it.equals(clean, ignoreCase = true) } ?: clean
        if (key !in dishes && dishes.size >= MAX_DISHES) return 0
        val views = dishes.getOrPut(key) { mutableListOf() }
        views.add(view.copyOf())
        while (views.size > MAX_VIEWS) views.removeAt(0)
        save()
        return views.size
    }

    @Synchronized
    fun forget(name: String) {
        dishes.remove(name)
        save()
    }

    /** The taught dishes this view looks most like (best view of each), likeliest first. */
    @Synchronized
    fun match(view: FloatArray, at: Float = MATCH): List<Pair<String, Float>> =
        dishes.mapNotNull { (name, views) ->
            views.maxOfOrNull { dot(it, view) }?.takeIf { it >= at }?.let { name to it }
        }.sortedByDescending { it.second }.take(2)

    private fun dot(a: FloatArray, b: FloatArray): Float {
        if (a.size != b.size) return 0f
        var s = 0f
        for (i in a.indices) s += a[i] * b[i]
        return s
    }

    private fun save() {
        val all = JSONArray()
        dishes.forEach { (name, views) ->
            all.put(JSONObject().put("name", name).put("views", JSONArray(views.map { encode(it) })))
        }
        val temp = File(file.parentFile, file.name + ".new")
        temp.writeText(JSONObject().put("version", 1).put("dishes", all).toString())
        temp.renameTo(file)
    }

    private fun encode(v: FloatArray): String {
        val bytes = ByteBuffer.allocate(4 * v.size).order(ByteOrder.LITTLE_ENDIAN)
        v.forEach { bytes.putFloat(it) }
        return Base64.encodeToString(bytes.array(), Base64.NO_WRAP)
    }

    private fun decode(s: String): FloatArray {
        val bytes = ByteBuffer.wrap(Base64.decode(s, Base64.NO_WRAP)).order(ByteOrder.LITTLE_ENDIAN)
        return FloatArray(bytes.remaining() / 4) { bytes.getFloat() }
    }
}

/**
 * Google's MobileNet-V3 (small) image embedder from MediaPipe (assets/mobilenet_v3_small_embedder.tflite, Apache
 * 2.0), run on the phone with TensorFlow Lite: a camera frame in, 1,024 numbers out (scaled to length 1, so two
 * views compare by their dot product). Safe to close while a frame is being read: it then returns null.
 */
class DishEmbedder(context: Context) : AutoCloseable {
    private val interpreter: Interpreter
    private val size: Int
    private val dim: Int
    private val input: ByteBuffer
    private var closed = false

    init {
        val asset = context.assets.openFd("mobilenet_v3_small_embedder.tflite")
        val model = FileInputStream(asset.fileDescriptor).use { stream ->
            stream.channel.map(FileChannel.MapMode.READ_ONLY, asset.startOffset, asset.declaredLength)
        }
        asset.close()
        interpreter = Interpreter(model, Interpreter.Options().setNumThreads(2))
        size = interpreter.getInputTensor(0).shape()[1]          // 224: a square RGB picture, values 0 to 1
        dim = interpreter.getOutputTensor(0).shape()[1]          // 1,024
        input = ByteBuffer.allocateDirect(4 * size * size * 3).order(ByteOrder.nativeOrder())
    }

    @Synchronized
    fun embed(frame: Bitmap, rotation: Int): FloatArray? {
        if (closed) return null
        val side = min(frame.width, frame.height)
        val turn = Matrix().apply { postRotate(rotation.toFloat()) }
        val square = Bitmap.createBitmap(frame, (frame.width - side) / 2, (frame.height - side) / 2, side, side, turn, true)
        val small = Bitmap.createScaledBitmap(square, size, size, true)
        val pixels = IntArray(size * size)
        small.getPixels(pixels, 0, size, 0, 0, size, size)
        input.rewind()
        for (p in pixels) {
            input.putFloat(((p shr 16) and 0xFF) / 255f)
            input.putFloat(((p shr 8) and 0xFF) / 255f)
            input.putFloat((p and 0xFF) / 255f)
        }
        input.rewind()
        val out = Array(1) { FloatArray(dim) }
        interpreter.run(input, out)
        var norm = 0f
        for (x in out[0]) norm += x * x
        norm = sqrt(norm).coerceAtLeast(1e-6f)
        return FloatArray(dim) { out[0][it] / norm }
    }

    @Synchronized
    override fun close() {
        if (!closed) {
            closed = true
            interpreter.close()
        }
    }
}
