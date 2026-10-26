import 'package:flutter/services.dart';

import 'api.dart';
import 'models.dart';

/// Bridge to the native Android side (notifications and the camera intent).
class Notifier {
  Notifier._();

  static const MethodChannel _channel = MethodChannel('biteguard/notifications');

  static void listenForOpen(void Function(int alertId) onOpen) {
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'openAlert') onOpen(call.arguments as int);
    });
  }

  static Future<void> requestPermission() async {
    try {
      await _channel.invokeMethod<void>('requestPermission');
    } on MissingPluginException {
      // Not running on Android.
    }
  }

  static Future<int?> consumePendingOpen() async {
    try {
      return await _channel.invokeMethod<int>('consumePendingOpen');
    } on MissingPluginException {
      return null;
    }
  }

  static Future<String?> capturePhoto() => _channel.invokeMethod<String>('capturePhoto');

  /// One notification per attempt, updated in place: shown immediately as text,
  /// then re-posted with the (small) thumbnail once it has downloaded.
  static Future<void> showAlert(Alert a, BiteGuardApi? api) async {
    final String title;
    final String body;
    if (a.processing) {
      title = 'Lid opened — checking face…';
      body = 'Tap to see the live camera.';
    } else if (a.authorized) {
      title = 'Authorized: ${a.person}';
      body = 'The box unlocked for 5 seconds.';
    } else {
      title = 'Access blocked: ${a.person}';
      body = 'The box stayed locked. Tap to see who it was.';
    }
    await _show(a.id, title, body, null);
    final thumb = a.thumb;
    if (!a.processing && thumb != null && api != null) {
      try {
        final bytes = await api.getBytes('/captures/$thumb', timeout: const Duration(seconds: 4));
        await _show(a.id, title, body, bytes);
      } catch (_) {
        // The text notification is already visible.
      }
    }
  }

  static Future<void> _show(int id, String title, String body, Uint8List? thumbnail) async {
    try {
      await _channel.invokeMethod<bool>('show', {
        'id': id,
        'alertId': id,
        'title': title,
        'body': body,
        'thumbnail': thumbnail,
      });
    } on MissingPluginException {
      // Not running on Android.
    }
  }
}
