package com.example.biteguard

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channelId = "biteguard_access"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        createNotificationChannel()

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "biteguard/notifications")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "requestPermission" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                        ) {
                            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
                        }
                        result.success(null)
                    }
                    "show" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                        ) {
                            result.success(false)
                            return@setMethodCallHandler
                        }

                        val values = call.arguments as? Map<*, *> ?: emptyMap<Any, Any>()
                        showAccessNotification(
                            id = (values["id"] as? Number)?.toInt() ?: 0,
                            title = values["title"] as? String ?: "BiteGuard access attempt",
                            body = values["body"] as? String ?: "An access attempt was recorded.",
                        )
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                channelId,
                "Access attempts",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply { description = "Alerts when BiteGuard records access to the box." }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    @Suppress("DEPRECATION")
    private fun showAccessNotification(id: Int, title: String, body: String) {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, channelId)
        } else {
            Notification.Builder(this).setPriority(Notification.PRIORITY_HIGH)
        }
        val notification = builder
            .setSmallIcon(R.drawable.ic_biteguard)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(id, notification)
    }
}
