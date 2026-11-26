package app.buddy

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.UseCase
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.Button
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.mlkit.common.model.LocalModel
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.label.ImageLabeling
import com.google.mlkit.vision.label.custom.CustomImageLabelerOptions
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * The Health screen: Buddy's health page (web/health.html) with a "Scan food" button over it. A scanned barcode or
 * a dish named from the camera opens Plan → Food with the matches; only the number or the dish's name goes to the
 * owner's server, never a picture.
 */
@Composable
fun HealthScreen(link: ServerLink?) {
    val context = LocalContext.current
    var query by remember { mutableStateOf("") }
    val scan = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val code = result.data?.getStringExtra(FoodScanActivity.BARCODE)
        val dish = result.data?.getStringExtra(FoodScanActivity.DISH)
        if (result.resultCode == Activity.RESULT_OK) {
            // A new address each time, so scanning the same thing twice still looks it up again.
            val stamp = "&t=" + System.currentTimeMillis()
            when {
                !code.isNullOrBlank() -> query = "scanbarcode=" + URLEncoder.encode(code, "UTF-8") + stamp
                !dish.isNullOrBlank() -> query = "scandish=" + URLEncoder.encode(dish, "UTF-8") + stamp
            }
        }
    }
    Box(Modifier.fillMaxSize()) {
        key(query) { BuddyPage(link, "health.html", query) }
        if (link != null) {
            ExtendedFloatingActionButton(
                onClick = { scan.launch(Intent(context, FoodScanActivity::class.java)) },
                // Beside Buddy's microphone button (56 dp in the same corner, 16 dp in), not under it.
                modifier = Modifier.align(Alignment.BottomEnd).padding(end = 88.dp, bottom = 16.dp),
            ) { Text("Scan food") }
        }
    }
}

/**
 * Reads a food pack's barcode, or suggests what a dish is, with the camera, on the phone. ML Kit's bundled barcode
 * scanner and Google's food classifier (assets/food_v1.tflite, Apache 2.0) both run here, offline: no picture is
 * kept or sent. The number, or the dish the owner taps, goes back to the Health screen.
 */
class FoodScanActivity : ComponentActivity() {
    companion object {
        const val BARCODE = "barcode"
        const val DISH = "dish"
    }

    private val done = AtomicBoolean(false)

    private fun finishWith(key: String, value: String) {
        if (value.isBlank() || !done.compareAndSet(false, true)) return
        setResult(Activity.RESULT_OK, Intent().putExtra(key, value.trim()))
        finish()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            ScanScreen(onCode = { finishWith(BARCODE, it) }, onDish = { finishWith(DISH, it) }, onCancel = { finish() })
        }
    }
}

@Composable
private fun ScanScreen(onCode: (String) -> Unit, onDish: (String) -> Unit, onCancel: () -> Unit) {
    val context = LocalContext.current
    var allowed by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED)
    }
    var asked by remember { mutableStateOf(false) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        allowed = ok
        asked = true
    }
    var dishMode by remember { mutableStateOf(false) }
    var typed by remember { mutableStateOf("") }
    var guesses by remember { mutableStateOf(listOf<Guess>()) }
    var problem by remember { mutableStateOf("") }
    val book = remember { DishBook(context) }
    val latest = remember { AtomicReference<FloatArray?>(null) }  // what the camera sees now, as numbers
    var taught by remember { mutableStateOf(book.names()) }
    var said by remember { mutableStateOf("") }
    var showTaught by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxSize().background(Color(0xFF0E1222)).safeDrawingPadding().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(selected = !dishMode, onClick = { dishMode = false; typed = "" }, label = { Text("Barcode") })
            FilterChip(selected = dishMode, onClick = { dishMode = true; typed = ""; guesses = emptyList(); said = "" },
                label = { Text("Dish") })
        }
        Text(
            if (dishMode) "Point at the plate and tap the dish it is. Suggestions are made on your phone; no " +
                "picture is kept or sent. It doesn't know dosa, appam, avial, thoran or chapati until you teach it: " +
                "type the name and tap Teach, from 3 to 5 sides."
            else "Point at the pack's barcode. Read on your phone; only the number is looked up. No picture is " +
                "kept or sent.",
            color = Color(0xFFB8BED0),
        )
        if (allowed) {
            key(dishMode) {
                if (dishMode) {
                    DishCamera(book, latest, onGuesses = { guesses = it }, onProblem = { problem = it },
                        modifier = Modifier.fillMaxWidth().weight(1f))
                } else {
                    BarcodeCamera(onCode, Modifier.fillMaxWidth().weight(1f))
                }
            }
        } else {
            Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(if (asked) "Camera access is off: type it below instead, or allow it in Settings."
                         else "Buddy needs the camera for this.", color = Color.White)
                    if (!asked) Button(onClick = { ask.launch(Manifest.permission.CAMERA) }) { Text("Allow camera") }
                }
            }
        }
        if (dishMode) {
            if (problem.isNotEmpty()) Text(problem, color = Color(0xFFFFB4A9))
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                guesses.forEach { g ->
                    OutlinedButton(onClick = { onDish(g.name) }, modifier = Modifier.fillMaxWidth()) {
                        Text(if (g.taught) "${g.name} · one you taught" else "${g.name} · ${(g.score * 100).toInt()}%")
                    }
                }
            }
        }
        OutlinedTextField(
            value = typed,
            onValueChange = { v -> typed = if (dishMode) v.take(60) else v.filter { it.isDigit() }.take(14) },
            label = { Text(if (dishMode) "Or type the dish" else "Or type the numbers under the barcode") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { if (dishMode) onDish(typed) else onCode(typed) },
                enabled = if (dishMode) typed.trim().length >= 2 else typed.length >= 8,
            ) { Text("Look up") }
            if (dishMode) {
                OutlinedButton(
                    onClick = {
                        val view = latest.get()
                        said = if (view == null) "Point the camera at the dish first." else {
                            val n = book.teach(typed, view)
                            taught = book.names()
                            if (n == 0) "That's the most dishes Buddy keeps: forget one first."
                            else "Saved view $n of ${typed.trim()}. Turn the plate or step to one side and tap Teach " +
                                "again: 3 to 5 views work best."
                        }
                    },
                    enabled = allowed && typed.trim().length >= 2,
                ) { Text("Teach") }
            }
            OutlinedButton(onClick = onCancel) { Text("Cancel") }
        }
        if (dishMode && said.isNotEmpty()) Text(said, color = Color(0xFFB8BED0))
        if (dishMode && taught.isNotEmpty()) {
            TextButton(onClick = { showTaught = !showTaught }) { Text("Dishes you taught (${taught.size})") }
            if (showTaught) {
                taught.forEach { (name, views) ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("$name · $views view${if (views == 1) "" else "s"}", color = Color.White,
                            modifier = Modifier.weight(1f))
                        TextButton(onClick = { book.forget(name); taught = book.names() }) { Text("Forget") }
                    }
                }
                Text("Kept on this phone only, as numbers that describe each view, not pictures.",
                    color = Color(0xFFB8BED0))
            }
        }
    }
}

/** A suggestion for the dish in view: from the dishes the owner taught, or from Google's food classifier. */
private data class Guess(val name: String, val score: Float, val taught: Boolean)

/**
 * The camera preview with an analyzer on every frame (the latest only, when the analyzer is busy). Leaving it (or
 * switching between Barcode and Dish) lets go of the camera before its analyzer's thread stops.
 */
@Composable
private fun CameraFeed(modifier: Modifier, analyze: (ImageProxy) -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val worker = remember { Executors.newSingleThreadExecutor() }
    val bound = remember { arrayOfNulls<ProcessCameraProvider>(1) }
    val uses = remember { mutableListOf<UseCase>() }
    DisposableEffect(Unit) {
        onDispose {
            uses.filterIsInstance<ImageAnalysis>().forEach { it.clearAnalyzer() }
            runCatching { bound[0]?.unbind(*uses.toTypedArray()) }
            worker.shutdown()
        }
    }
    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            val view = PreviewView(ctx)
            val providerFuture = ProcessCameraProvider.getInstance(ctx)
            providerFuture.addListener({
                val provider = providerFuture.get()
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(view.surfaceProvider) }
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
                analysis.setAnalyzer(worker) { proxy -> analyze(proxy) }
                runCatching {
                    provider.unbindAll()
                    provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                    bound[0] = provider
                    uses.addAll(listOf(preview, analysis))
                }
            }, ContextCompat.getMainExecutor(context))
            view
        },
    )
}

@androidx.annotation.OptIn(ExperimentalGetImage::class)
@Composable
private fun BarcodeCamera(onCode: (String) -> Unit, modifier: Modifier) {
    val scanner = remember {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder().setBarcodeFormats(
                Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8, Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E,
            ).build()
        )
    }
    DisposableEffect(Unit) { onDispose { scanner.close() } }
    CameraFeed(modifier) { proxy ->
        val image = proxy.image
        if (image == null) {
            proxy.close()
        } else {
            scanner.process(InputImage.fromMediaImage(image, proxy.imageInfo.rotationDegrees))
                .addOnSuccessListener { codes ->
                    codes.firstNotNullOfOrNull { c -> c.rawValue?.takeIf { v -> v.all { it.isDigit() } } }?.let(onCode)
                }
                .addOnCompleteListener { proxy.close() }
        }
    }
}

/**
 * Suggestions a few times a second, on the phone: the dishes the owner taught (DishEmbedder and DishBook) first, then
 * Google's food classifier's likeliest. Each frame is copied, released to the camera, and dropped after use.
 */
@Composable
private fun DishCamera(
    book: DishBook,
    latest: AtomicReference<FloatArray?>,
    onGuesses: (List<Guess>) -> Unit,
    onProblem: (String) -> Unit,
    modifier: Modifier,
) {
    val context = LocalContext.current
    val names = remember {  // the model's English dish names, by output index (0 is "no dish")
        runCatching { context.assets.open("food_v1_labels_en.txt").bufferedReader().readLines() }.getOrDefault(emptyList())
    }
    val labeler = remember {
        runCatching {
            val model = LocalModel.Builder().setAssetFilePath("food_v1.tflite").build()
            ImageLabeling.getClient(
                CustomImageLabelerOptions.Builder(model).setConfidenceThreshold(0.05f).setMaxResultCount(4).build()
            )
        }.getOrNull()
    }
    val embedder = remember { runCatching { DishEmbedder(context) }.getOrNull() }
    DisposableEffect(Unit) {
        onDispose {
            labeler?.close()
            embedder?.close()
            latest.set(null)
        }
    }
    if (labeler == null && embedder == null) {
        LaunchedEffect(Unit) { onProblem("Dish suggestions aren't available on this phone: type the dish below.") }
        return
    }
    val last = remember { longArrayOf(0L) }
    CameraFeed(modifier) { proxy ->
        val now = SystemClock.elapsedRealtime()
        if (now - last[0] < 700) {
            proxy.close()
            return@CameraFeed
        }
        last[0] = now
        val rotation = proxy.imageInfo.rotationDegrees
        val frame = runCatching { proxy.toBitmap() }.getOrNull()
        proxy.close()
        if (frame == null) return@CameraFeed
        val view = runCatching { embedder?.embed(frame, rotation) }.getOrNull()
        latest.set(view)
        val mine = view?.let { book.match(it) }.orEmpty().map { (name, score) -> Guess(name, score, true) }
        if (labeler == null) {
            onGuesses(mine)
            return@CameraFeed
        }
        labeler.process(InputImage.fromBitmap(frame, rotation))
            .addOnSuccessListener { labels ->
                val theirs = labels.mapNotNull { l ->
                    val name = names.getOrNull(l.index)?.trim().takeUnless { it.isNullOrEmpty() } ?: l.text
                    if (l.index == 0 || name.startsWith("__") || name.startsWith("/g/")) null
                    else Guess(name, l.confidence, false)
                }.filter { g -> mine.none { it.name.equals(g.name, ignoreCase = true) } }
                onGuesses((mine + theirs).take(3))
            }
            .addOnFailureListener {
                onGuesses(mine)
                onProblem("Couldn't read the picture: type the dish below.")
            }
    }
}
