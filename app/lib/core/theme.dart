import 'package:flutter/material.dart';

class WhoopTheme {
  WhoopTheme._();

  // Core colors — AMOLED dark theme
  static const background = Color(0xFF000000);
  static const surface = Color(0xFF0D0E12);
  static const surfaceContainer = Color(0xFF161820);
  static const cardBorder = Color(0xFF1E2028);
  static const primary = Color(0xFF7DCE82);
  static const primaryContainer = Color(0xFF122016);
  static const error = Color(0xFFFF6B6B);
  static const warning = Color(0xFFFFD666);
  static const textPrimary = Color(0xFFE8EAEF);
  static const textSecondary = Color(0xFF6B7080);
  static const divider = Color(0xFF1A1C22);

  // Score colors
  static const sleepBlue = Color(0xFF7EB8F7);
  static const recoveryGreen = Color(0xFF7DCE82);
  static const recoveryYellow = Color(0xFFFFD666);
  static const recoveryRed = Color(0xFFFF6B6B);
  static const strainAmber = Color(0xFFFFD666);

  // Ring colors
  static const stepsBlue = Color(0xFF5BAAFF);
  static const caloriesOrange = Color(0xFFFF9F5A);
  static const exertionGold = Color(0xFFE8B84A);

  static BoxDecoration cardDecoration({double radius = 16}) {
    return BoxDecoration(
      color: surface,
      borderRadius: BorderRadius.circular(radius),
      border: Border.all(color: cardBorder, width: 0.5),
    );
  }

  static Color recoveryColor(double score) {
    if (score < 34) return recoveryRed;
    if (score < 67) return recoveryYellow;
    return recoveryGreen;
  }

  /// Build the Material 3 AMOLED dark theme, optionally with system dynamic colors.
  static ThemeData buildTheme(ColorScheme? dynamicDark) {
    final colorScheme = dynamicDark ?? ColorScheme.fromSeed(
      seedColor: const Color(0xFF7DCE82),
      brightness: Brightness.dark,
      surface: surface,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: colorScheme.copyWith(
        surface: surface,
      ),
      scaffoldBackgroundColor: background,
      appBarTheme: const AppBarTheme(
        backgroundColor: Colors.transparent,
        foregroundColor: textPrimary,
        elevation: 0,
        centerTitle: true,
        surfaceTintColor: Colors.transparent,
        iconTheme: IconThemeData(color: textPrimary),
      ),
      cardTheme: CardThemeData(
        color: surface,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: cardBorder, width: 0.5),
        ),
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        indicatorColor: primaryContainer,
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const TextStyle(
              color: primary,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            );
          }
          return const TextStyle(
            color: textSecondary,
            fontSize: 12,
            fontWeight: FontWeight.w500,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const IconThemeData(color: primary, size: 24);
          }
          return const IconThemeData(color: textSecondary, size: 24);
        }),
        height: 70,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: surface,
        selectedItemColor: primary,
        unselectedItemColor: textSecondary,
        type: BottomNavigationBarType.fixed,
        elevation: 0,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surfaceContainer,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: cardBorder),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: cardBorder),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: primary),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: primary,
          foregroundColor: background,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
          textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16),
        ),
      ),
      dividerColor: divider,
      dialogTheme: DialogThemeData(
        backgroundColor: surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      textTheme: const TextTheme(
        headlineLarge: TextStyle(color: textPrimary, fontWeight: FontWeight.bold),
        headlineMedium: TextStyle(color: textPrimary, fontWeight: FontWeight.bold),
        titleLarge: TextStyle(color: textPrimary, fontWeight: FontWeight.w600),
        titleMedium: TextStyle(color: textPrimary, fontWeight: FontWeight.w500),
        bodyLarge: TextStyle(color: textPrimary),
        bodyMedium: TextStyle(color: textSecondary),
        labelLarge: TextStyle(color: textPrimary, fontWeight: FontWeight.w600),
      ),
      expansionTileTheme: const ExpansionTileThemeData(
        iconColor: textSecondary,
        collapsedIconColor: textSecondary,
      ),
    );
  }

  // Legacy fallback (used when DynamicColorBuilder isn't available)
  static final darkTheme = buildTheme(null);
}
