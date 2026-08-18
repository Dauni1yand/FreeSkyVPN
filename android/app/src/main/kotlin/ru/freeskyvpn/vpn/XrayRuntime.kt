package ru.freeskyvpn.vpn

import android.util.Log
import java.io.File

/**
 * The Xray process, behind an interface.
 *
 * Xray-core is reached through XTLS's own gomobile wrapper, libXray
 * (MPL-2.0). It is not on Maven, so the .aar is vendored in `app/libs/` —
 * see the README there. v2rayNG's AndroidLibXrayLite would have been the
 * easier drop-in and is deliberately not used: it is GPL-3.0, and linking
 * it would oblige this app to be GPL-3.0 as well.
 *
 * The interface exists so that everything above it — the service, the
 * config builder, the UI — can be reasoned about and swapped without the
 * native library present. [reflective] is what keeps this file compiling
 * before the .aar is dropped in, and what makes the failure mode a clear
 * message instead of a link error.
 */
interface XrayRuntime {
    fun start(config: File, assetsDir: File): Boolean
    fun stop()
    val isRunning: Boolean

    companion object {
        /**
         * Binds to libXray if it is present, and degrades to a loud no-op
         * if it is not.
         *
         * Reflection rather than a direct call so that a build without the
         * vendored .aar still compiles and runs — it simply cannot connect,
         * and says so, which is a far better first experience for anyone
         * cloning this repository than an unresolved symbol.
         */
        fun reflective(): XrayRuntime = ReflectiveXray()
    }
}

private class ReflectiveXray : XrayRuntime {

    private var running = false

    override val isRunning: Boolean get() = running

    override fun start(config: File, assetsDir: File): Boolean {
        val entry = libXray() ?: run {
            Log.e(TAG, "libXray is not in this build; drop the .aar into app/libs/")
            return false
        }
        return try {
            // libXray's own signature: RunXray(datDir, configPath, maxMemory).
            // maxMemory 0 lets the Go runtime manage its own budget, which
            // is what the upstream Android sample does.
            val method = entry.getMethod(
                "runXray", String::class.java, String::class.java, Long::class.javaPrimitiveType
            )
            method.invoke(null, assetsDir.absolutePath, config.absolutePath, 0L)
            running = true
            true
        } catch (t: Throwable) {
            Log.e(TAG, "could not start Xray", t)
            running = false
            false
        }
    }

    override fun stop() {
        running = false
        runCatching { libXray()?.getMethod("stopXray")?.invoke(null) }
            .onFailure { Log.w(TAG, "stopping Xray failed", it) }
    }

    private fun libXray(): Class<*>? =
        runCatching { Class.forName("libXray.LibXray") }.getOrNull()

    private companion object {
        const val TAG = "XrayRuntime"
    }
}
