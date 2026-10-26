/// Plain data classes shared by the API layer and the UI (no Flutter imports).
class Alert {
  const Alert({
    required this.id,
    required this.rev,
    required this.createdAt,
    required this.time,
    required this.person,
    required this.status,
    required this.authorized,
    this.image,
    this.thumb,
  });

  final int id;
  final int rev;
  final double createdAt; // server epoch seconds
  final String time;
  final String person;
  final String status; // processing | ok | unknown | no_face | no_image | engine_not_ready
  final bool authorized;
  final String? image;
  final String? thumb;

  bool get processing => status == 'processing';

  factory Alert.fromJson(Map<String, dynamic> j) => Alert(
        id: (j['id'] as num).toInt(),
        rev: (j['rev'] as num?)?.toInt() ?? 0,
        createdAt: (j['created_at'] as num?)?.toDouble() ?? 0,
        time: j['time'] as String? ?? '',
        person: j['person'] as String? ?? 'Unknown',
        status: j['status'] as String? ?? 'unknown',
        authorized: j['authorized'] == true,
        image: j['image'] as String?,
        thumb: j['thumb'] as String?,
      );
}

class AlertDelta {
  const AlertDelta(this.rev, this.alerts, this.serverNow);
  final int rev;
  final List<Alert> alerts;
  final double serverNow;
}

class Person {
  const Person({
    required this.slug,
    required this.name,
    required this.photos,
    required this.authorized,
  });

  final String slug;
  final String name;
  final int photos;
  final bool authorized;

  factory Person.fromJson(Map<String, dynamic> j) => Person(
        slug: j['slug'] as String? ?? '',
        name: j['name'] as String? ?? '',
        photos: (j['photos'] as num?)?.toInt() ?? 0,
        authorized: j['authorized'] == true,
      );
}

class Health {
  const Health({
    required this.faceReady,
    required this.faces,
    required this.cameraFps,
    required this.cameraAge,
    required this.latencyMs,
  });

  final bool faceReady;
  final int faces;
  final double cameraFps;
  final double? cameraAge; // seconds since the last camera frame, null = never
  final int latencyMs;

  bool get cameraLive => cameraAge != null && cameraAge! < 5;
}
