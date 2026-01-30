import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../screens/login_screen.dart';
import '../screens/home_screen.dart';
import '../screens/calendar_screen.dart';
import '../screens/trends_screen.dart';
import '../screens/settings_screen.dart';
import '../screens/day_detail_screen.dart';
import '../screens/health_screen.dart';
import '../screens/sleep_detail_screen.dart';
import '../screens/recovery_detail_screen.dart';
import '../screens/strain_detail_screen.dart';
import '../screens/activity_detail_screen.dart';
import '../screens/sensor_data_screen.dart';
import '../screens/analysis_screen.dart';
import '../screens/device_screen.dart';
import '../screens/start_activity_screen.dart';
import '../screens/activity_timer_screen.dart';
import '../screens/activity_summary_screen.dart';
import '../screens/journal_screen.dart';
import '../screens/report_screen.dart';
import '../screens/stress_screen.dart';
import '../screens/onboarding_screen.dart';
import '../screens/chat_screen.dart';
import '../core/theme.dart';
import '../services/auth_service.dart';

final _rootNavigatorKey = GlobalKey<NavigatorState>();

CustomTransitionPage<void> _noTransition(Widget child, GoRouterState state) {
  return CustomTransitionPage<void>(
    key: state.pageKey,
    child: child,
    transitionsBuilder: (_, __, ___, child) => child,
    transitionDuration: Duration.zero,
    reverseTransitionDuration: Duration.zero,
  );
}

GoRouter createRouter(AuthService authService) => GoRouter(
      navigatorKey: _rootNavigatorKey,
      initialLocation: '/',
      refreshListenable: authService,
      redirect: (context, state) {
        final loggedIn = authService.isAuthenticated;
        final loggingIn = state.matchedLocation == '/login';
        final onboarding = state.matchedLocation == '/onboarding';
        if (!loggedIn && !loggingIn) return '/login';
        if (loggedIn && loggingIn) return '/';
        if (onboarding) return null;
        return null;
      },
      routes: [
        GoRoute(
          path: '/login',
          pageBuilder: (context, state) => _noTransition(const LoginScreen(), state),
        ),
        GoRoute(
          path: '/onboarding',
          pageBuilder: (context, state) => _noTransition(const OnboardingScreen(), state),
        ),
        ShellRoute(
          builder: (context, state, child) => HomeShell(
            location: state.matchedLocation,
            child: child,
          ),
          routes: [
            GoRoute(
              path: '/',
              pageBuilder: (context, state) => _noTransition(const HomeScreen(), state),
            ),
            GoRoute(
              path: '/chat',
              pageBuilder: (context, state) => _noTransition(const ChatScreen(), state),
            ),
            GoRoute(
              path: '/settings',
              pageBuilder: (context, state) => _noTransition(const SettingsScreen(), state),
            ),
          ],
        ),
        // Standalone routes (no bottom nav)
        GoRoute(
          path: '/calendar',
          pageBuilder: (context, state) => _noTransition(const CalendarScreen(), state),
        ),
        GoRoute(
          path: '/trends',
          pageBuilder: (context, state) => _noTransition(const TrendsScreen(), state),
        ),
        GoRoute(
          path: '/analysis',
          pageBuilder: (context, state) => _noTransition(const AnalysisScreen(), state),
        ),
        GoRoute(
          path: '/health',
          pageBuilder: (context, state) => _noTransition(const HealthScreen(), state),
        ),
        GoRoute(
          path: '/day/:date',
          pageBuilder: (context, state) =>
              _noTransition(DayDetailScreen(date: state.pathParameters['date']!), state),
        ),
        GoRoute(
          path: '/sleep/:date',
          pageBuilder: (context, state) => _noTransition(
            SleepDetailScreen(
              date: state.pathParameters['date']!,
              activityId: state.uri.queryParameters['activityId'],
            ),
            state,
          ),
        ),
        GoRoute(
          path: '/recovery/:date',
          pageBuilder: (context, state) =>
              _noTransition(RecoveryDetailScreen(date: state.pathParameters['date']!), state),
        ),
        GoRoute(
          path: '/strain/:date',
          pageBuilder: (context, state) =>
              _noTransition(StrainDetailScreen(date: state.pathParameters['date']!), state),
        ),
        GoRoute(
          path: '/activity/:id',
          pageBuilder: (context, state) =>
              _noTransition(ActivityDetailScreen(activityId: state.pathParameters['id']!), state),
        ),
        GoRoute(
          path: '/sensor-data',
          pageBuilder: (context, state) => _noTransition(const SensorDataScreen(), state),
        ),
        GoRoute(
          path: '/device',
          pageBuilder: (context, state) => _noTransition(const DeviceScreen(), state),
        ),
        GoRoute(
          path: '/start-activity',
          pageBuilder: (context, state) => _noTransition(const StartActivityScreen(), state),
        ),
        GoRoute(
          path: '/activity-timer',
          pageBuilder: (context, state) => _noTransition(const ActivityTimerScreen(), state),
        ),
        GoRoute(
          path: '/activity-summary/:id',
          pageBuilder: (context, state) => _noTransition(
            ActivitySummaryScreen(activityId: state.pathParameters['id']!),
            state,
          ),
        ),
        GoRoute(
          path: '/journal',
          pageBuilder: (context, state) => _noTransition(const JournalScreen(), state),
        ),
        GoRoute(
          path: '/report',
          pageBuilder: (context, state) => _noTransition(const ReportScreen(), state),
        ),
        GoRoute(
          path: '/stress/:date',
          pageBuilder: (context, state) =>
              _noTransition(StressScreen(date: state.pathParameters['date']!), state),
        ),
      ],
    );

class HomeShell extends StatelessWidget {
  final Widget child;
  final String location;
  const HomeShell({super.key, required this.child, required this.location});

  static const _paths = ['/', '/chat', '/settings'];

  int get _currentIndex {
    final idx = _paths.indexOf(location);
    return idx >= 0 ? idx : 0;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: WhoopTheme.background,
      body: child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: _currentIndex,
        onDestinationSelected: (i) => context.go(_paths[i]),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.auto_awesome_outlined), selectedIcon: Icon(Icons.auto_awesome), label: 'AI Coach'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}
