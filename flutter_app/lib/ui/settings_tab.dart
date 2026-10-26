import 'package:flutter/material.dart';

import '../core/api.dart';
import '../core/app_state.dart';
import '../core/models.dart';
import 'theme.dart';

class SettingsTab extends StatefulWidget {
  const SettingsTab({super.key});

  @override
  State<SettingsTab> createState() => _SettingsTabState();
}

class _SettingsTabState extends State<SettingsTab> {
  final _url = TextEditingController();
  bool _seeded = false;
  bool _testing = false;
  Health? _health;
  String? _error;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final state = AppScope.of(context);
    if (!_seeded && state.loaded) {
      _seeded = true;
      _url.text = state.baseUrl;
    }
  }

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final raw = _url.text.trim();
    if (raw.isNotEmpty && Uri.tryParse(BiteGuardApi.normalizeUrl(raw))?.host.isNotEmpty != true) {
      setState(() => _error = 'That doesn\'t look like a valid address.');
      return;
    }
    await AppScope.of(context).setUrl(raw);
    if (!mounted) return;
    _url.text = AppScope.of(context).baseUrl;
    FocusScope.of(context).unfocus();
    setState(() {
      _error = null;
      _health = null;
    });
    await _test();
  }

  Future<void> _test() async {
    final api = AppScope.of(context).api;
    if (api == null) return;
    setState(() {
      _testing = true;
      _error = null;
    });
    try {
      final h = await api.health();
      if (mounted) setState(() => _health = h);
    } catch (e) {
      if (mounted) {
        setState(() {
          _health = null;
          _error = 'Could not reach the server. Check the address and that it is running.';
        });
      }
    }
    if (mounted) setState(() => _testing = false);
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final scheme = Theme.of(context).colorScheme;
    final h = _health;
    return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 24), children: [
      Text('Server', style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 8),
      TextField(
        controller: _url,
        keyboardType: TextInputType.url,
        autocorrect: false,
        onSubmitted: (_) => _save(),
        decoration: const InputDecoration(
          labelText: 'Server address',
          hintText: 'https://….trycloudflare.com',
          helperText: 'Emulator: http://10.0.2.2:5000 · Same Wi-Fi: http://<laptop-IP>:5000',
          helperMaxLines: 2,
        ),
      ),
      const SizedBox(height: 12),
      Row(children: [
        Expanded(
          child: FilledButton.icon(
            onPressed: _save,
            icon: const Icon(Icons.save),
            label: const Text('Save & test'),
          ),
        ),
        const SizedBox(width: 8),
        OutlinedButton(
          onPressed: _testing || state.api == null ? null : _test,
          child: _testing
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : const Text('Test'),
        ),
      ]),
      if (_error != null)
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Text(_error!, style: TextStyle(color: scheme.error)),
        ),
      if (h != null) ...[
        const SizedBox(height: 16),
        _check(Icons.cloud_done, kOk, 'Server reachable', '${h.latencyMs} ms round trip'),
        _check(
          h.faceReady ? Icons.face : Icons.hourglass_top,
          h.faceReady ? kOk : scheme.primary,
          h.faceReady ? 'Face recognition ready' : 'Face recognition starting…',
          '${h.faces} enrolled photo sample${h.faces == 1 ? '' : 's'}',
        ),
        _check(
          h.cameraLive ? Icons.videocam : Icons.videocam_off,
          h.cameraLive ? kOk : scheme.outline,
          h.cameraLive ? 'Camera live' : 'Camera offline',
          h.cameraLive ? '${h.cameraFps.round()} fps from the laptop' : 'Open /camera on the laptop and press Start',
        ),
      ],
      const SizedBox(height: 24),
      Text(
        'The tunnel address changes every time cloudflared restarts. Paste the new one here '
        'and into the Wokwi sketch.',
        style: TextStyle(color: scheme.onSurfaceVariant),
      ),
    ]);
  }

  Widget _check(IconData icon, Color color, String title, String subtitle) => ListTile(
        contentPadding: EdgeInsets.zero,
        leading: Icon(icon, color: color),
        title: Text(title),
        subtitle: Text(subtitle),
      );
}
