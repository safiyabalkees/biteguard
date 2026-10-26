import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/api.dart';
import '../core/app_state.dart';
import '../core/models.dart';
import '../core/notifier.dart';
import 'widgets.dart';

/// Who the box recognises: list, add, remove.
class PeopleTab extends StatefulWidget {
  const PeopleTab({super.key});

  @override
  State<PeopleTab> createState() => _PeopleTabState();
}

class _PeopleTabState extends State<PeopleTab> {
  List<Person>? _people;
  String? _error;
  BiteGuardApi? _loadedFor;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final api = AppScope.of(context).api;
    if (api != _loadedFor) {
      _loadedFor = api;
      _load();
    }
  }

  Future<void> _load() async {
    final api = _loadedFor;
    if (api == null) return;
    try {
      final list = await api.people();
      if (mounted && api == _loadedFor) {
        setState(() {
          _people = list;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not load people.');
    }
  }

  Future<void> _add() async {
    final added = await Navigator.push<bool>(
      context,
      MaterialPageRoute<bool>(builder: (_) => const EnrollPage()),
    );
    if (added == true) _load();
  }

  Future<void> _delete(Person p) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Remove ${p.name}?'),
        content: const Text('The box will no longer recognise this person.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Remove')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await _loadedFor?.deletePerson(p.slug);
      _load();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final Widget body;
    if (_loadedFor == null) {
      body = const EmptyState(icon: Icons.link_off, title: 'Not connected', message: 'Set the server address in Settings.');
    } else if (_error != null && _people == null) {
      body = EmptyState(
        icon: Icons.cloud_off,
        title: _error!,
        action: OutlinedButton(onPressed: _load, child: const Text('Retry')),
      );
    } else if (_people == null) {
      body = const Center(child: CircularProgressIndicator());
    } else if (_people!.isEmpty) {
      body = const EmptyState(
        icon: Icons.face_outlined,
        title: 'Nobody enrolled yet',
        message: 'Add the people who are allowed to open the box.',
      );
    } else {
      body = RefreshIndicator(
        onRefresh: _load,
        child: ListView.builder(
          padding: const EdgeInsets.only(bottom: 96),
          itemCount: _people!.length,
          itemBuilder: (_, i) {
            final p = _people![i];
            return ListTile(
              leading: CircleAvatar(
                backgroundColor: scheme.primaryContainer,
                foregroundColor: scheme.onPrimaryContainer,
                child: Text(p.name.isEmpty ? '?' : p.name[0].toUpperCase()),
              ),
              title: Text(p.name),
              subtitle: Text('${p.photos} photo${p.photos == 1 ? '' : 's'} · ${p.authorized ? 'allowed' : 'known, not allowed'}'),
              trailing: p.authorized
                  ? IconButton(
                      tooltip: 'Remove',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () => _delete(p),
                    )
                  : null,
            );
          },
        ),
      );
    }

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: body,
      floatingActionButton: _loadedFor == null
          ? null
          : FloatingActionButton.extended(
              onPressed: _add,
              icon: const Icon(Icons.person_add),
              label: const Text('Add person'),
            ),
    );
  }
}

/// Capture up to five photos with the phone camera and upload them.
class EnrollPage extends StatefulWidget {
  const EnrollPage({super.key});

  @override
  State<EnrollPage> createState() => _EnrollPageState();
}

class _EnrollPageState extends State<EnrollPage> {
  static const _angles = ['Straight on', 'Turn left', 'Turn right', 'Tilt up', 'Tilt down'];
  final _name = TextEditingController();
  final List<String?> _photos = List<String?>.filled(5, null);
  bool _saving = false;
  String _message = '';

  int get _count => _photos.where((p) => p != null).length;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _capture(int slot) async {
    try {
      final path = await Notifier.capturePhoto();
      if (path != null && mounted) {
        setState(() {
          _photos[slot] = path;
          _message = '';
        });
      }
    } on PlatformException catch (e) {
      if (mounted && e.code != 'capture_cancelled') {
        setState(() => _message = e.message ?? 'Could not capture a photo.');
      }
    }
  }

  Future<void> _submit() async {
    final api = AppScope.of(context).api;
    final name = _name.text.trim();
    final paths = _photos.whereType<String>().toList();
    if (api == null) return;
    if (name.isEmpty || paths.isEmpty) {
      setState(() => _message = 'Enter a name and take at least one photo.');
      return;
    }
    setState(() {
      _saving = true;
      _message = '';
    });
    try {
      await api.enroll(name, [for (final p in paths) File(p)]);
      for (final p in paths) {
        try {
          await File(p).delete();
        } catch (_) {}
      }
      if (mounted) Navigator.pop(context, true);
      return;
    } on ApiException catch (e) {
      _message = e.message;
    } catch (_) {
      _message = 'Could not reach the BiteGuard server.';
    }
    if (mounted) setState(() => _saving = false);
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Add person')),
      body: ListView(padding: const EdgeInsets.fromLTRB(16, 0, 16, 24), children: [
        TextField(
          controller: _name,
          textCapitalization: TextCapitalization.words,
          decoration: const InputDecoration(labelText: 'Name'),
        ),
        const SizedBox(height: 20),
        Text('Photos ($_count/5)', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 4),
        Text('One clear face per photo. Different angles help the match.',
            style: TextStyle(color: scheme.onSurfaceVariant)),
        const SizedBox(height: 12),
        Wrap(spacing: 10, runSpacing: 10, children: [
          for (var i = 0; i < 5; i++) _slot(i, scheme),
        ]),
        const SizedBox(height: 24),
        FilledButton.icon(
          onPressed: _saving ? null : _submit,
          icon: _saving
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.check),
          label: Text(_saving ? 'Saving…' : 'Save'),
        ),
        if (_message.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(_message, style: TextStyle(color: scheme.error)),
          ),
      ]),
    );
  }

  Widget _slot(int i, ColorScheme scheme) {
    final path = _photos[i];
    return SizedBox(
      width: 104,
      child: Column(children: [
        InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: _saving ? null : () => _capture(i),
          child: Container(
            width: 104,
            height: 104,
            decoration: BoxDecoration(
              color: scheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(16),
            ),
            clipBehavior: Clip.antiAlias,
            child: path == null
                ? Icon(Icons.add_a_photo_outlined, color: scheme.onSurfaceVariant)
                : Image.file(File(path), fit: BoxFit.cover, cacheWidth: 312),
          ),
        ),
        const SizedBox(height: 4),
        Text(_angles[i], style: const TextStyle(fontSize: 12)),
      ]),
    );
  }
}
