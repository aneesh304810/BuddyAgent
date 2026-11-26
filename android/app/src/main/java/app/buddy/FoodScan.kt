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
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.google.mlkit.vision.text.TextRecognition
import kotlinx.coroutines.delay
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.Spacer
import androidx.activity.result.ActivityResult
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
    // From elsewhere: a quick action on Home (Home.kt) opens a section (?section=food, bp) or the scanner, and a scan
    // made on another screen arrives with what it read (scanbarcode=…, scandish=…).
    val jump = remember { Jump.health.also { Jump.health = null } }
    var query by remember {
        mutableStateOf(when {
            jump == null || jump == "scan" -> ""
            jump.startsWith("scan") || jump.startsWith("section=") -> jump
            else -> "section=$jump"
        })
    }
    val scan = rememberFoodScanner { query = it }
    LaunchedEffect(jump) { if (jump == "scan" && link != null) scan() }
    if (link != null && NavLayout.v2) {
        // The redesign: Scan food (used most) and Log BP in a row under the page, so they never cover it, in place
        // of the microphone (health isn't spoken into the conversation).
        Column(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f).fillMaxWidth()) { key(query) { BuddyPage(link, "health.html", query) } }
            Row(Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(onClick = scan, modifier = Modifier.weight(1f).heightIn(min = 48.dp)) {
                    GlyphIcon(Glyph.SCAN, Pal.onChip, 18.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Scan food")
                }
                OutlinedButton(onClick = { query = "section=bp&t=" + System.currentTimeMillis() },
                    modifier = Modifier.weight(1f).heightIn(min = 48.dp)) {
                    GlyphIcon(Glyph.PULSE, Pal.link, 18.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Log BP", color = Pal.link)
                }
            }
        }
        return
    }
    Box(Modifier.fillMaxSize()) {
        key(query) { BuddyPage(link, "health.html", query) }
        if (link != null) {
            ExtendedFloatingActionButton(
                onClick = scan,
                // Beside Buddy's microphone button (56 dp in the same corner, 16 dp in), not under it.
                modifier = Modifier.align(Alignment.BottomEnd).padding(end = 88.dp, bottom = 16.dp),
                containerColor = Pal.accent, contentColor = Pal.onChip,
            ) { Text("Scan food") }
        }
    }
}

/** What the Health page needs from a scan: the barcode read, or the dish tapped (null when cancelled). A new address
 *  each time, so scanning the same thing twice still looks it up again. */
fun scanQuery(result: ActivityResult): String? {
    if (result.resultCode != Activity.RESULT_OK) return null
    val code = result.data?.getStringExtra(FoodScanActivity.BARCODE)
    val dish = result.data?.getStringExtra(FoodScanActivity.DISH)
    val shopped = result.data?.getStringExtra(FoodScanActivity.GROCERIES)
    val stamp = "&t=" + System.currentTimeMillis()
    return when {
        !code.isNullOrBlank() -> "scanbarcode=" + URLEncoder.encode(code, "UTF-8") + stamp
        !dish.isNullOrBlank() -> "scandish=" + URLEncoder.encode(dish, "UTF-8") + stamp
        !shopped.isNullOrBlank() -> "section=groceries" + stamp
        else -> null
    }
}

/** Opens the scanner; [onResult] gets what it read. The phone shares where it is as the scan starts (when sharing is
 *  on in Settings), so the server can tell eating from shopping (scanmeal.py). */
@Composable
fun rememberFoodScanner(onResult: (String) -> Unit): () -> Unit {
    val context = LocalContext.current
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        scanQuery(result)?.let(onResult)
    }
    return remember(launcher) {
        {
            LocationShare.maybeSend(context, force = true)
            launcher.launch(Intent(context, FoodScanActivity::class.java))
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
        const val GROCERIES = "groceries"  // in: open in Groceries mode; out: a shopping trip ended (open Groceries)
    }

    private val done = AtomicBoolean(false)

    private fun finishWith(key: String, value: String) {
        if (value.isBlank() || !done.compareAndSet(false, true)) return
        setResult(Activity.RESULT_OK, Intent().putExtra(key, value.trim()))
        finish()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val link = BuddyPrefs(this).link
        val shopping = intent.getBooleanExtra(GROCERIES, false)
        setContent {
            MaterialTheme(colorScheme = buddyColorScheme()) {
                ScanScreen(link, shopping, onCode = { finishWith(BARCODE, it) }, onDish = { finishWith(DISH, it) },
                    onCancel = { finish() }, onShopDone = { finishWith(GROCERIES, "1") })
            }
        }
    }
}

@Composable
private fun ScanScreen(link: ServerLink?, startShopping: Boolean, onCode: (String) -> Unit, onDish: (String) -> Unit,
                       onCancel: () -> Unit, onShopDone: () -> Unit) {
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
    // Barcode (look one up), Dish (suggest what's on the plate) or Groceries (shopping: the camera stays open, ShopScan.kt)
    var mode by remember { mutableStateOf(if (startShopping) GROCERIES_MODE else "barcode") }
    var picked by remember { mutableStateOf(startShopping) }
    val dishMode = mode == "dish"
    val shopMode = mode == GROCERIES_MODE
    val scope = rememberCoroutineScope()
    val shop = remember { ShopSession(link, scope) }
    LaunchedEffect(Unit) {  // at a store you marked, open in Groceries mode (the phone's place went to the server)
        if (link != null && !startShopping) {
            delay(1500)
            if (!picked && runCatching { GroceryApi.atStore(link) }.getOrDefault(false)) mode = GROCERIES_MODE
        }
    }
    LaunchedEffect(shopMode) { if (shopMode) shop.load() }
    val readCode: (String) -> Unit = { code -> if (shopMode) shop.scanned(code) else onCode(code) }
    var typed by remember { mutableStateOf("") }
    var guesses by remember { mutableStateOf(listOf<Guess>()) }
    var problem by remember { mutableStateOf("") }
    val book = remember { DishBook(context) }
    val latest = remember { AtomicReference<FloatArray?>(null) }  // what the camera sees now, as numbers
    var taught by remember { mutableStateOf(book.names()) }
    var said by remember { mutableStateOf("") }
    var showTaught by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxSize().background(Pal.bg).safeDrawingPadding().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(selected = mode == "barcode", onClick = { mode = "barcode"; picked = true; typed = "" },
                label = { Text("Barcode") })
            FilterChip(selected = dishMode, onClick = { mode = "dish"; picked = true; typed = ""; guesses = emptyList(); said = "" },
                label = { Text("Dish") })
            FilterChip(selected = shopMode, onClick = { mode = GROCERIES_MODE; picked = true; typed = "" },
                label = { Text("Groceries") })
        }
        Text(
            if (dishMode) "Point at the plate and tap the dish it is. Suggestions are made on your phone; no " +
                "picture is kept or sent. It doesn't know dosa, appam, avial, thoran or chapati until you teach it: " +
                "type the name and tap Teach, from 3 to 5 sides."
            else if (shopMode) "Shopping: scan each product. Buddy says if it's good for you, and Add puts it in your " +
                "groceries and ticks it off your list. Only the barcode number is looked up; no picture is kept or sent."
            else "Point at the pack's barcode. Read on your phone; only the number is looked up. No picture is " +
                "kept or sent.",
            color = Pal.dim,
        )
        if (allowed) {
            key(mode, shop.readingFor != 0) {
                if (dishMode) {
                    DishCamera(book, latest, onGuesses = { guesses = it }, onProblem = { problem = it },
                        modifier = Modifier.fillMaxWidth().weight(1f))
                } else {
                    if (shopMode && shop.readingFor != 0) {
                        DateCamera({ text -> shop.sawText(text) }, Modifier.fillMaxWidth().weight(1f))
                    } else {
                        BarcodeCamera(readCode, Modifier.fillMaxWidth().weight(1f))
                    }
                }
            }
        } else {
            Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(if (asked) "Camera access is off: type it below instead, or allow it in Settings."
                         else "Buddy needs the camera for this.", color = Pal.text)
                    if (!asked) Button(onClick = { ask.launch(Manifest.permission.CAMERA) }) { Text("Allow camera") }
                }
            }
        }
        if (dishMode) {
            if (problem.isNotEmpty()) Text(problem, color = Pal.bad)
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                guesses.forEach { g ->
                    OutlinedButton(onClick = { onDish(g.name) }, modifier = Modifier.fillMaxWidth()) {
                        Text(if (g.taught) "${g.name} · one you taught" else "${g.name} · ${(g.score * 100).toInt()}%")
                    }
                }
            }
        }
        if (shopMode) ShopPanel(shop, Modifier.fillMaxWidth())
        OutlinedTextField(
            value = typed,
            onValueChange = { v -> typed = if (dishMode) v.take(60) else v.filter { it.isDigit() }.take(14) },
            label = { Text(if (dishMode) "Or type the dish" else "Or type the numbers under the barcode") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { if (dishMode) onDish(typed) else if (shopMode) { shop.scanned(typed); typed = "" } else onCode(typed) },
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
            if (shopMode && shop.added > 0) Button(onClick = onShopDone) { Text("Done") }
            else OutlinedButton(onClick = onCancel) { Text(if (shopMode) "Done" else "Cancel") }
        }
        if (dishMode && said.isNotEmpty()) Text(said, color = Pal.dim)
        if (dishMode && taught.isNotEmpty()) {
            TextButton(onClick = { showTaught = !showTaught }) { Text("Dishes you taught (${taught.size})") }
            if (showTaught) {
                taught.forEach { (name, views) ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("$name · $views view${if (views == 1) "" else "s"}", color = Pal.text,
                            modifier = Modifier.weight(1f))
                        TextButton(onClick = { book.forget(name); taught = book.names() }) { Text("Forget") }
                    }
                }
                Text("Kept on this phone only, as numbers that describe each view, not pictures.",
                    color = Pal.dim)
            }
        }
    }
}

private const val GROCERIES_MODE = "groceries"

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

/** The words in view, read on the phone (ML Kit's text reader), for the date on a pack (PackDate.kt). */
@Composable
private fun DateCamera(onText: (String) -> Unit, modifier: Modifier) {
    val reader = remember { TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) }
    DisposableEffect(Unit) { onDispose { reader.close() } }
    CameraFeed(modifier) { proxy ->
        val image = proxy.image
        if (image == null) {
            proxy.close()
        } else {
            reader.process(InputImage.fromMediaImage(image, proxy.imageInfo.rotationDegrees))
                .addOnSuccessListener { seen -> if (seen.text.isNotBlank()) onText(seen.text) }
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
