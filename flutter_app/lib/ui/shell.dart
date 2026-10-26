import 'package:flutter/material.dart';

import '../core/app_state.dart';
import 'alert_page.dart';
import 'home_tab.dart';
import 'people_page.dart';
import 'settings_tab.dart';

/// Bottom-navigation shell. Only the visible tab is built, so the camera
/// stream stops the moment you leave Home.
class Shell extends StatefulWidget {
  const Shell({super.key});

  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int _tab = 0;
  AppState? _state;

  static const _titles = ['BiteGuard', 'People', 'Settings'];

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final state = AppScope.of(context);
    if (_state != state) {
      _state?.removeListener(_checkPendingOpen);
      _state = state..addListener(_checkPendingOpen);
    }
  }

  @override
  void dispose() {
    _state?.removeListener(_checkPendingOpen);
    super.dispose();
  }

  /// A tapped notification asks us to open a specific attempt.
  void _checkPendingOpen() {
    final id = _state?.takePendingOpen();
    if (id == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      Navigator.of(context).popUntil((r) => r.isFirst);
      Navigator.push(context, MaterialPageRoute<void>(builder: (_) => AlertPage(alertId: id)));
    });
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final scheme = Theme.of(context).colorScheme;
    final Widget body = switch (_tab) {
      0 => HomeTab(onOpenSettings: () => setState(() => _tab = 2)),
      1 => const PeopleTab(),
      _ => const SettingsTab(),
    };
    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_tab]),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Chip(
              visualDensity: VisualDensity.compact,
              avatar: Icon(Icons.circle, size: 10, color: state.connected ? Colors.green : scheme.error),
              label: Text(state.connected ? 'Connected' : 'Offline'),
            ),
          ),
        ],
      ),
      body: SafeArea(top: false, child: body),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.people_outline), selectedIcon: Icon(Icons.people), label: 'People'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}
