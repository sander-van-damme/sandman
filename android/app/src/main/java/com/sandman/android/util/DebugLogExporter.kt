package com.sandman.android.util

import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.FileProvider
import java.io.File

private const val TAG = "DebugLogExporter"

object DebugLogExporter {

    fun share(context: Context) {
        try {
            val process = Runtime.getRuntime().exec(arrayOf("logcat", "-d", "-t", "1000"))
            val logs = process.inputStream.bufferedReader().readText()
            val file = File(context.cacheDir, "sandman_debug.log")
            file.writeText(logs)
            val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_STREAM, uri)
                putExtra(Intent.EXTRA_SUBJECT, "Sandman debug logs")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            context.startActivity(
                Intent.createChooser(intent, "Share debug logs")
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            Log.e(TAG, "Failed to export logs", e)
        }
    }
}
