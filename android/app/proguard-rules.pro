# Sandman proguard rules

# Keep OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# Keep kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.json.** { kotlinx.serialization.KSerializer serializer(...); }
-keep,includedescriptorclasses class com.sandman.android.**$$serializer { *; }
-keepclassmembers class com.sandman.android.** {
    *** Companion;
}
