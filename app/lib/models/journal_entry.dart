import 'package:flutter/material.dart';

class JournalEntry {
  final String date; // yyyy-MM-dd
  final Set<String> behaviors;
  final int stressLevel; // 1-5
  final String notes;
  final DateTime createdAt;

  JournalEntry({
    required this.date,
    this.behaviors = const {},
    this.stressLevel = 3,
    this.notes = '',
    DateTime? createdAt,
  }) : createdAt = createdAt ?? DateTime.now();

  Map<String, dynamic> toJson() => {
        'date': date,
        'behaviors': behaviors.toList(),
        'stressLevel': stressLevel,
        'notes': notes,
        'createdAt': createdAt.toIso8601String(),
      };

  factory JournalEntry.fromJson(Map<String, dynamic> json) => JournalEntry(
        date: json['date'] as String,
        behaviors: Set<String>.from(json['behaviors'] as List? ?? []),
        stressLevel: json['stressLevel'] as int? ?? 3,
        notes: json['notes'] as String? ?? '',
        createdAt: DateTime.tryParse(json['createdAt'] as String? ?? '') ?? DateTime.now(),
      );

  /// All available behaviors, flattened from categories (backward compatible).
  static List<String> get availableBehaviors =>
      behaviorCategories.values.expand((v) => v).toList();

  /// Behaviors organized by category.
  static const Map<String, List<String>> behaviorCategories = {
    'Sleep': ['Shared Bed', 'Dark Room', 'Blue Light Blocker', 'Nap', 'Sleep Mask', 'White Noise'],
    'Diet': ['Caffeine', 'Alcohol', 'Late Meal', 'Hydrated', 'Supplements', 'Melatonin', 'Fasting'],
    'Activity': ['Exercise', 'Stretching', 'Yoga', 'Sauna', 'Cold Exposure', 'Massage'],
    'Mind': ['Meditation', 'Screen Time', 'Reading', 'Breathwork', 'Nature Time'],
    'Health': ['Sick', 'Allergies', 'Medication', 'Travel'],
  };

  /// Icons for each behavior.
  static const Map<String, IconData> behaviorIcons = {
    // Sleep
    'Shared Bed': Icons.people,
    'Dark Room': Icons.dark_mode,
    'Blue Light Blocker': Icons.visibility_off,
    'Nap': Icons.airline_seat_flat,
    'Sleep Mask': Icons.remove_red_eye,
    'White Noise': Icons.graphic_eq,
    // Diet
    'Caffeine': Icons.coffee,
    'Alcohol': Icons.local_bar,
    'Late Meal': Icons.restaurant,
    'Hydrated': Icons.water_drop,
    'Supplements': Icons.medication,
    'Melatonin': Icons.nights_stay,
    'Fasting': Icons.no_food,
    // Activity
    'Exercise': Icons.fitness_center,
    'Stretching': Icons.accessibility_new,
    'Yoga': Icons.self_improvement,
    'Sauna': Icons.hot_tub,
    'Cold Exposure': Icons.ac_unit,
    'Massage': Icons.spa,
    // Mind
    'Meditation': Icons.self_improvement,
    'Screen Time': Icons.phone_android,
    'Reading': Icons.menu_book,
    'Breathwork': Icons.air,
    'Nature Time': Icons.park,
    // Health
    'Sick': Icons.sick,
    'Allergies': Icons.healing,
    'Medication': Icons.medical_services,
    'Travel': Icons.flight,
  };

  /// Category icon for section headers.
  static const Map<String, IconData> categoryIcons = {
    'Sleep': Icons.bedtime,
    'Diet': Icons.restaurant_menu,
    'Activity': Icons.directions_run,
    'Mind': Icons.psychology,
    'Health': Icons.favorite,
  };
}
