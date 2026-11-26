plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// CI passes a build number and, when the signing secrets exist, a release key.
// The same key must sign every build, or Android refuses to install updates.
val buildNumber = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
val keystorePath: String? = System.getenv("BUDDY_KEYSTORE_PATH")

android {
    namespace = "app.buddy"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.buddy"
        minSdk = 31
        targetSdk = 35
        versionCode = buildNumber
        versionName = "0.3.$buildNumber"
        ndk { abiFilters += "arm64-v8a" }  // the speech model's runtime for 64-bit ARM phones only: a smaller app
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("BUDDY_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("BUDDY_KEY_ALIAS")
                keyPassword = System.getenv("BUDDY_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName(if (keystorePath != null) "release" else "debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }
    androidResources {
        noCompress += "tflite"  // ML Kit reads the dish model straight from the app, so it must stay uncompressed
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.health.connect:connect-client:1.1.0-alpha07")  // Buddy Connect's Health group
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("io.coil-kt:coil-compose:2.7.0")  // photos and map tiles in result cards
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.19.2")  // speech detection (Silero VAD)
    // Health → Scan food: the camera, and Google's barcode scanner with its model bundled (works offline;
    // no picture leaves the phone).
    implementation("androidx.camera:camera-camera2:1.4.1")
    implementation("androidx.camera:camera-lifecycle:1.4.1")
    implementation("androidx.camera:camera-view:1.4.1")
    // CameraX hands back its camera as a Guava ListenableFuture but carries that type only at run time, and another
    // library already resolves Guava's stand-alone ListenableFuture to its empty placeholder: use Guava itself.
    implementation("com.google.guava:guava:33.3.1-android")
    implementation("com.google.mlkit:barcode-scanning:17.3.0")
    // Health → Scan food → Dish: ML Kit runs Google's food classifier (assets/food_v1.tflite) on the phone.
    implementation("com.google.mlkit:image-labeling-custom:17.0.3")

    testImplementation("junit:junit:4.13.2")
}
