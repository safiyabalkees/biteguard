import 'dart:typed_data';

import 'package:biteguard/core/mjpeg.dart';
import 'package:biteguard/core/models.dart';
import 'package:flutter_test/flutter_test.dart';

Uint8List _jpeg(int n) => Uint8List.fromList([0xFF, 0xD8, ...List.filled(n, 7), 0xFF, 0xD9]);

Uint8List _part(Uint8List frame) => Uint8List.fromList([
      ...'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n'.codeUnits,
      ...frame,
      ...'\r\n'.codeUnits,
    ]);

void main() {
  test('MJPEG parser reassembles frames split across arbitrary chunks', () {
    final a = _jpeg(5000), b = _jpeg(123);
    final stream = Uint8List.fromList([
      ..._part(a),
      ...'--frame\r\nContent-Length: 0\r\n\r\n\r\n'.codeUnits, // heartbeat
      ..._part(b),
    ]);
    final parser = MjpegParser();
    final out = <Uint8List>[];
    for (var i = 0; i < stream.length; i += 77) {
      out.addAll(parser.add(stream.sublist(i, i + 77 > stream.length ? stream.length : i + 77)));
    }
    expect(out.length, equals(2));
    expect(out[0], equals(a));
    expect(out[1], equals(b));
  });

  test('Alert.fromJson tolerates missing optional fields', () {
    final alert = Alert.fromJson({'id': 3, 'status': 'processing', 'person': 'Identifying…'});
    expect(alert.id, equals(3));
    expect(alert.processing, isTrue);
    expect(alert.authorized, isFalse);
    expect(alert.thumb, isNull);
  });
}
