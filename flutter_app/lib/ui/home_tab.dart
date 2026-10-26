import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/models.dart';
import 'alert_page.dart';
import 'live_feed.dart';
import 'theme.dart';
import 'widgets.dart';

class HomeTab extends StatelessWidget {
  const HomeTab({super.key, required this.onOpenSettings});
  final VoidCallback onOpenSettings;

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final api = state.api;

    if (state.loaded && api == null) {
      return EmptyState(
        icon: Icons.cookie_outlined,
        title: 'Connect to your BiteGuard box',
        message: 'Enter the server address (your Cloudflare tunnel URL, or the laptop\'s address on Wi-Fi).',
        action: FilledButton.icon(
          onPressed: onOpenSettings,
          icon: const Icon(Icons.link),
          label: const Text('Set server address'),
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: state.refresh,
      child: CustomScrollView(slivers: [
        SliverPadding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
          sliver: SliverList.list(children: [
            _StatusCard(alerts: state.alerts, connected: state.connected, serverNow: state.serverNow),
            const SizedBox(height: 16),
            if (api != null)
              LiveFeed(
                api: api,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute<void>(builder: (_) => const LivePage()),
                ),
              ),
            const SizedBox(height: 12),
            const BuzzerButton(),
            const SizedBox(height: 24),
            Text('Activity', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
          ]),
        ),
        if (state.alerts.isEmpty)
          const SliverToBoxAdapter(
            child: Padding(
              padding: EdgeInsets.symmetric(vertical: 32, horizontal: 16),
              child: Text('No access attempts yet. Your snacks are safe.', textAlign: TextAlign.center),
            ),
          )
        else
          SliverList.builder(
            itemCount: state.alerts.length > 30 ? 30 : state.alerts.length,
            itemBuilder: (_, i) => AlertTile(state.alerts[i]),
          ),
        const SliverToBoxAdapter(child: SizedBox(height: 24)),
      ]),
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.alerts, required this.connected, required this.serverNow});

  final List<Alert> alerts;
  final bool connected;
  final double serverNow;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final Alert? last = alerts.isEmpty ? null : alerts.first;

    Color color = kOk;
    IconData icon = Icons.shield_outlined;
    String title = 'All quiet';
    String subtitle = 'Box locked · no attempts yet';
    if (!connected) {
      color = scheme.outline;
      icon = Icons.cloud_off;
      title = 'Not connected';
      subtitle = 'Trying to reach the server…';
    } else if (last != null) {
      final ago = timeAgo(last.createdAt, serverNow);
      if (last.processing) {
        color = scheme.primary;
        icon = Icons.hourglass_top;
        title = 'Checking face…';
        subtitle = 'Lid opened $ago';
      } else if (last.authorized) {
        icon = Icons.verified_user;
        title = '${last.person} opened the box';
        subtitle = ago;
      } else {
        color = kBlocked;
        icon = Icons.gpp_bad;
        title = 'Blocked: ${last.person}';
        subtitle = 'Last attempt $ago';
      }
    }

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(children: [
        Icon(icon, color: color, size: 36),
        const SizedBox(width: 14),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
            Text(subtitle, style: TextStyle(color: scheme.onSurfaceVariant)),
          ]),
        ),
      ]),
    );
  }
}
