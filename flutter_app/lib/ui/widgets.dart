import 'package:flutter/material.dart';

import '../core/api.dart';
import '../core/app_state.dart';
import '../core/models.dart';
import 'alert_page.dart';
import 'theme.dart';

/// "just now", "5 min ago", "2 h ago", "3 d ago" using the server clock.
String timeAgo(double createdAt, double serverNow) {
  final s = (serverNow - createdAt).round();
  if (s < 10) return 'just now';
  if (s < 60) return '$s s ago';
  if (s < 3600) return '${s ~/ 60} min ago';
  if (s < 86400) return '${s ~/ 3600} h ago';
  return '${s ~/ 86400} d ago';
}

String alertTitle(Alert a) {
  if (a.processing) return 'Checking face…';
  return a.authorized ? a.person : 'Blocked · ${a.person}';
}

/// Network image that sends the tunnel header and degrades quietly.
class AuthImage extends StatelessWidget {
  const AuthImage(this.url, {super.key, this.width, this.height, this.cacheWidth, this.fit = BoxFit.cover});

  final String url;
  final double? width;
  final double? height;
  final int? cacheWidth;
  final BoxFit fit;

  @override
  Widget build(BuildContext context) => Image.network(
        url,
        headers: kApiHeaders,
        width: width,
        height: height,
        cacheWidth: cacheWidth,
        fit: fit,
        gaplessPlayback: true,
        errorBuilder: (_, __, ___) => Container(
          width: width,
          height: height,
          color: Theme.of(context).colorScheme.surfaceContainerHighest,
          child: const Icon(Icons.image_not_supported_outlined),
        ),
      );
}

class StatusPill extends StatelessWidget {
  const StatusPill(this.alert, {super.key});
  final Alert alert;

  @override
  Widget build(BuildContext context) {
    final (label, color, icon) = alert.processing
        ? ('Checking', Theme.of(context).colorScheme.primary, Icons.hourglass_top)
        : alert.authorized
            ? ('Authorized', kOk, Icons.lock_open)
            : ('Blocked', kBlocked, Icons.lock);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 13, color: color),
        const SizedBox(width: 4),
        Text(label, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
      ]),
    );
  }
}

class AlertTile extends StatelessWidget {
  const AlertTile(this.alert, {super.key});
  final Alert alert;

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final api = state.api;
    final img = alert.thumb ?? alert.image;
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
      onTap: () => Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => AlertPage(alertId: alert.id)),
      ),
      leading: ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: SizedBox(
          width: 56,
          height: 56,
          child: img == null || api == null
              ? ColoredBox(
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                  child: Icon(alert.processing ? Icons.hourglass_top : Icons.no_photography_outlined),
                )
              : AuthImage(api.url('/captures/$img'), width: 56, height: 56, cacheWidth: 168),
        ),
      ),
      title: Text(alertTitle(alert), maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(children: [
          StatusPill(alert),
          const SizedBox(width: 8),
          Flexible(
            child: Text(timeAgo(alert.createdAt, state.serverNow),
                maxLines: 1, overflow: TextOverflow.ellipsis),
          ),
        ]),
      ),
      trailing: const Icon(Icons.chevron_right),
    );
  }
}

class BuzzerButton extends StatefulWidget {
  const BuzzerButton({super.key, this.expanded = true});
  final bool expanded;

  @override
  State<BuzzerButton> createState() => _BuzzerButtonState();
}

class _BuzzerButtonState extends State<BuzzerButton> {
  bool _busy = false;

  Future<void> _press() async {
    final api = AppScope.of(context).api;
    if (api == null) return;
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      await api.buzz();
      messenger.showSnackBar(const SnackBar(content: Text('Buzzer triggered')));
    } catch (_) {
      messenger.showSnackBar(const SnackBar(content: Text('Could not reach the server')));
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final button = FilledButton.tonalIcon(
      onPressed: _busy ? null : _press,
      icon: const Icon(Icons.volume_up),
      label: const Text('Sound the buzzer'),
    );
    return widget.expanded ? SizedBox(width: double.infinity, child: button) : button;
  }
}

class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.title, this.message, this.action});

  final IconData icon;
  final String title;
  final String? message;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 56, color: scheme.outline),
          const SizedBox(height: 12),
          Text(title, style: Theme.of(context).textTheme.titleMedium, textAlign: TextAlign.center),
          if (message != null) ...[
            const SizedBox(height: 6),
            Text(message!,
                textAlign: TextAlign.center,
                style: TextStyle(color: scheme.onSurfaceVariant)),
          ],
          if (action != null) ...[const SizedBox(height: 16), action!],
        ]),
      ),
    );
  }
}
