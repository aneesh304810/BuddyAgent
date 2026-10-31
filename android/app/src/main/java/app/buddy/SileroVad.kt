package app.buddy

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import java.nio.FloatBuffer
import java.nio.LongBuffer

/**
 * Speech detection on the phone: Silero VAD v5 (MIT licence, assets/silero_vad.onnx, about 2 MB).
 * Says how likely each 32 ms frame (512 samples at 16 kHz) is speech, whatever its loudness: breathing,
 * rustling and hum score near 0, a quiet voice across the table scores high. About a millisecond a frame.
 */
class SileroVad(context: Context) : AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession = env.createSession(
        context.assets.open("silero_vad.onnx").use { it.readBytes() },
        OrtSession.SessionOptions().apply { setIntraOpNumThreads(1) },
    )
    private var state = FloatArray(2 * 1 * 128)
    private val window = FloatArray(CONTEXT + FRAME)  // the last 64 samples of the previous frame, then this one

    /** Speech probability (0..1) of one frame of exactly [FRAME] samples. */
    fun probability(frame: ShortArray): Float {
        System.arraycopy(window, FRAME, window, 0, CONTEXT)
        for (i in 0 until FRAME) window[CONTEXT + i] = frame[i] / 32768f
        OnnxTensor.createTensor(env, FloatBuffer.wrap(window), longArrayOf(1, (CONTEXT + FRAME).toLong())).use { input ->
            OnnxTensor.createTensor(env, FloatBuffer.wrap(state), longArrayOf(2, 1, 128)).use { stateIn ->
                OnnxTensor.createTensor(env, LongBuffer.wrap(longArrayOf(RATE)), longArrayOf()).use { sr ->
                    session.run(mapOf("input" to input, "state" to stateIn, "sr" to sr)).use { out ->
                        @Suppress("UNCHECKED_CAST")
                        val p = (out[0].value as Array<FloatArray>)[0][0]
                        state = (out[1] as OnnxTensor).floatBuffer.let { b -> FloatArray(b.remaining()).also { b.get(it) } }
                        return p
                    }
                }
            }
        }
    }

    fun reset() {
        state.fill(0f)
        window.fill(0f)
    }

    override fun close() = session.close()

    companion object {
        const val FRAME = 512
        const val CONTEXT = 64
        const val RATE = 16_000L
    }
}
