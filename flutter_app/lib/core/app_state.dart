import 'dart:async';
import 'dart:math';

import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'models.dart';
import 'notifier.dart';

const String kDefaultServerUrl =
    'http://10.0.2.2:5000';
const Set<String> _expiredTunnelUrls = {
  'https://study-baker-integrated-knit.trycloudflare.com',
  'https://thumbzilla-settings-gets-techno.trycloudflare.com',
  'https://conflict-techrepublic-equipped-caused.trycloudflare.com',
};

/// Server connection and alert list for the whole app.
///
/// One long-poll loop keeps the list current: the server answers the moment
/// something changes, so alerts show up (and notify) within a network round
/// trip instead of waiting for a poll timer.
class AppState extends ChangeNotifier {
  String baseUrl = '';
  bool loaded = false;
  bool connected = false;
  List<Alert> alerts = const [];
  BiteGuardApi? api;

  double _serverSkew = 0; // server clock minus phone clock, seconds
  int _rev = 0;
  int _generation = 0;
  bool _synced = false;
  bool _disposed = false;
  int? _pendingOpen;
  final Map<int, String> _notified = {}; // alert id -> status already shown

  /// Server "now" in epoch seconds, corrected for clock differences.
  double get serverNow => DateTime.now().millisecondsSinceEpoch / 1000 + _serverSkew;

  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    final savedUrl = prefs.getString('baseUrl');
    baseUrl = savedUrl == null ||
            savedUrl.isEmpty ||
            _expiredTunnelUrls.contains(savedUrl)
        ? kDefaultServerUrl
        : savedUrl;
    if (savedUrl != baseUrl) await prefs.setString('baseUrl', baseUrl);
    loaded = true;
    Notifier.listenForOpen((id) {
      _pendingOpen = id;
      notifyListeners();
    });
    notifyListeners();
    await Notifier.requestPermission();
    _pendingOpen = await Notifier.consumePendingOpen();
    _restart();
  }

  Future<void> setUrl(String url) async {
    baseUrl = BiteGuardApi.normalizeUrl(url);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('baseUrl', baseUrl);
    _restart();
  }

  Future<void> refresh() async {
    final a = api;
    if (a == null) return;
    try {
      _merge(await a.changes(since: 0, waitSeconds: 0), replace: true);
    } catch (_) {
      if (connected) {
        connected = false;
        notifyListeners();
      }
    }
  }

  /// Alert id the user tapped in a notification, once its data is available.
  int? takePendingOpen() {
    final id = _pendingOpen;
    if (id == null) return null;
    if (alerts.any((a) => a.id == id)) {
      _pendingOpen = null;
      return id;
    }
    if (_synced) _pendingOpen = null; // too old to be in the list
    return null;
  }

  void _restart() {
    _generation++;
    api?.close();
    api = baseUrl.isEmpty ? null : BiteGuardApi(baseUrl);
    alerts = const [];
    connected = false;
    _rev = 0;
    _synced = false;
    _notified.clear();
    notifyListeners();
    final a = api;
    if (a != null) unawaited(_loop(_generation, a));
  }

  Future<void> _loop(int generation, BiteGuardApi a) async {
    var backoff = 1;
    while (!_disposed && generation == _generation) {
      try {
        final delta = await a.changes(since: _rev, waitSeconds: _synced ? 20 : 0);
        if (_disposed || generation != _generation) return;
        backoff = 1;
        _merge(delta);
      } catch (_) {
        if (_disposed || generation != _generation) return;
        if (connected) {
          connected = false;
          notifyListeners();
        }
        await Future<void>.delayed(Duration(seconds: backoff));
        backoff = min(backoff * 2, 8);
      }
    }
  }

  void _merge(AlertDelta delta, {bool replace = false}) {
    _serverSkew = delta.serverNow - DateTime.now().millisecondsSinceEpoch / 1000;
    final firstSync = !_synced;
    _synced = true;
    _rev = max(_rev, delta.rev);
    connected = true;

    final byId = <int, Alert>{if (!replace) for (final a in alerts) a.id: a};
    for (final a in delta.alerts) {
      byId[a.id] = a;
    }
    alerts = (byId.values.toList()..sort((a, b) => b.id.compareTo(a.id))).take(100).toList();

    for (final a in delta.alerts.reversed) {
      final recent = serverNow - a.createdAt < 30;
      if (firstSync && !recent) {
        _notified[a.id] = a.status; // history from before the app opened
        continue;
      }
      if (_notified[a.id] == a.status) continue;
      _notified[a.id] = a.status;
      unawaited(Notifier.showAlert(a, api));
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    api?.close();
    super.dispose();
  }
}

class AppScope extends InheritedNotifier<AppState> {
  const AppScope({super.key, required AppState state, required super.child})
      : super(notifier: state);

  static AppState of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AppScope>()!.notifier!;
}
