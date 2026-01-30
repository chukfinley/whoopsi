import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/theme.dart';
import '../models/coaching.dart';
import '../models/recovery.dart';
import '../models/sleep.dart';
import '../models/sport_types.dart';
import '../models/strain.dart';
import '../services/api_service.dart';
import '../services/activity_tracker_service.dart';
import '../services/ai_service.dart';
import '../services/ble_service.dart';
import '../services/hydration_service.dart';
import '../services/journal_service.dart';
import '../services/sync_orchestrator.dart';
import '../services/weather_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/score_gauge.dart';
import '../widgets/weather_card.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  Recovery? _recovery;
  Sleep? _sleep;
  Strain? _strain;
  bool _loading = true;
  String? _error;
  DateTime _selectedDate = DateTime.now();
  Map<String, dynamic>? _sleepLastNight;
  List<Map<String, dynamic>> _sleepEntries = [];
  List<Map<String, dynamic>> _workoutActivities = [];
  Map<String, dynamic>? _profile;

  @override
  void initState() {
    super.initState();
    _restoreDateAndFetch();
    final ble = context.read<BleService>();
    if (!ble.connected) ble.connect();
    _loadProfile();
    // Auto-refresh when sync+upload completes
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<SyncOrchestrator>().addListener(_onSyncComplete);
    });
  }

  @override
  void dispose() {
    try {
      context.read<SyncOrchestrator>().removeListener(_onSyncComplete);
    } catch (_) {}
    super.dispose();
  }

  void _onSyncComplete() {
    if (!mounted || !_isToday) return;
    // Cloud needs processing time after upload
    Future.delayed(const Duration(seconds: 3), () {
      if (mounted && _isToday) _fetch();
    });
  }

  Future<void> _loadProfile() async {
    final api = context.read<ApiService>();
    final cached = api.cache.get<Map<String, dynamic>>('profile');
    if (cached != null && mounted) setState(() => _profile = cached);
    try {
      final p = await api.getProfile(forceRefresh: cached != null);
      if (mounted) setState(() => _profile = p);
    } catch (_) {}
  }

  Future<void> _restoreDateAndFetch() async {
    final prefs = await SharedPreferences.getInstance();
    final resumeLastDate = prefs.getBool('startup_resume_last_date') ?? false;
    if (resumeLastDate) {
      final api = context.read<ApiService>();
      final saved = api.cache.get<Map<String, dynamic>>('app_last_date');
      if (saved != null) {
        final dateStr = saved['date'] as String?;
        if (dateStr != null) {
          final parsed = DateTime.tryParse(dateStr);
          if (parsed != null) _selectedDate = parsed;
        }
      }
    }
    _fetch();
  }

  void _saveDate() {
    final api = context.read<ApiService>();
    api.cache.setPermanent('app_last_date', {'date': _dateStr});
  }

  String get _dateStr => DateFormat('yyyy-MM-dd').format(_selectedDate);

  bool get _isToday =>
      DateFormat('yyyy-MM-dd').format(_selectedDate) ==
      DateFormat('yyyy-MM-dd').format(DateTime.now());

  Future<void> _fetch() async {
    final api = context.read<ApiService>();

    bool hasCached = false;
    final cachedRec = api.cache.get<Map<String, dynamic>>('deep_dive:recovery:$_dateStr');
    final cachedSlp = api.cache.get<Map<String, dynamic>>('deep_dive:sleep:$_dateStr');
    final cachedStr = api.cache.get<Map<String, dynamic>>('deep_dive:strain:$_dateStr');
    if (cachedRec != null || cachedSlp != null || cachedStr != null) {
      hasCached = true;
      _recovery = cachedRec != null ? Recovery.fromDeepDive(cachedRec) : null;
      _sleep = cachedSlp != null ? Sleep.fromDeepDive(cachedSlp) : null;
      _strain = cachedStr != null ? Strain.fromDeepDive(cachedStr) : null;
      _sleepLastNight = api.cache.get<Map<String, dynamic>>('sleep_last_night:$_dateStr');
      // Also restore cached activities immediately so My Day isn't empty
      _sleepEntries = [];
      _workoutActivities = [];
      final cachedSleepAct = api.cache.get<Map<String, dynamic>>('sleep_activities:$_dateStr');
      if (cachedSleepAct != null) {
        for (final r in cachedSleepAct['records'] as List? ?? []) {
          _sleepEntries.add(r as Map<String, dynamic>);
        }
      }
      final cachedWorkouts = api.cache.get<Map<String, dynamic>>('workout_activities:$_dateStr');
      if (cachedWorkouts != null) {
        for (final r in cachedWorkouts['records'] as List? ?? []) {
          _workoutActivities.add(r as Map<String, dynamic>);
        }
      }
      if (mounted) setState(() => _loading = false);
    } else {
      setState(() {
        _loading = true;
        _error = null;
        _recovery = null;
        _sleep = null;
        _strain = null;
        _sleepLastNight = null;
        _sleepEntries = [];
        _workoutActivities = [];
      });
    }

    try {
      // Parallel fetch all data for better performance
      Recovery? rec;
      Sleep? slp;
      Strain? str;
      Map<String, dynamic>? sleepLn;
      final sleepEntries = <Map<String, dynamic>>[];
      final workouts = <Map<String, dynamic>>[];

      final results = await Future.wait([
        api.getDeepDive('recovery', _dateStr, forceRefresh: hasCached).catchError((_) => <String, dynamic>{}),
        api.getDeepDive('sleep', _dateStr, forceRefresh: hasCached).catchError((_) => <String, dynamic>{}),
        api.getDeepDive('strain', _dateStr, forceRefresh: hasCached).catchError((_) => <String, dynamic>{}),
        api.getSleepLastNight(_dateStr, forceRefresh: hasCached).catchError((_) => <String, dynamic>{}),
        api.getSleepActivities(_dateStr, forceRefresh: hasCached).catchError((_) => <String, dynamic>{}),
        api.getWorkoutActivities(_dateStr, forceRefresh: hasCached).catchError((_) => <String, dynamic>{}),
      ]);

      final recData = results[0] as Map<String, dynamic>;
      if (recData.isNotEmpty) rec = Recovery.fromDeepDive(recData);
      final slpData = results[1] as Map<String, dynamic>;
      if (slpData.isNotEmpty) slp = Sleep.fromDeepDive(slpData);
      final strData = results[2] as Map<String, dynamic>;
      if (strData.isNotEmpty) str = Strain.fromDeepDive(strData);
      final sleepLnData = results[3] as Map<String, dynamic>;
      if (sleepLnData.isNotEmpty) sleepLn = sleepLnData;
      final sleepActData = results[4] as Map<String, dynamic>;
      for (final r in sleepActData['records'] as List? ?? []) {
        sleepEntries.add(r as Map<String, dynamic>);
      }
      final workoutData = results[5] as Map<String, dynamic>;
      for (final r in workoutData['records'] as List? ?? []) {
        workouts.add(r as Map<String, dynamic>);
      }

      if (mounted) {
        setState(() {
          if (rec != null) _recovery = rec;
          if (slp != null) _sleep = slp;
          if (str != null) _strain = str;
          _sleepLastNight = sleepLn;
          _sleepEntries = sleepEntries;
          _workoutActivities = workouts;
          _loading = false;
        });
      }

      // Trigger AI insight fetch if enabled and today
      if (_isToday && rec != null) {
        _fetchAiInsight(rec, slp, str);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          if (!hasCached) _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  void _fetchAiInsight(Recovery rec, Sleep? slp, Strain? str) {
    final ai = context.read<AiService>();
    if (!ai.enabled || ai.hasTodayInsight) return;
    ai.getInsight(
      recovery: rec.score,
      hrvMs: rec.hrvMs,
      rhr: rec.rhr,
      sleepHours: _parseSleepHours(slp),
      sleepNeeded: 7.5,
      sleepEfficiency: _parseSleepEfficiency(slp),
      yesterdayStrain: str?.score ?? 0,
      recoveryTrend: [rec.score],
    );
  }

  double _parseSleepHours(Sleep? slp) {
    if (slp?.hoursVsNeeded == null) return 0;
    final match = RegExp(r'(\d+)h\s*(\d+)m').firstMatch(slp!.hoursVsNeeded!);
    if (match != null) return int.parse(match.group(1)!) + int.parse(match.group(2)!) / 60;
    return double.tryParse(slp.hoursVsNeeded!.replaceAll(RegExp(r'[^0-9.]'), '')) ?? 0;
  }

  double _parseSleepEfficiency(Sleep? slp) {
    if (slp?.efficiency == null) return 0;
    return double.tryParse(slp!.efficiency!.replaceAll(RegExp(r'[^0-9.]'), '')) ?? 0;
  }

  void _changeDate(int days) {
    HapticFeedback.lightImpact();
    setState(() { _selectedDate = _selectedDate.add(Duration(days: days)); });
    _saveDate();
    _fetch();
  }

  List<Map<String, dynamic>> _parseActivities() {
    if (_strain == null) return [];
    final raw = _strain!.raw;
    final activities = <Map<String, dynamic>>[];
    for (final section in raw['sections'] as List? ?? []) {
      for (final item in (section as Map)['items'] as List? ?? []) {
        final map = item as Map<String, dynamic>;
        final type = (map['type'] as String? ?? '').toUpperCase();
        if (type.contains('ACTIVITY')) {
          final content = map['content'] as Map<String, dynamic>? ?? {};
          activities.add({
            'name': content['title'] ?? content['name'] ?? 'Activity',
            'score': content['score_display'] ?? content['strain'] ?? '',
            'time': content['subtitle'] ?? content['time_range'] ?? '',
            'id': content['activity_id'] ?? content['id'] ?? '',
            'type': type,
          });
        }
      }
    }
    return activities;
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: _loading
          ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
          : _error != null
              ? _buildError()
              : RefreshIndicator(
                  color: WhoopTheme.primary,
                  onRefresh: _fetch,
                  child: _buildContent(),
                ),
    );
  }

  Widget _buildError() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off, color: WhoopTheme.textSecondary, size: 48),
            const SizedBox(height: 16),
            Text('Unable to load data', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(_error!, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13),
                textAlign: TextAlign.center, maxLines: 3, overflow: TextOverflow.ellipsis),
            const SizedBox(height: 24),
            OutlinedButton.icon(
              onPressed: _fetch,
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
              style: OutlinedButton.styleFrom(
                foregroundColor: WhoopTheme.primary,
                side: const BorderSide(color: WhoopTheme.primary),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildContent() {
    final rec = _recovery ?? const Recovery();
    final slp = _sleep ?? const Sleep();
    final str = _strain ?? const Strain();
    final activities = _workoutActivities.isNotEmpty ? _workoutActivities : <Map<String, dynamic>>[];
    final parsedActivities = _parseActivities();
    final tracker = context.watch<ActivityTrackerService>();

    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.symmetric(horizontal: 16),
      children: [
        const SizedBox(height: 8),
        _buildTopBar(),
        const SizedBox(height: 12),
        _buildDateBar(),
        const SizedBox(height: 16),

        // Weather card (only shows if location is set)
        const WeatherCard(),

        // Active activity banner
        if (tracker.hasActiveActivity) ...[
          const SizedBox(height: 12),
          _buildActiveActivityBanner(tracker),
        ],
        const SizedBox(height: 12),

        // Three gauges: Sleep | Recovery (big) | Strain
        GlassCard(
          padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              ScoreGauge(
                score: slp.score,
                maxScore: 100,
                label: 'Sleep',
                color: WhoopTheme.sleepBlue,
                onTap: () {
                  HapticFeedback.lightImpact();
                  context.push('/sleep/$_dateStr');
                },
              ),
              ScoreGauge(
                score: rec.score,
                maxScore: 100,
                label: 'Recovery',
                color: WhoopTheme.recoveryColor(rec.score),
                size: 120,
                onTap: () {
                  HapticFeedback.lightImpact();
                  context.push('/recovery/$_dateStr');
                },
              ),
              ScoreGauge(
                score: str.score,
                maxScore: 21,
                label: 'Strain',
                color: WhoopTheme.strainAmber,
                onTap: () {
                  HapticFeedback.lightImpact();
                  context.push('/strain/$_dateStr');
                },
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),

        // Stats box: Steps, Calories, Strain breakdown
        _buildStatsBox(str),
        const SizedBox(height: 16),

        // AI Insights card (only if enabled)
        _buildAiInsightsCard(),

        // Strain Coach + Sleep Coach side by side
        if (_isToday && rec.score > 0) ...[
          Row(
            children: [
              Expanded(child: _buildStrainCoachCard(rec.score)),
              const SizedBox(width: 10),
              Expanded(child: _buildSleepCoachCard()),
            ],
          ),
          const SizedBox(height: 12),
        ],

        // Hydration card
        if (_isToday) ...[
          _buildHydrationCard(),
          const SizedBox(height: 12),
        ],

        // Quick actions row
        if (_isToday) ...[
          _buildQuickActions(),
          const SizedBox(height: 16),
        ],

        // My Day
        Padding(
          padding: const EdgeInsets.only(left: 4, bottom: 10),
          child: Row(
            children: [
              const Text('My Day', style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600)),
              const Spacer(),
              if (_isToday && !tracker.hasActiveActivity)
                GestureDetector(
                  onTap: () {
                    HapticFeedback.lightImpact();
                    context.push('/start-activity');
                  },
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                    decoration: BoxDecoration(
                      color: WhoopTheme.primary.withOpacity(0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.add, color: WhoopTheme.primary, size: 14),
                        SizedBox(width: 4),
                        Text('Activity', style: TextStyle(color: WhoopTheme.primary, fontSize: 12, fontWeight: FontWeight.w600)),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ),
        ..._buildSleepCards(slp),
        if (activities.isNotEmpty)
          ...activities.map(_buildWorkoutCard)
        else if (parsedActivities.isNotEmpty)
          ...parsedActivities.map(_buildActivityCard)
        else if (_sleep == null && _sleepEntries.isEmpty)
          _buildEmptyActivities(),
        const SizedBox(height: 80),
      ],
    );
  }

  // ─── Strain Coach Card ────────────────────────────────────────

  Widget _buildStrainCoachCard(double recoveryScore) {
    final coach = StrainCoach(recoveryScore);
    Color zoneColor;
    IconData zoneIcon;
    switch (coach.zone) {
      case 'green':
        zoneColor = WhoopTheme.recoveryGreen;
        zoneIcon = Icons.rocket_launch;
        break;
      case 'yellow':
        zoneColor = WhoopTheme.warning;
        zoneIcon = Icons.trending_flat;
        break;
      default:
        zoneColor = WhoopTheme.error;
        zoneIcon = Icons.hotel;
    }

    return GestureDetector(
      onTap: () {
        HapticFeedback.lightImpact();
        context.push('/strain/$_dateStr');
      },
      child: GlassCard(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(zoneIcon, color: zoneColor, size: 18),
                const SizedBox(width: 6),
                Text('Strain Coach', style: TextStyle(color: zoneColor, fontSize: 12, fontWeight: FontWeight.w600)),
              ],
            ),
            const SizedBox(height: 8),
            Text(coach.headline,
                style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text(coach.recommendation,
                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, height: 1.3)),
          ],
        ),
      ),
    );
  }

  // ─── Sleep Coach Card ─────────────────────────────────────────

  Widget _buildSleepCoachCard() {
    final coach = SleepCoach.fromSleepData(_sleepLastNight);
    return GestureDetector(
      onTap: () {
        HapticFeedback.lightImpact();
        context.push('/sleep/$_dateStr');
      },
      child: GlassCard(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.bedtime, color: WhoopTheme.sleepBlue, size: 18),
                const SizedBox(width: 6),
                const Text('Sleep Coach', style: TextStyle(color: WhoopTheme.sleepBlue, fontSize: 12, fontWeight: FontWeight.w600)),
              ],
            ),
            const SizedBox(height: 8),
            Text('Need ${coach.sleepNeededDisplay}',
                style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text('Bedtime: ${coach.recommendedBedtime}',
                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
            if (coach.sleepDebt > 0)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(coach.debtDisplay,
                    style: const TextStyle(color: WhoopTheme.warning, fontSize: 11)),
              ),
          ],
        ),
      ),
    );
  }

  // ─── AI Insights Card ─────────────────────────────────────────

  Widget _buildAiInsightsCard() {
    return Consumer<AiService>(
      builder: (context, ai, _) {
        if (!ai.enabled) return const SizedBox.shrink();
        if (ai.loading) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: GlassCard(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  const SizedBox(width: 16, height: 16,
                      child: CircularProgressIndicator(color: WhoopTheme.primary, strokeWidth: 2)),
                  const SizedBox(width: 12),
                  const Text('Generating insights...', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
                ],
              ),
            ),
          );
        }
        if (ai.cachedInsight == null) return const SizedBox.shrink();
        return Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: GlassCard(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.auto_awesome, color: WhoopTheme.primary, size: 18),
                    const SizedBox(width: 8),
                    const Text('AI Insights', style: TextStyle(color: WhoopTheme.primary, fontSize: 13, fontWeight: FontWeight.w600)),
                    const Spacer(),
                    GestureDetector(
                      onTap: () {
                        ai.clearCache();
                        if (_recovery != null) _fetchAiInsight(_recovery!, _sleep, _strain);
                      },
                      child: const Icon(Icons.refresh, color: WhoopTheme.textSecondary, size: 16),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Text(ai.cachedInsight!,
                    style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13, height: 1.5)),
              ],
            ),
          ),
        );
      },
    );
  }

  // ─── Hydration Card ───────────────────────────────────────────

  Widget _buildHydrationCard() {
    return Consumer<HydrationService>(
      builder: (context, hydration, _) {
        final progress = hydration.progress.clamp(0.0, 1.0);
        final color = hydration.goalReached ? WhoopTheme.primary : WhoopTheme.sleepBlue;
        return GlassCard(
          padding: const EdgeInsets.all(14),
          child: Column(
            children: [
              Row(
                children: [
                  Icon(Icons.water_drop, color: color, size: 20),
                  const SizedBox(width: 8),
                  Text('Hydration', style: TextStyle(color: color, fontSize: 13, fontWeight: FontWeight.w600)),
                  const Spacer(),
                  Text('${hydration.intakeMl} / ${hydration.goalMl} ml',
                      style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                ],
              ),
              const SizedBox(height: 10),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: progress,
                  backgroundColor: WhoopTheme.divider,
                  color: color,
                  minHeight: 8,
                ),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  // Glass count
                  Text('${hydration.glassesConsumed} glasses',
                      style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                  const Spacer(),
                  // Remove button
                  GestureDetector(
                    onTap: () {
                      HapticFeedback.lightImpact();
                      hydration.removeGlass();
                    },
                    child: Container(
                      width: 36, height: 36,
                      decoration: BoxDecoration(
                        color: WhoopTheme.surfaceContainer,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: const Icon(Icons.remove, color: WhoopTheme.textSecondary, size: 18),
                    ),
                  ),
                  const SizedBox(width: 8),
                  // Add button - prominent
                  GestureDetector(
                    onTap: () {
                      HapticFeedback.lightImpact();
                      hydration.addGlass();
                    },
                    child: Container(
                      height: 36,
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      decoration: BoxDecoration(
                        color: color.withOpacity(0.15),
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.add, color: color, size: 18),
                          const SizedBox(width: 4),
                          Text('+${hydration.glassSizeMl}ml',
                              style: TextStyle(color: color, fontSize: 13, fontWeight: FontWeight.w600)),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  // ─── Quick Actions ────────────────────────────────────────────

  Widget _buildQuickActions() {
    return Column(
      children: [
        Row(
          children: [
            _quickAction(Icons.calendar_today, 'Calendar', () {
              HapticFeedback.lightImpact();
              context.push('/calendar');
            }),
            const SizedBox(width: 8),
            _quickAction(Icons.trending_up, 'Trends', () {
              HapticFeedback.lightImpact();
              context.push('/trends');
            }),
            const SizedBox(width: 8),
            _quickAction(Icons.insights, 'Insights', () {
              HapticFeedback.lightImpact();
              context.push('/analysis');
            }),
            const SizedBox(width: 8),
            _quickAction(Icons.monitor_heart, 'Health', () {
              HapticFeedback.lightImpact();
              context.push('/health');
            }),
          ],
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            _quickAction(Icons.book_outlined, 'Journal', () {
              HapticFeedback.lightImpact();
              context.push('/journal');
            }),
            const SizedBox(width: 8),
            _quickAction(Icons.bar_chart, 'Report', () {
              HapticFeedback.lightImpact();
              context.push('/report');
            }),
            const SizedBox(width: 8),
            _quickAction(Icons.psychology, 'Stress', () {
              HapticFeedback.lightImpact();
              context.push('/stress/$_dateStr');
            }),
            const SizedBox(width: 8),
            const Expanded(child: SizedBox()),
          ],
        ),
      ],
    );
  }

  Widget _quickAction(IconData icon, String label, VoidCallback onTap) {
    return Expanded(
      child: GestureDetector(
        onTap: onTap,
        child: GlassCard(
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Column(
            children: [
              Icon(icon, color: WhoopTheme.textSecondary, size: 20),
              const SizedBox(height: 4),
              Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w500)),
            ],
          ),
        ),
      ),
    );
  }

  // ─── Existing widgets (unchanged) ─────────────────────────────

  Widget _buildActiveActivityBanner(ActivityTrackerService tracker) {
    final activity = tracker.currentActivity!;
    final elapsed = activity.elapsed;
    final h = elapsed.inHours;
    final m = elapsed.inMinutes % 60;
    final s = elapsed.inSeconds % 60;
    final timeStr = h > 0
        ? '${h}h ${m.toString().padLeft(2, '0')}m'
        : '${m}:${s.toString().padLeft(2, '0')}';

    return GestureDetector(
      onTap: () => context.push('/activity-timer'),
      child: GlassCard(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: Row(
          children: [
            Container(
              width: 36, height: 36,
              decoration: BoxDecoration(
                color: WhoopTheme.primary.withOpacity(0.15),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Icon(
                SportTypes.icon(activity.sportId ?? -1),
                color: WhoopTheme.primary, size: 18,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    activity.sportName ?? 'Activity',
                    style: const TextStyle(color: WhoopTheme.primary, fontSize: 14, fontWeight: FontWeight.w600),
                  ),
                  Text(timeStr, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                ],
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
              decoration: BoxDecoration(
                color: WhoopTheme.primary.withOpacity(0.12),
                borderRadius: BorderRadius.circular(8),
              ),
              child: const Text('TAP TO VIEW', style: TextStyle(color: WhoopTheme.primary, fontSize: 10, fontWeight: FontWeight.w700)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTopBar() {
    return Consumer<BleService>(
      builder: (context, ble, _) {
        final hr = ble.heartRate;
        final battery = ble.batteryLevel;
        final firstName = _profile?['first_name'] as String? ?? '';

        return Row(
          children: [
            // Username left — tap to connect or show device info
            GestureDetector(
              onTap: () {
                if (!ble.connected) {
                  ble.connect();
                } else {
                  _showDeviceInfo(ble);
                }
              },
              child: Row(
                children: [
                  Icon(
                    ble.connected ? Icons.watch : Icons.watch_off_outlined,
                    color: ble.connected ? WhoopTheme.primary : WhoopTheme.textSecondary,
                    size: 18,
                  ),
                  const SizedBox(width: 6),
                  Text(
                    firstName.isNotEmpty ? firstName : 'Open Whoop',
                    style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 15, fontWeight: FontWeight.w600),
                  ),
                ],
              ),
            ),
            const Spacer(),
            // HR + Battery + Sync indicator — tap to open device screen
            GestureDetector(
              onTap: () => context.push('/device'),
              child: Row(
                children: [
                  // Sync indicator (spinning icon when syncing)
                  if (ble.syncPhase != SyncPhase.idle && ble.syncPhase != SyncPhase.done)
                    const _RotatingSyncIcon(),
                  if (ble.syncPhase != SyncPhase.idle && ble.syncPhase != SyncPhase.done)
                    const SizedBox(width: 8),
                  // Live HR (only show value when actually live)
                  Icon(Icons.favorite, color: ble.isHrLive ? WhoopTheme.error : WhoopTheme.textSecondary, size: 14),
                  const SizedBox(width: 3),
                  Text(
                    ble.isHrLive && hr > 0 ? '$hr' : '--',
                    style: TextStyle(color: ble.isHrLive ? WhoopTheme.textPrimary : WhoopTheme.textSecondary, fontSize: 13, fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(width: 14),
                  // Battery
                  Icon(
                    battery >= 0
                        ? (ble.isCharging
                            ? Icons.battery_charging_full
                            : (battery > 20 ? Icons.battery_full : Icons.battery_alert))
                        : Icons.battery_unknown,
                    color: battery >= 0 ? WhoopTheme.textPrimary : WhoopTheme.textSecondary,
                    size: 16,
                  ),
                  const SizedBox(width: 2),
                  Text(
                    battery >= 0 ? '$battery%' : '--%',
                    style: TextStyle(color: battery >= 0 ? WhoopTheme.textPrimary : WhoopTheme.textSecondary, fontSize: 13, fontWeight: FontWeight.w500),
                  ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _buildDateBar() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        GestureDetector(
          onTap: () => _changeDate(-1),
          child: const Padding(
            padding: EdgeInsets.all(6),
            child: Icon(Icons.chevron_left, color: WhoopTheme.textPrimary, size: 22),
          ),
        ),
        const SizedBox(width: 8),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
            color: WhoopTheme.surface,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: WhoopTheme.cardBorder),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                _isToday ? 'TODAY' : DateFormat('MMM d').format(_selectedDate).toUpperCase(),
                style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600, letterSpacing: 0.5),
              ),
              const SizedBox(width: 6),
              const Icon(Icons.calendar_today, size: 14, color: WhoopTheme.textSecondary),
            ],
          ),
        ),
        const SizedBox(width: 8),
        GestureDetector(
          onTap: _isToday ? null : () => _changeDate(1),
          child: Padding(
            padding: const EdgeInsets.all(6),
            child: Icon(Icons.chevron_right,
                color: _isToday ? WhoopTheme.cardBorder : WhoopTheme.textPrimary, size: 22),
          ),
        ),
      ],
    );
  }

  Widget _buildStatsBox(Strain str) {
    // Extract steps and calories
    double steps = 0;
    double calories = 0;
    if (str.steps != null) {
      steps = double.tryParse(str.steps!.replaceAll(RegExp(r'[^0-9.]'), '')) ?? 0;
    }
    try {
      final sections = str.raw['sections'] as List? ?? [];
      for (final sec in sections) {
        for (final item in (sec as Map)['items'] as List? ?? []) {
          final content = (item as Map)['content'] as Map<String, dynamic>? ?? {};
          final metrics = content['metrics'] as List?;
          if (metrics != null) {
            for (final m in metrics) {
              final title = ((m as Map)['title'] ?? '').toString().toLowerCase();
              if (title.contains('calorie') || title.contains('kilojoule')) {
                final val = double.tryParse((m['value'] ?? '').toString().replaceAll(RegExp(r'[^0-9.]'), '')) ?? 0;
                if (title.contains('kilojoule')) {
                  calories = val / 4.184;
                } else {
                  calories = val;
                }
              }
            }
          }
        }
      }
    } catch (_) {}

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Row(
        children: [
          Expanded(child: _statItem(Icons.directions_walk, WhoopTheme.stepsBlue, _formatNumber(steps.round()), 'Steps')),
          Container(width: 1, height: 40, color: WhoopTheme.divider),
          Expanded(child: _statItem(Icons.local_fire_department, WhoopTheme.caloriesOrange, '${calories.round()}', 'Calories')),
          Container(width: 1, height: 40, color: WhoopTheme.divider),
          Expanded(child: _statItem(Icons.flash_on, WhoopTheme.exertionGold, str.score.toStringAsFixed(1), 'Strain')),
        ],
      ),
    );
  }

  Widget _statItem(IconData icon, Color color, String value, String label) {
    return Column(
      children: [
        Icon(icon, color: color, size: 20),
        const SizedBox(height: 6),
        Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w700)),
        Text(label.toUpperCase(), style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
      ],
    );
  }

  String _formatNumber(int n) {
    if (n >= 10000) return '${(n / 1000).toStringAsFixed(1)}k';
    if (n >= 1000) {
      final s = n.toString();
      return '${s.substring(0, s.length - 3)}.${s.substring(s.length - 3, s.length - 2)}k';
    }
    return '$n';
  }

  void _showDeviceInfo(BleService ble) {
    ble.requestBattery();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: WhoopTheme.surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Device Info', style: TextStyle(color: WhoopTheme.textPrimary)),
        content: AnimatedBuilder(
          animation: ble,
          builder: (_, __) => Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _infoRow('Status', ble.status),
              _infoRow('Battery', ble.batteryLevel >= 0 ? '${ble.batteryLevel}%' : '--'),
              _infoRow('Heart Rate', ble.heartRate > 0 ? '${ble.heartRate} BPM' : '--'),
              if (ble.deviceName.isNotEmpty) _infoRow('Name', ble.deviceName),
              if (ble.deviceSerial.isNotEmpty) _infoRow('Serial', ble.deviceSerial),
              if (ble.firmwareInfo.isNotEmpty) _infoRow('Firmware', ble.firmwareInfo),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () { ble.disconnect(); Navigator.pop(ctx); },
            child: const Text('Disconnect', style: TextStyle(color: WhoopTheme.error)),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Close', style: TextStyle(color: WhoopTheme.primary)),
          ),
        ],
      ),
    );
  }

  Widget _infoRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
          const SizedBox(width: 16),
          Flexible(child: Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 13), textAlign: TextAlign.end)),
        ],
      ),
    );
  }

  Widget _buildEmptyActivities() {
    return GlassCard(
      padding: const EdgeInsets.symmetric(vertical: 32, horizontal: 16),
      child: Column(
        children: [
          const Icon(Icons.directions_run, color: WhoopTheme.textSecondary, size: 32),
          const SizedBox(height: 12),
          const Text('No activities', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 14)),
          const SizedBox(height: 4),
          const Text('Activities will appear here when detected',
              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
          if (_isToday) ...[
            const SizedBox(height: 16),
            OutlinedButton.icon(
              onPressed: () => context.push('/start-activity'),
              icon: const Icon(Icons.add, size: 16),
              label: const Text('Start Activity'),
              style: OutlinedButton.styleFrom(
                foregroundColor: WhoopTheme.primary,
                side: const BorderSide(color: WhoopTheme.primary),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
              ),
            ),
          ],
        ],
      ),
    );
  }

  String _formatTime24h(DateTime dt) {
    return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }

  List<Widget> _buildSleepCards(Sleep slp) {
    final cards = <Widget>[];

    String? mainSleepTime;
    if (_sleepLastNight != null) {
      final header = _sleepLastNight!['header_section'] as Map<String, dynamic>?;
      final dest = header?['destination'] as Map<String, dynamic>?;
      final params = dest?['parameters'] as Map<String, dynamic>?;
      final startStr = params?['start_time'] as String?;
      final endStr = params?['end_time'] as String?;
      if (startStr != null && endStr != null) {
        final start = DateTime.tryParse(startStr)?.toLocal();
        final end = DateTime.tryParse(endStr)?.toLocal();
        if (start != null && end != null) {
          mainSleepTime = '${_formatTime24h(start)} - ${_formatTime24h(end)}';
        }
      }
    }

    if (_sleep != null && slp.score > 0) {
      cards.add(_buildMyDayCard(
        icon: Icons.bedtime_outlined,
        iconColor: WhoopTheme.sleepBlue,
        title: 'Sleep',
        score: '${slp.score.round()}%',
        subtitle: mainSleepTime ?? slp.hoursVsNeeded ?? '',
        onTap: () => context.push('/sleep/$_dateStr'),
      ));
    }

    for (final entry in _sleepEntries) {
      final isNap = entry['nap'] == true;
      if (!isNap) continue;
      cards.add(_buildNapCard(entry));
    }

    return cards;
  }

  Widget _buildNapCard(Map<String, dynamic> entry) {
    final score = entry['score'] as Map<String, dynamic>?;
    final napId = (entry['id'] ?? '').toString();

    final startStr = entry['start'] as String?;
    final endStr = entry['end'] as String?;
    String timeRange = '';
    if (startStr != null && endStr != null) {
      final start = DateTime.tryParse(startStr)?.toLocal();
      final end = DateTime.tryParse(endStr)?.toLocal();
      if (start != null && end != null) {
        timeRange = '${_formatTime24h(start)} - ${_formatTime24h(end)}';
      }
    }

    final sleepPerf = (score?['sleep_performance_percentage'] as num?)?.toDouble() ?? 0;

    return _buildMyDayCard(
      icon: Icons.airline_seat_flat_outlined,
      iconColor: WhoopTheme.sleepBlue,
      title: 'Nap',
      score: sleepPerf > 0 ? '${sleepPerf.round()}%' : '--',
      subtitle: timeRange,
      onTap: () {
        final api = context.read<ApiService>();
        api.cache.setPermanent('sleep_activity:$napId', entry);
        context.push('/sleep/$_dateStr?activityId=$napId');
      },
    );
  }

  Widget _buildMyDayCard({
    required IconData icon,
    required Color iconColor,
    required String title,
    required String score,
    required String subtitle,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: GlassCard(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Row(
            children: [
              Container(
                width: 36, height: 36,
                decoration: BoxDecoration(
                  color: iconColor.withOpacity(0.12),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Icon(icon, color: iconColor, size: 18),
              ),
              const SizedBox(width: 12),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: iconColor.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(score, style: TextStyle(color: iconColor, fontSize: 13, fontWeight: FontWeight.w700)),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w500)),
                    if (subtitle.isNotEmpty)
                      Text(subtitle, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right, color: WhoopTheme.textSecondary, size: 18),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildWorkoutCard(Map<String, dynamic> workout) {
    final score = workout['score'] as Map<String, dynamic>?;
    final strain = (score?['strain'] as num?)?.toDouble() ?? 0;
    final activityId = (workout['id'] ?? '').toString();
    final sportId = workout['sport_id'] as int? ?? 0;
    final name = SportTypes.name(sportId);

    final startStr = workout['start'] as String?;
    final endStr = workout['end'] as String?;
    String timeRange = '';
    if (startStr != null && endStr != null) {
      final start = DateTime.tryParse(startStr)?.toLocal();
      final end = DateTime.tryParse(endStr)?.toLocal();
      if (start != null && end != null) {
        timeRange = '${_formatTime24h(start)} - ${_formatTime24h(end)}';
      }
    }

    Color badgeColor;
    if (strain < 7) {
      badgeColor = WhoopTheme.sleepBlue;
    } else if (strain < 14) {
      badgeColor = WhoopTheme.warning;
    } else {
      badgeColor = WhoopTheme.error;
    }

    return _buildMyDayCard(
      icon: SportTypes.icon(sportId),
      iconColor: badgeColor,
      title: name,
      score: strain.toStringAsFixed(1),
      subtitle: timeRange,
      onTap: () {
        if (activityId.isNotEmpty) {
          final api = context.read<ApiService>();
          api.cache.setPermanent('workout:$activityId', workout);
          context.push('/activity/$activityId');
        }
      },
    );
  }


  Widget _buildActivityCard(Map<String, dynamic> activity) {
    final name = activity['name'] as String;
    final scoreStr = activity['score']?.toString() ?? '';
    final time = activity['time'] as String;
    final activityId = activity['id']?.toString() ?? '';

    final scoreNum = double.tryParse(scoreStr.replaceAll(RegExp(r'[^0-9.]'), '')) ?? 0;
    Color badgeColor;
    if (scoreNum < 7) {
      badgeColor = WhoopTheme.sleepBlue;
    } else if (scoreNum < 14) {
      badgeColor = WhoopTheme.warning;
    } else {
      badgeColor = WhoopTheme.error;
    }

    return _buildMyDayCard(
      icon: Icons.fitness_center,
      iconColor: badgeColor,
      title: name,
      score: scoreStr.isNotEmpty ? scoreStr : '--',
      subtitle: time,
      onTap: () {
        if (activityId.isNotEmpty) {
          final api = context.read<ApiService>();
          api.cache.setPermanent('workout:$activityId', activity);
          context.push('/activity/$activityId');
        }
      },
    );
  }
}

// === Rotating sync icon for the top bar ===
class _RotatingSyncIcon extends StatefulWidget {
  const _RotatingSyncIcon();

  @override
  State<_RotatingSyncIcon> createState() => _RotatingSyncIconState();
}

class _RotatingSyncIconState extends State<_RotatingSyncIcon>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 2),
    )..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RotationTransition(
      turns: _controller,
      child: const Icon(Icons.sync, color: WhoopTheme.primary, size: 14),
    );
  }
}
