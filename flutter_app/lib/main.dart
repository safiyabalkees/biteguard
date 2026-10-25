import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

// Lets requests pass through ngrok's free-tier browser warning page.
const hdr = {'ngrok-skip-browser-warning': 'true'};
const _notificationChannel = MethodChannel('biteguard/notifications');

void main() => runApp(const BiteGuardApp());

class BiteGuardApp extends StatelessWidget {
  const BiteGuardApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'BiteGuard',
        debugShowCheckedModeBanner: false,
        theme: ThemeData.dark(useMaterial3: true).copyWith(
          colorScheme: ColorScheme.fromSeed(
              seedColor: Colors.deepOrange, brightness: Brightness.dark),
        ),
        home: const HomePage(),
      );
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  // The active tunnel lets a physical phone reach the laptop-hosted server.
  String baseUrl = 'https://subsequent-programmers-gather-administrative.trycloudflare.com';
  List<dynamic> alerts = [];
  int lastSeenId = -1;
  bool connected = false;
  bool polling = false;
  Timer? timer;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    final prefs = await SharedPreferences.getInstance();
    baseUrl = prefs.getString('baseUrl') ?? baseUrl;
    await _notificationChannel.invokeMethod<void>('requestPermission');
    timer = Timer.periodic(const Duration(seconds: 2), (_) => _poll());
    _poll();
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> _poll() async {
    if (polling) return;
    polling = true;
    try {
      final r = await http
          .get(Uri.parse('$baseUrl/alerts'), headers: hdr)
          .timeout(const Duration(seconds: 4));
      final list = jsonDecode(r.body) as List<dynamic>;
      final maxId = list.isEmpty ? 0 : list.first['id'] as int;
      if (lastSeenId >= 0 && maxId > lastSeenId && mounted) {
        final fresh = list.where((a) => (a['id'] as int) > lastSeenId).toList();
        for (final a in fresh) {
          _showSystemNotification(a);
          if (a['authorized'] != true) _showAlertBanner(a);
        }
      }
      lastSeenId = maxId;
      if (mounted)
        setState(() {
          alerts = list;
          connected = true;
        });
    } catch (_) {
      if (mounted) setState(() => connected = false);
    } finally {
      polling = false;
    }
  }

  void _showSystemNotification(dynamic alert) {
    _notificationChannel.invokeMethod<bool>('show', {
      'id': alert['id'] as int,
      'title': alert['authorized'] == true
          ? 'Authorized access'
          : 'Unauthorized access',
      'body': 'Person: ${alert['person']}',
    });
  }

  void _showAlertBanner(dynamic a) {
    final m = ScaffoldMessenger.of(context);
    m.clearMaterialBanners();
    m.showMaterialBanner(MaterialBanner(
      backgroundColor: Colors.red.shade900,
      leading: const Icon(Icons.warning_amber_rounded, size: 32),
      content: Text('Unauthorized access\nPerson: ${a['person']}'),
      actions: [
        TextButton(
            onPressed: () {
              m.clearMaterialBanners();
              _openAlert(a);
            },
            child: const Text('VIEW')),
        TextButton(
            onPressed: m.clearMaterialBanners, child: const Text('DISMISS')),
      ],
    ));
  }

  void _openAlert(dynamic a) => Navigator.push(context,
      MaterialPageRoute(builder: (_) => AlertPage(baseUrl: baseUrl, alert: a)));

  Future<void> _settings() async {
    final c = TextEditingController(text: baseUrl);
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Server URL'),
        content: TextField(
            controller: c,
            decoration: const InputDecoration(
                helperText:
                    'Emulator: http://10.0.2.2:5000\nCloudflare: https://your-tunnel.trycloudflare.com',
                helperMaxLines: 3)),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Save')),
        ],
      ),
    );
    if (ok == true) {
      var u = c.text.trim();
      if (u.endsWith('/')) u = u.substring(0, u.length - 1);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('baseUrl', u);
      setState(() {
        baseUrl = u;
        lastSeenId = -1;
      });
      _poll();
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: const Text('🍪 BiteGuard'),
          actions: [
            Icon(Icons.circle,
                size: 12, color: connected ? Colors.green : Colors.red),
            IconButton(icon: const Icon(Icons.settings), onPressed: _settings),
          ],
        ),
        body: alerts.isEmpty
            ? Center(
                child: Text(
                    connected
                        ? 'No access attempts yet.\nYour snacks are safe.'
                        : 'Not connected.\nCheck the server URL in Settings.',
                    textAlign: TextAlign.center))
            : ListView.builder(
                itemCount: alerts.length,
                itemBuilder: (_, i) {
                  final a = alerts[i];
                  final ok = a['authorized'] == true;
                  return Card(
                    margin:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                    child: ListTile(
                      onTap: () => _openAlert(a),
                      leading: a['image'] == null
                          ? const Icon(Icons.no_photography, size: 40)
                          : ClipRRect(
                              borderRadius: BorderRadius.circular(8),
                              child: Image.network(
                                  '$baseUrl/captures/${a['image']}',
                                  headers: hdr,
                                  width: 56,
                                  height: 56,
                                  fit: BoxFit.cover,
                                  errorBuilder: (_, __, ___) =>
                                      const Icon(Icons.broken_image))),
                      title: Text(ok
                          ? 'Authorized: ${a['person']}'
                          : 'Unauthorized: ${a['person']}'),
                      subtitle: Text('${a['time']}'),
                      trailing: const Icon(Icons.chevron_right),
                    ),
                  );
                },
              ),
      );
}

class AlertPage extends StatefulWidget {
  final String baseUrl;
  final dynamic alert;
  const AlertPage({super.key, required this.baseUrl, required this.alert});
  @override
  State<AlertPage> createState() => _AlertPageState();
}

class _AlertPageState extends State<AlertPage> {
  final _rec = AudioRecorder();
  bool recording = false, sending = false;
  String? path;
  String msg = '';

  @override
  void dispose() {
    _rec.dispose();
    super.dispose();
  }

  Future<void> _toggleRecord() async {
    if (recording) {
      final p = await _rec.stop();
      setState(() {
        recording = false;
        path = p;
        msg = 'Recorded. Tap send.';
      });
      return;
    }
    if (!await _rec.hasPermission()) {
      setState(() => msg = 'Microphone permission denied');
      return;
    }
    final dir = await getTemporaryDirectory();
    final p = '${dir.path}/voice_${DateTime.now().millisecondsSinceEpoch}.wav';
    await _rec.start(const RecordConfig(encoder: AudioEncoder.wav), path: p);
    setState(() {
      recording = true;
      msg = 'Recording… tap again to stop';
    });
  }

  Future<void> _send() async {
    if (path == null) return;
    setState(() {
      sending = true;
      msg = 'Sending…';
    });
    try {
      final req =
          http.MultipartRequest('POST', Uri.parse('${widget.baseUrl}/voice'))
            ..headers.addAll(hdr)
            ..fields['alert_id'] = '${widget.alert['id']}'
            ..files.add(await http.MultipartFile.fromPath('audio', path!));
      final res = await req.send();
      setState(() => msg = res.statusCode == 200
          ? 'Voice message sent to the box.'
          : 'Server error ${res.statusCode}');
    } catch (e) {
      setState(() => msg = 'Failed: $e');
    }
    setState(() => sending = false);
  }

  @override
  Widget build(BuildContext context) {
    final a = widget.alert;
    final ok = a['authorized'] == true;
    return Scaffold(
      appBar:
          AppBar(title: Text(ok ? 'Authorized access' : 'Unauthorized access')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        if (a['image'] != null)
          ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Image.network('${widget.baseUrl}/captures/${a['image']}',
                  headers: hdr)),
        const SizedBox(height: 16),
        Text('Person: ${a['person']}',
            style: Theme.of(context).textTheme.titleLarge),
        Text('Time: ${a['time']}'),
        Text('Status: ${ok ? 'Access granted' : 'Access blocked'}'),
        const SizedBox(height: 24),
        FilledButton.icon(
          onPressed: _toggleRecord,
          icon: Icon(recording ? Icons.stop : Icons.mic),
          label: Text(recording ? 'Stop recording' : 'Record voice'),
          style: FilledButton.styleFrom(
              backgroundColor: recording ? Colors.red : null,
              padding: const EdgeInsets.all(16)),
        ),
        const SizedBox(height: 12),
        FilledButton.tonalIcon(
          onPressed: (path != null && !sending && !recording) ? _send : null,
          icon: const Icon(Icons.send),
          label: const Text('Send Voice Message'),
          style: FilledButton.styleFrom(padding: const EdgeInsets.all(16)),
        ),
        const SizedBox(height: 16),
        Center(child: Text(msg)),
      ]),
    );
  }
}
