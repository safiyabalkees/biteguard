import 'package:flutter/material.dart';

import 'core/app_state.dart';
import 'ui/live_feed.dart' show routeObserver;
import 'ui/shell.dart';
import 'ui/theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const BiteGuardApp());
}

class BiteGuardApp extends StatefulWidget {
  const BiteGuardApp({super.key});

  @override
  State<BiteGuardApp> createState() => _BiteGuardAppState();
}

class _BiteGuardAppState extends State<BiteGuardApp> {
  final AppState _state = AppState();

  @override
  void initState() {
    super.initState();
    _state.init();
  }

  @override
  void dispose() {
    _state.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AppScope(
        state: _state,
        child: MaterialApp(
          title: 'BiteGuard',
          debugShowCheckedModeBanner: false,
          theme: buildTheme(Brightness.light),
          darkTheme: buildTheme(Brightness.dark),
          navigatorObservers: [routeObserver],
          home: const Shell(),
        ),
      );
}
