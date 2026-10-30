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
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("io.coil-kt:coil-compose:2.7.0")  // photos and map tiles in result cards

    testImplementation("junit:junit:4.13.2")
}
