// Deliberately a plain Kotlin/JVM library, not an Android one.
//
// Everything here is decidable without a device: parsing a vless:// link,
// building the Xray config, deciding what bypasses the tunnel. Keeping it
// off the Android toolchain means these run as ordinary JVM tests in
// milliseconds, and it draws a hard line — anything that needs a Context
// does not belong in this module.
plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
}

kotlin { jvmToolchain(17) }

dependencies {
    implementation(libs.kotlinx.serialization.json)
    testImplementation(kotlin("test"))
}

tasks.test { useJUnitPlatform() }
