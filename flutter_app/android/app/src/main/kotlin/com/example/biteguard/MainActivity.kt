package com.example.biteguard

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Bitmap
import android.os.Build
import androidx.core.content.FileProvider
import java.io.File
import java.io.FileOutputStream
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channelId = "biteguard_access"
    private val alertExtra = "biteguard_alert_id"
    private val cameraRequestCode = 2002
    private var notificationChannel: MethodChannel? = null
    private var pendingPhotoResult: MethodChannel.Result? = null
    private var pendingPhotoFile: File? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        createNotificationChannel()

        notificationChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "biteguard/notifications")
        notificationChannel!!
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
                            alertId = (values["alertId"] as? Number)?.toInt() ?: 0,
                            title = values["title"] as? String ?: "BiteGuard access attempt",
                            body = values["body"] as? String ?: "An access attempt was recorded.",
                            thumbnail = values["thumbnail"] as? ByteArray,
                        )
                        result.success(true)
                    }
                    "consumePendingOpen" -> {
                        result.success(intent?.getIntExtra(alertExtra, -1)?.takeIf { it >= 0 }?.also { intent.removeExtra(alertExtra) })
                    }
                    "capturePhoto" -> capturePhoto(result)
                    else -> result.notImplemented()
                }
            }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val alertId = intent.getIntExtra(alertExtra, -1)
        if (alertId >= 0) notificationChannel?.invokeMethod("openAlert", alertId)
    }

    private fun capturePhoto(result: MethodChannel.Result) {
        if (pendingPhotoResult != null) {
            result.error("camera_busy", "A camera capture is already in progress.", null)
            return
        }
        val file = File.createTempFile("biteguard_face_", ".jpg", cacheDir)
        val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
        val camera = Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE).apply {
            putExtra(android.provider.MediaStore.EXTRA_OUTPUT, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        }
        try {
            pendingPhotoFile = file
            pendingPhotoResult = result
            startActivityForResult(camera, cameraRequestCode)
        } catch (e: Exception) {
            pendingPhotoFile?.delete()
            pendingPhotoFile = null
            pendingPhotoResult = null
            result.error("camera_unavailable", "Could not open the phone camera.", null)
        }
    }

    @Deprecated("Deprecated by Android, retained for the small camera intent bridge")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != cameraRequestCode) return
        val callback = pendingPhotoResult ?: return
        val file = pendingPhotoFile
        pendingPhotoResult = null
        pendingPhotoFile = null
        if (resultCode != RESULT_OK || file == null || !file.isFile || file.length() == 0L) {
            file?.delete()
            callback.error("capture_cancelled", "No photo was captured.", null)
            return
        }
        try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(file.absolutePath, bounds)
            var sample = 1
            while (bounds.outWidth / sample > 1200 || bounds.outHeight / sample > 1200) sample *= 2
            val options = BitmapFactory.Options().apply { inSampleSize = sample }
            val decoded = BitmapFactory.decodeFile(file.absolutePath, options)
                ?: throw IllegalStateException("Camera image could not be decoded.")
            val scale = minOf(1f, 800f / maxOf(decoded.width, decoded.height))
            val compact = if (scale < 1f) Bitmap.createScaledBitmap(decoded, (decoded.width * scale).toInt(), (decoded.height * scale).toInt(), true) else decoded
            FileOutputStream(file, false).use { compact.compress(Bitmap.CompressFormat.JPEG, 76, it) }
            if (compact !== decoded) compact.recycle()
            decoded.recycle()
            callback.success(file.absolutePath)
        } catch (e: Exception) {
            file.delete()
            callback.error("photo_processing_failed", e.message, null)
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
    private fun showAccessNotification(id: Int, alertId: Int, title: String, body: String, thumbnail: ByteArray?) {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, channelId)
        } else {
            Notification.Builder(this).setPriority(Notification.PRIORITY_HIGH)
        }
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(alertExtra, alertId)
        }
        val pendingIntent = PendingIntent.getActivity(
            this, id, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val image = thumbnail?.let { BitmapFactory.decodeByteArray(it, 0, it.size) }
        builder
            .setSmallIcon(R.drawable.ic_biteguard)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setOnlyAlertOnce(true)
        if (image != null) {
            builder.setLargeIcon(image)
            builder.setStyle(Notification.BigPictureStyle().bigPicture(image).setSummaryText(body))
        } else {
            builder.setStyle(Notification.BigTextStyle().bigText(body))
        }
        val notification = builder.build()
        getSystemService(NotificationManager::class.java).notify(id, notification)
    }
}
