package app.buddy

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The Health screen: Buddy's health page (web/health.html) with a "Scan food" button over it. A scanned barcode
 * opens Plan → Food with the matches; only the barcode's number goes to the owner's server, never a picture.
 */
@Composable
fun HealthScreen(link: ServerLink?) {
    val context = LocalContext.current
    var query by remember { mutableStateOf("") }
    val scan = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val code = result.data?.getStringExtra(FoodScanActivity.BARCODE)
        if (result.resultCode == Activity.RESULT_OK && !code.isNullOrBlank()) {
            // A new address each time, so scanning the same pack twice still looks it up again.
            query = "scanbarcode=" + URLEncoder.encode(code, "UTF-8") + "&t=" + System.currentTimeMillis()
        }
    }
    Box(Modifier.fillMaxSize()) {
        key(query) { BuddyPage(link, "health.html", query) }
        if (link != null) {
            ExtendedFloatingActionButton(
                onClick = { scan.launch(Intent(context, FoodScanActivity::class.java)) },
                modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
            ) { Text("Scan food") }
        }
    }
}

/**
 * Reads a food pack's barcode with the camera, on the phone: ML Kit's bundled scanner works offline and nothing
 * leaves the phone here. The number goes back to the Health screen; the picture is never kept or sent.
 */
class FoodScanActivity : ComponentActivity() {
    companion object {
        const val BARCODE = "barcode"
    }

    private val done = AtomicBoolean(false)

    private fun finishWith(code: String) {
        if (!done.compareAndSet(false, true)) return
        setResult(Activity.RESULT_OK, Intent().putExtra(BARCODE, code))
        finish()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { ScanScreen(onCode = ::finishWith, onCancel = { finish() }) }
    }
}

@Composable
private fun ScanScreen(onCode: (String) -> Unit, onCancel: () -> Unit) {
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
    var typed by remember { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().background(Color(0xFF0E1222)).safeDrawingPadding().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Scan a food pack's barcode", color = Color.White)
        Text("Read on your phone; only the number is looked up. No picture is kept or sent.",
            color = Color(0xFFB8BED0))
        if (allowed) {
            BarcodeCamera(onCode, Modifier.fillMaxWidth().weight(1f))
        } else {
            Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(if (asked) "Camera access is off: type the numbers below instead, or allow it in Settings."
                         else "Buddy needs the camera to read the barcode.", color = Color.White)
                    if (!asked) Button(onClick = { ask.launch(Manifest.permission.CAMERA) }) { Text("Allow camera") }
                }
            }
        }
        OutlinedTextField(
            value = typed, onValueChange = { v -> typed = v.filter { it.isDigit() }.take(14) },
            label = { Text("Or type the numbers under the barcode") }, singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { onCode(typed) }, enabled = typed.length >= 8) { Text("Look up") }
            OutlinedButton(onClick = onCancel) { Text("Cancel") }
        }
    }
}

@androidx.annotation.OptIn(ExperimentalGetImage::class)
@Composable
private fun BarcodeCamera(onCode: (String) -> Unit, modifier: Modifier) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val scanner = remember {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder().setBarcodeFormats(
                Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8, Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E,
            ).build()
        )
    }
    val worker = remember { Executors.newSingleThreadExecutor() }
    DisposableEffect(Unit) {
        onDispose {
            scanner.close()
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
                analysis.setAnalyzer(worker) { proxy: ImageProxy ->
                    val image = proxy.image
                    if (image == null) {
                        proxy.close()
                        return@setAnalyzer
                    }
                    scanner.process(InputImage.fromMediaImage(image, proxy.imageInfo.rotationDegrees))
                        .addOnSuccessListener { codes ->
                            codes.firstNotNullOfOrNull { c -> c.rawValue?.takeIf { v -> v.all { it.isDigit() } } }
                                ?.let(onCode)
                        }
                        .addOnCompleteListener { proxy.close() }
                }
                runCatching {
                    provider.unbindAll()
                    provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                }
            }, ContextCompat.getMainExecutor(context))
            view
        },
    )
}
