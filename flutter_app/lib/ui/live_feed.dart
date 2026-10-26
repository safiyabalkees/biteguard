import 'dart:async';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../core/api.dart';

/// Lets [LiveFeed] pause while another page covers it (registered in MaterialApp).
final RouteObserver<ModalRoute<void>> routeObserver = RouteObserver<ModalRoute<void>>();

enum _Link { connecting, live, offline }

/// Live camera view. One long-lived MJPEG connection; frames are painted as
/// they arrive. It pauses itself when the app is backgrounded or another page
/// covers it, so it never streams unseen.
class LiveFeed extends StatefulWidget {
  const LiveFeed({super.key, required this.api, this.aspectRatio = 4 / 3, this.onTap});

  final BiteGuardApi api;
  final double aspectRatio;
  final VoidCallback? onTap;

  @override
  State<LiveFeed> createState() => _LiveFeedState();
}

class _LiveFeedState extends State<LiveFeed> with WidgetsBindingObserver, RouteAware {
  final ValueNotifier<Uint8List?> _frame = ValueNotifier<Uint8List?>(null);
  CameraStream? _stream;
  Completer<void>? _wake;
  Timer? _ticker;
  int _session = 0;
  bool _routeVisible = true;
  bool _appVisible = true;
  _Link _link = _Link.connecting;
  DateTime _lastFrame = DateTime.fromMillisecondsSinceEpoch(0);
  int _framesInWindow = 0;
  DateTime _windowStart = DateTime.now();
  double _fps = 0;
  ModalRoute<void>? _route;

  bool get _shouldRun => _routeVisible && _appVisible;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) => _tick());
    _sync();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final route = ModalRoute.of(context);
    if (route != _route) {
      if (_route != null) routeObserver.unsubscribe(this);
      _route = route;
      if (route != null) routeObserver.subscribe(this, route);
    }
  }

  @override
  void didUpdateWidget(LiveFeed old) {
    super.didUpdateWidget(old);
    if (old.api != widget.api) {
      _stop();
      _frame.value = null;
      _sync();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _appVisible = state == AppLifecycleState.resumed;
    _sync();
  }

  @override
  void didPushNext() {
    _routeVisible = false;
    _sync();
  }

  @override
  void didPopNext() {
    _routeVisible = true;
    _sync();
  }

  void _sync() => _shouldRun ? _start() : _stop();

  void _start() {
    if (_stream != null) return;
    unawaited(_run(++_session));
  }

  void _stop() {
    _session++;
    _stream?.close();
    _stream = null;
    _wake?.complete();
    _wake = null;
  }

  Future<void> _run(int session) async {
    var backoff = 1;
    while (mounted && session == _session) {
      if (_link != _Link.live && mounted) setState(() => _link = _Link.connecting);
      final stream = widget.api.openCamera();
      final ended = Completer<void>();
      _stream = stream;
      _wake = ended;
      var gotFrame = false;
      stream.frames().listen(
        (f) {
          if (session != _session) return;
          gotFrame = true;
          _onFrame(f);
        },
        onError: (Object _) {
          if (!ended.isCompleted) ended.complete();
        },
        onDone: () {
          if (!ended.isCompleted) ended.complete();
        },
        cancelOnError: true,
      );
      await ended.future;
      stream.close();
      if (session != _session || !mounted) return;
      _stream = null;
      setState(() => _link = _Link.offline);
      if (gotFrame) backoff = 1;
      await Future<void>.delayed(Duration(seconds: backoff));
      backoff = min(backoff * 2, 6);
      if (session != _session) return;
    }
  }

  void _onFrame(Uint8List f) {
    _lastFrame = DateTime.now();
    _framesInWindow++;
    _frame.value = f;
    if (_link != _Link.live && mounted) setState(() => _link = _Link.live);
  }

  void _tick() {
    if (!mounted) return;
    final now = DateTime.now();
    final secs = now.difference(_windowStart).inMilliseconds / 1000;
    final fps = _framesInWindow / (secs <= 0 ? 1 : secs);
    _framesInWindow = 0;
    _windowStart = now;
    final stalled = _link == _Link.live && now.difference(_lastFrame).inSeconds >= 4;
    if (stalled || (fps - _fps).abs() >= 0.5) {
      setState(() {
        _fps = fps;
        if (stalled) _link = _Link.offline;
      });
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    routeObserver.unsubscribe(this);
    _ticker?.cancel();
    _stop();
    _frame.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return AspectRatio(
      aspectRatio: widget.aspectRatio,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(20),
        child: Stack(fit: StackFit.expand, children: [
          const ColoredBox(color: Colors.black),
          RepaintBoundary(
            child: ValueListenableBuilder<Uint8List?>(
              valueListenable: _frame,
              builder: (_, bytes, __) => bytes == null
                  ? const SizedBox.shrink()
                  : Opacity(
                      opacity: _link == _Link.offline ? 0.35 : 1,
                      child: Image.memory(
                        bytes,
                        fit: BoxFit.cover,
                        gaplessPlayback: true,
                        filterQuality: FilterQuality.low,
                        errorBuilder: (_, __, ___) => const SizedBox.shrink(),
                      ),
                    ),
            ),
          ),
          if (_link != _Link.live)
            Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                if (_link == _Link.connecting)
                  const SizedBox(width: 28, height: 28, child: CircularProgressIndicator(strokeWidth: 3))
                else
                  const Icon(Icons.videocam_off, color: Colors.white70, size: 36),
                const SizedBox(height: 10),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: Text(
                    _link == _Link.connecting
                        ? 'Connecting to camera…'
                        : 'Camera offline.\nOpen /camera on the laptop and press Start.',
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: Colors.white70),
                  ),
                ),
              ]),
            ),
          if (_link == _Link.live)
            Positioned(
              left: 12,
              top: 12,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.black54,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(Icons.circle, size: 9, color: scheme.error),
                  const SizedBox(width: 6),
                  Text('LIVE · ${_fps.round()} fps',
                      style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600)),
                ]),
              ),
            ),
          if (widget.onTap != null)
            Material(
              type: MaterialType.transparency,
              child: InkWell(onTap: widget.onTap),
            ),
        ]),
      ),
    );
  }
}
