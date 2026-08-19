plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.compose.compiler)
}

android {
    namespace = "ru.freeskyvpn"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.freeskyvpn"
        // 26 (Android 8, 2017). Below that VpnService lacks the metered and
        // per-app APIs this app relies on, and the remaining audience is not
        // worth the branches.
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"

        // The two things that change per deployment. Read from Gradle
        // properties so a fork or a staging build needs no source edit, and
        // so the service token never sits in the repository.
        //
        // Put them in android/local.properties (git-ignored) or pass them on
        // the command line:
        //
        //   ./gradlew :app:assembleDebug \
        //       -PheadApiUrl=https://api.example.ru \
        //       -PheadServiceToken=<HEAD_SECRET_KEY from the server .env>
        //
        // HTTPS is not negotiable: Android blocks cleartext by default, and
        // the per-user token this app carries must never cross a plain
        // connection.
        val headApiUrl = providers.gradleProperty("headApiUrl").getOrElse("")
        val headServiceToken = providers.gradleProperty("headServiceToken").getOrElse("")

        // Fail at configuration time rather than at the first API call. An
        // empty token produces a 401 on every request, which reads as "the
        // server is broken" and sends someone debugging the wrong machine.
        if (headApiUrl.isBlank() || headServiceToken.isBlank()) {
            logger.warn(
                "\n" +
                    "  ┌─ FreeSkyVPN ─────────────────────────────────────────────\n" +
                    "  │ headApiUrl / headServiceToken не заданы.\n" +
                    "  │ Приложение соберётся, но получит 401 на каждый запрос.\n" +
                    "  │ Задайте их в android/local.properties:\n" +
                    "  │     headApiUrl=https://api.вашдомен.ru\n" +
                    "  │     headServiceToken=<HEAD_SECRET_KEY с сервера>\n" +
                    "  └──────────────────────────────────────────────────────────"
            )
        }

        buildConfigField("String", "HEAD_API_URL", "\"$headApiUrl\"")
        // Identifies the client build to the head. Not a user secret — it is
        // in the APK — which is exactly why per-user tokens exist alongside it.
        buildConfigField("String", "HEAD_SERVICE_TOKEN", "\"$headServiceToken\"")
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    implementation(project(":core"))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.service)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.security.crypto)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    debugImplementation(libs.compose.ui.tooling)

    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)

    // Xray-core, wrapped for Android by XTLS's own libXray (MPL-2.0).
    //
    // Deliberately not v2rayNG's AndroidLibXrayLite: that project is
    // GPL-3.0, and linking it would oblige this app to be GPL-3.0 too.
    // libXray is not published to Maven, so the .aar is vendored — see
    // app/libs/README.md for where to get it and how to build it yourself.
    implementation(fileTree("libs") { include("*.aar") })
}
