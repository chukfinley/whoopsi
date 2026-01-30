import 'package:flutter/material.dart';

class SportTypes {
  SportTypes._();

  static const Map<int, String> all = {
    -1: 'Activity', 0: 'Running', 1: 'Cycling',
    16: 'Baseball', 17: 'Basketball', 18: 'Rowing', 19: 'Fencing',
    20: 'Field Hockey', 21: 'Football', 22: 'Golf',
    24: 'Ice Hockey', 25: 'Lacrosse', 27: 'Rugby', 28: 'Sailing',
    29: 'Skiing', 30: 'Soccer', 31: 'Softball', 32: 'Squash',
    33: 'Swimming', 34: 'Tennis', 35: 'Track & Field',
    36: 'Volleyball', 37: 'Water Polo', 38: 'Wrestling', 39: 'Boxing',
    42: 'Dance', 43: 'Pilates', 44: 'Yoga', 45: 'Weightlifting',
    47: 'Cross Country Skiing', 48: 'Functional Fitness',
    49: 'Duathlon', 51: 'Gymnastics', 52: 'Hiking/Rucking',
    53: 'Horseback Riding', 55: 'Kayaking', 56: 'Martial Arts',
    57: 'Mountain Biking', 59: 'Powerlifting', 60: 'Rock Climbing',
    61: 'Paddleboarding', 62: 'Triathlon', 63: 'Walking',
    64: 'Surfing', 65: 'Elliptical', 66: 'Stairmaster',
    70: 'Meditation', 71: 'Other', 73: 'Diving',
    82: 'Ultimate', 83: 'Climber', 84: 'Jumping Rope',
    85: 'Australian Football', 86: 'Skateboarding', 87: 'Coaching',
    88: 'Ice Bath', 89: 'Commuting', 90: 'Gaming',
    91: 'Snowboarding', 92: 'Motocross', 93: 'Caddying',
    94: 'Obstacle Course Racing', 95: 'Motor Racing', 96: 'HIIT',
    97: 'Spin', 98: 'Jiu Jitsu', 99: 'Manual Labor',
    100: 'Cricket', 101: 'Pickleball', 102: 'Inline Skating',
    103: 'Box Fitness', 104: 'Spikeball', 105: 'Wheelchair Pushing',
    106: 'Paddle Tennis', 107: 'Barre', 108: 'Stage Performance',
    109: 'High Stress Work', 110: 'Parkour', 111: 'Gaelic Football',
    112: 'Hurling/Camogie', 113: 'Circus Arts',
    121: 'Massage Therapy', 123: 'Strength Trainer',
    125: 'Watching Sports', 126: 'Assault Bike', 127: 'Kickboxing',
    128: 'Stretching', 230: 'Table Tennis', 231: 'Badminton',
    232: 'Netball', 233: 'Sauna', 234: 'Disc Golf',
    235: 'Yard Work', 236: 'Air Compression',
    237: 'Percussive Massage', 238: 'Paintball', 239: 'Ice Skating',
    240: 'Handball', 248: 'F45 Training', 249: 'Padel',
    250: "Barry's", 251: 'Dedicated Parenting',
    252: 'Stroller Walking', 253: 'Stroller Jogging',
    254: 'Toddlerwearing', 255: 'Babywearing', 258: 'Barre3',
    259: 'Hot Yoga', 261: 'Stadium Steps', 262: 'Polo',
    263: 'Musical Performance', 264: 'Kite Boarding',
    266: 'Dog Walking', 267: 'Water Skiing', 268: 'Wakeboarding',
    269: 'Cooking', 270: 'Cleaning', 272: 'Public Speaking',
  };

  static String name(int id) => all[id] ?? 'Activity ($id)';

  static IconData icon(int id) {
    switch (id) {
      case 0: return Icons.directions_run;
      case 1: return Icons.directions_bike;
      case 33: return Icons.pool;
      case 44: return Icons.self_improvement;
      case 45: return Icons.fitness_center;
      case 52: return Icons.terrain;
      case 63: return Icons.directions_walk;
      case 70: return Icons.spa;
      case 42: return Icons.music_note;
      case 30: return Icons.sports_soccer;
      case 17: return Icons.sports_basketball;
      case 34: return Icons.sports_tennis;
      case 22: return Icons.sports_golf;
      case 39: case 127: return Icons.sports_mma;
      case 96: return Icons.timer;
      case 88: case 233: return Icons.ac_unit;
      case 60: return Icons.landscape;
      case 128: return Icons.accessibility_new;
      case 90: return Icons.videogame_asset;
      case 266: return Icons.pets;
      default: return Icons.fitness_center;
    }
  }

  static List<MapEntry<int, String>> search(String query) {
    if (query.isEmpty) {
      // Return popular types first
      const popular = [0, 1, 45, 63, 52, 44, 48, 96, 33, 42, 30, 39, 70, 71];
      final result = <MapEntry<int, String>>[];
      for (final id in popular) {
        if (all.containsKey(id)) result.add(MapEntry(id, all[id]!));
      }
      for (final e in all.entries) {
        if (!popular.contains(e.key) && e.key >= 0) result.add(e);
      }
      return result;
    }
    final lower = query.toLowerCase();
    return all.entries
        .where((e) => e.key >= 0 && e.value.toLowerCase().contains(lower))
        .toList();
  }
}
