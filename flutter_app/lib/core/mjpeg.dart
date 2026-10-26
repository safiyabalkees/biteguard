import 'dart:convert';
import 'dart:typed_data';

/// Incremental parser for the server's `multipart/x-mixed-replace` camera
/// stream. Every part carries a Content-Length, so no JPEG marker scanning is
/// needed. Zero-length parts are heartbeats and produce no frame.
class MjpegParser {
  MjpegParser({this.maxFrameBytes = 4 * 1024 * 1024});

  final int maxFrameBytes;
  Uint8List _buf = Uint8List(64 * 1024);
  int _start = 0;
  int _end = 0;
  int? _need;

  /// Feed a network chunk; returns every complete frame it finished.
  List<Uint8List> add(List<int> chunk) {
    _reserve(chunk.length);
    _buf.setRange(_end, _end + chunk.length, chunk);
    _end += chunk.length;

    final frames = <Uint8List>[];
    while (true) {
      if (_need == null) {
        final h = _headerEnd();
        if (h < 0) {
          if (_end - _start > 16 * 1024) _reset(); // garbage, resync
          break;
        }
        final header = latin1.decode(Uint8List.sublistView(_buf, _start, h));
        final m = RegExp(r'content-length:\s*(\d+)', caseSensitive: false)
            .firstMatch(header);
        _start = h + 4;
        if (m == null) continue; // preamble / boundary only
        final n = int.parse(m.group(1)!);
        if (n > maxFrameBytes) {
          _reset();
          break;
        }
        _need = n;
      }
      final need = _need!;
      if (_end - _start < need) break;
      if (need > 0) {
        frames.add(Uint8List.fromList(Uint8List.sublistView(_buf, _start, _start + need)));
      }
      _start += need;
      _need = null;
    }
    _compact();
    return frames;
  }

  int _headerEnd() {
    for (var i = _start; i + 3 < _end; i++) {
      if (_buf[i] == 13 && _buf[i + 1] == 10 && _buf[i + 2] == 13 && _buf[i + 3] == 10) {
        return i;
      }
    }
    return -1;
  }

  void _reserve(int extra) {
    if (_end + extra <= _buf.length) return;
    final live = _end - _start;
    if (live + extra <= _buf.length) {
      _buf.setRange(0, live, _buf, _start);
    } else {
      var size = _buf.length;
      while (size < live + extra) {
        size *= 2;
      }
      final n = Uint8List(size)..setRange(0, live, _buf, _start);
      _buf = n;
    }
    _start = 0;
    _end = live;
  }

  void _compact() {
    if (_start == _end) {
      _start = _end = 0;
    }
  }

  void _reset() {
    _start = _end = 0;
    _need = null;
  }
}
