import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'mjpeg.dart';
import 'models.dart';

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Talks to the BiteGuard server with one keep-alive `dart:io` client
/// (no per-request connection setup, no extra packages).
class BiteGuardApi {
  BiteGuardApi(String url) : baseUrl = normalizeUrl(url);

  final String baseUrl;
  final HttpClient _client = HttpClient()
    ..connectionTimeout = const Duration(seconds: 6)
    ..idleTimeout = const Duration(seconds: 60);

  static String normalizeUrl(String input) {
    var u = input.trim();
    while (u.endsWith('/')) {
      u = u.substring(0, u.length - 1);
    }
    if (u.isNotEmpty && !u.contains('://')) u = 'http://$u';
    return u;
  }

  String url(String path) => '$baseUrl$path';

  void close() => _client.close(force: true);

  Future<HttpClientRequest> _open(String method, String path, Duration timeout) async {
    final req = await _client.openUrl(method, Uri.parse(url(path))).timeout(timeout);
    return req;
  }

  Future<Uint8List> _readAll(Stream<List<int>> s) async {
    final b = BytesBuilder();
    await for (final c in s) {
      b.add(c);
    }
    return b.takeBytes();
  }

  Future<(int, Uint8List)> _request(
    String method,
    String path, {
    Object? json,
    Duration timeout = const Duration(seconds: 8),
  }) async {
    final req = await _open(method, path, timeout);
    try {
      if (json != null) {
        final body = utf8.encode(jsonEncode(json));
        req.headers.contentType = ContentType.json;
        req.contentLength = body.length;
        req.add(body);
      }
      final res = await req.close().timeout(timeout);
      final bytes = await _readAll(res).timeout(timeout);
      return (res.statusCode, bytes);
    } on TimeoutException {
      req.abort();
      rethrow;
    }
  }

  Future<dynamic> _json(String method, String path, {Object? json, Duration? timeout}) async {
    final (code, bytes) = await _request(method, path,
        json: json, timeout: timeout ?? const Duration(seconds: 8));
    dynamic body;
    try {
      body = bytes.isEmpty ? null : jsonDecode(utf8.decode(bytes));
    } on FormatException {
      body = null;
    }
    if (code < 200 || code >= 300) {
      final msg = body is Map && body['error'] is String ? body['error'] as String : 'HTTP $code';
      throw ApiException(msg);
    }
    return body;
  }

  /// Alert changes since [since]. With [waitSeconds] > 0 the server holds the
  /// request until something changes, so updates arrive immediately and idle
  /// cost is about three requests a minute.
  Future<AlertDelta> changes({required int since, int waitSeconds = 20}) async {
    final j = await _json('GET', '/alerts/changes?since=$since&wait=$waitSeconds',
        timeout: Duration(seconds: waitSeconds + 8)) as Map<String, dynamic>;
    return AlertDelta(
      (j['rev'] as num).toInt(),
      [for (final a in j['alerts'] as List<dynamic>) Alert.fromJson(a as Map<String, dynamic>)],
      (j['now'] as num?)?.toDouble() ?? DateTime.now().millisecondsSinceEpoch / 1000,
    );
  }

  Future<Health> health() async {
    final sw = Stopwatch()..start();
    final j = await _json('GET', '/health', timeout: const Duration(seconds: 6)) as Map<String, dynamic>;
    return Health(
      faceReady: j['face_recognition'] == true,
      faces: (j['faces'] as num?)?.toInt() ?? 0,
      cameraFps: (j['camera_fps'] as num?)?.toDouble() ?? 0,
      cameraAge: (j['camera_age'] as num?)?.toDouble(),
      latencyMs: sw.elapsedMilliseconds,
    );
  }

  Future<Uint8List> getBytes(String path, {Duration timeout = const Duration(seconds: 5)}) async {
    final (code, bytes) = await _request('GET', path, timeout: timeout);
    if (code != 200) throw ApiException('HTTP $code');
    return bytes;
  }

  Future<void> buzz({int durationMs = 350}) async {
    await _json('POST', '/buzzer', json: {'duration_ms': durationMs}, timeout: const Duration(seconds: 5));
  }

  Future<List<Person>> people() async {
    final j = await _json('GET', '/people') as List<dynamic>;
    return [for (final p in j) Person.fromJson(p as Map<String, dynamic>)];
  }

  Future<void> deletePerson(String slug) => _json('DELETE', '/people/$slug');

  /// Uploads 1-5 photos for a new authorized person (multipart/form-data).
  Future<String> enroll(String name, List<File> photos) async {
    final boundary = '----biteguard${DateTime.now().microsecondsSinceEpoch}';
    final parts = <List<int>>[
      utf8.encode('--$boundary\r\nContent-Disposition: form-data; name="name"\r\n\r\n$name\r\n'),
    ];
    for (var i = 0; i < photos.length; i++) {
      parts
        ..add(utf8.encode('--$boundary\r\nContent-Disposition: form-data; name="photos"; '
            'filename="face_${i + 1}.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'))
        ..add(await photos[i].readAsBytes())
        ..add(utf8.encode('\r\n'));
    }
    parts.add(utf8.encode('--$boundary--\r\n'));

    const timeout = Duration(seconds: 40);
    final req = await _open('POST', '/enroll', timeout);
    try {
      req.headers.contentType = ContentType('multipart', 'form-data', parameters: {'boundary': boundary});
      req.contentLength = parts.fold<int>(0, (n, p) => n + p.length);
      parts.forEach(req.add);
      final res = await req.close().timeout(timeout);
      final body = jsonDecode(utf8.decode(await _readAll(res).timeout(timeout))) as Map<String, dynamic>;
      if (res.statusCode != 200) throw ApiException(body['error'] as String? ?? 'Enrollment failed.');
      return body['name'] as String? ?? name;
    } on TimeoutException {
      req.abort();
      throw ApiException('The server took too long to answer.');
    }
  }

  /// A dedicated connection for the live camera (see [CameraStream]).
  CameraStream openCamera() => CameraStream._(this);
}

/// Live camera as a stream of JPEG frames over one long-lived connection.
/// [close] tears the socket down immediately, even while waiting for data
/// (cancelling a stream subscription alone would wait for the next frame).
class CameraStream {
  CameraStream._(this._api);

  final BiteGuardApi _api;
  final HttpClient _client = HttpClient()..connectionTimeout = const Duration(seconds: 6);

  void close() => _client.close(force: true);

  Stream<Uint8List> frames() async* {
    final req = await _client
        .openUrl('GET', Uri.parse(_api.url('/camera/stream')))
        .timeout(const Duration(seconds: 8));
    final res = await req.close().timeout(const Duration(seconds: 10));
    if (res.statusCode != 200) {
      await res.drain<void>();
      throw ApiException('HTTP ${res.statusCode}');
    }
    final parser = MjpegParser();
    // The server sends a heartbeat at least every 10 s, so silence means a dead link.
    await for (final chunk in res.timeout(const Duration(seconds: 20))) {
      for (final frame in parser.add(chunk)) {
        yield frame;
      }
    }
  }
}
