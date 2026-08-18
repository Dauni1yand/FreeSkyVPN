# kotlinx.serialization generates serializers reflectively from @Serializable
# classes; R8 cannot see those references and would strip them, which shows
# up as a SerializationException at the first API call in a release build and
# never in debug.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class ru.freeskyvpn.core.** {
    *** Companion;
}
-keepclasseswithmembers class ru.freeskyvpn.core.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# libXray and the tun2socks bridge are reached over JNI and reflection, so
# nothing in the bytecode references them by name.
-keep class libXray.** { *; }
-keep class ru.freeskyvpn.vpn.Tun2Socks { *; }

# OkHttp ships references to optional platform classes it guards at runtime.
-dontwarn okhttp3.**
-dontwarn okio.**
