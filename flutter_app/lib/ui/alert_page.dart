import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/models.dart';
import 'live_feed.dart';
import 'theme.dart';
import 'widgets.dart';

/// Details of one access attempt: the snapshot, the verdict, and the camera
/// right now. Updates in place as the server finishes identifying the face.
class AlertPage extends StatelessWidget {
  const AlertPage({super.key, required this.alertId});
  final int alertId;

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final api = state.api;
    final Alert? alert = state.alerts.where((a) => a.id == alertId).firstOrNull;
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('Access attempt')),
      body: alert == null || api == null
          ? const EmptyState(icon: Icons.history, title: 'This attempt is no longer available')
          : ListView(padding: const EdgeInsets.fromLTRB(16, 0, 16, 24), children: [
              _Verdict(alert: alert, serverNow: state.serverNow),
              const SizedBox(height: 16),
              if (alert.image != null)
                GestureDetector(
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                      builder: (_) => PhotoPage(title: alert.person, url: api.url('/captures/${alert.image}')),
                    ),
                  ),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(20),
                    child: AspectRatio(
                      aspectRatio: 4 / 3,
                      child: AuthImage(api.url('/captures/${alert.image}'), cacheWidth: 900),
                    ),
                  ),
                )
              else
                Container(
                  height: 140,
                  decoration: BoxDecoration(
                    color: scheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Center(
                    child: Text(alert.processing ? 'Capturing photo…' : 'No photo for this attempt'),
                  ),
                ),
              const SizedBox(height: 24),
              Text('Camera now', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              LiveFeed(
                api: api,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute<void>(builder: (_) => const LivePage()),
                ),
              ),
              const SizedBox(height: 12),
              const BuzzerButton(),
            ]),
    );
  }
}

class _Verdict extends StatelessWidget {
  const _Verdict({required this.alert, required this.serverNow});
  final Alert alert;
  final double serverNow;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = alert.processing ? scheme.primary : (alert.authorized ? kOk : kBlocked);
    final icon = alert.processing
        ? Icons.hourglass_top
        : (alert.authorized ? Icons.verified_user : Icons.gpp_bad);
    final headline = alert.processing
        ? 'Checking face…'
        : (alert.authorized ? 'Authorized' : 'Access blocked');
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(children: [
        Icon(icon, color: color, size: 40),
        const SizedBox(width: 14),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(headline, style: Theme.of(context).textTheme.titleLarge?.copyWith(color: color, fontWeight: FontWeight.w700)),
            const SizedBox(height: 2),
            Text(alert.person, style: Theme.of(context).textTheme.bodyLarge),
            const SizedBox(height: 2),
            Text('${alert.time} · ${timeAgo(alert.createdAt, serverNow)}',
                style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 13)),
          ]),
        ),
      ]),
    );
  }
}

class PhotoPage extends StatelessWidget {
  const PhotoPage({super.key, required this.title, required this.url});
  final String title;
  final String url;

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: Colors.black,
        appBar: AppBar(
          title: Text(title),
          backgroundColor: Colors.black,
          foregroundColor: Colors.white,
        ),
        body: Center(
          child: InteractiveViewer(
            minScale: 0.8,
            maxScale: 5,
            child: AuthImage(url, fit: BoxFit.contain, cacheWidth: 1400),
          ),
        ),
      );
}

/// Full-width live camera.
class LivePage extends StatelessWidget {
  const LivePage({super.key});

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: const Text('Live camera'),
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
      ),
      body: api == null
          ? const SizedBox.shrink()
          : SafeArea(
              child: Column(children: [
                Expanded(child: Center(child: LiveFeed(api: api))),
                const Padding(padding: EdgeInsets.all(16), child: BuzzerButton()),
              ]),
            ),
    );
  }
}
