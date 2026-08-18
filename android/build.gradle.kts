// Plugins are declared here without applying them, so each module can opt in
// to exactly what it needs. That is what lets :core stay a plain Kotlin
// library with no Android toolchain anywhere in its path — and therefore
// testable on any machine, including CI without an Android SDK.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.compose.compiler) apply false
}
