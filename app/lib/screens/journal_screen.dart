import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/journal_entry.dart';
import '../models/recovery.dart';
import '../services/api_service.dart';
import '../services/journal_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class JournalScreen extends StatefulWidget {
  const JournalScreen({super.key});

  @override
  State<JournalScreen> createState() => _JournalScreenState();
}

class _JournalScreenState extends State<JournalScreen> {
  late String _selectedDate;
  final _notesController = TextEditingController();
  Set<String> _selectedBehaviors = {};
  int _stressLevel = 3;

  // Impact tab data
  List<BehaviorCorrelation>? _correlations;
  bool _correlationsLoading = false;

  @override
  void initState() {
    super.initState();
    _selectedDate = DateFormat('yyyy-MM-dd').format(DateTime.now());
    _loadEntry();
  }

  @override
  void dispose() {
    _notesController.dispose();
    super.dispose();
  }

  void _loadEntry() {
    final journal = context.read<JournalService>();
    final entry = journal.getEntry(_selectedDate);
    if (entry != null) {
      _selectedBehaviors = Set.from(entry.behaviors);
      _stressLevel = entry.stressLevel;
      _notesController.text = entry.notes;
    } else {
      _selectedBehaviors = {};
      _stressLevel = 3;
      _notesController.text = '';
    }
  }

  void _save() {
    final journal = context.read<JournalService>();
    journal.saveEntry(JournalEntry(
      date: _selectedDate,
      behaviors: _selectedBehaviors,
      stressLevel: _stressLevel,
      notes: _notesController.text,
    ));
    HapticFeedback.lightImpact();
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Journal entry saved'),
        backgroundColor: WhoopTheme.surface,
        duration: Duration(seconds: 2),
      ),
    );
  }

  void _loadCorrelations() {
    if (_correlationsLoading) return;
    setState(() => _correlationsLoading = true);
    final journal = context.read<JournalService>();
    final api = context.read<ApiService>();
    final recoveryMap = journal.buildRecoveryMap(api);
    final correlations = journal.computeAllCorrelations(recoveryMap);
    setState(() {
      _correlations = correlations;
      _correlationsLoading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 3,
      child: GradientScaffold(
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          title: const Text('Journal', style: TextStyle(fontWeight: FontWeight.bold)),
          elevation: 0,
          bottom: const TabBar(
            indicatorColor: WhoopTheme.primary,
            labelColor: WhoopTheme.primary,
            unselectedLabelColor: WhoopTheme.textSecondary,
            tabs: [
              Tab(text: 'Today'),
              Tab(text: 'Impact'),
              Tab(text: 'History'),
            ],
          ),
        ),
        body: SafeArea(
          child: TabBarView(
            children: [
              _buildEditor(),
              _buildImpactTab(),
              _buildHistory(),
            ],
          ),
        ),
      ),
    );
  }

  // ─── TODAY TAB (Editor) ────────────────────────────────────

  Widget _buildEditor() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // Date selector
        Center(
          child: GestureDetector(
            onTap: () async {
              final picked = await showDatePicker(
                context: context,
                initialDate: DateTime.parse(_selectedDate),
                firstDate: DateTime(2024),
                lastDate: DateTime.now(),
                builder: (ctx, child) => Theme(
                  data: Theme.of(ctx).copyWith(
                    colorScheme: const ColorScheme.dark(
                      primary: WhoopTheme.primary,
                      surface: WhoopTheme.surface,
                    ),
                  ),
                  child: child!,
                ),
              );
              if (picked != null) {
                setState(() {
                  _selectedDate = DateFormat('yyyy-MM-dd').format(picked);
                  _loadEntry();
                });
              }
            },
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  _selectedDate == DateFormat('yyyy-MM-dd').format(DateTime.now())
                      ? 'TODAY'
                      : DateFormat('MMM d, yyyy').format(DateTime.parse(_selectedDate)),
                  style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13,
                      fontWeight: FontWeight.w600, letterSpacing: 0.5),
                ),
                const SizedBox(width: 6),
                const Icon(Icons.calendar_today, color: WhoopTheme.textSecondary, size: 14),
              ],
            ),
          ),
        ),
        const SizedBox(height: 20),

        // Behaviors by category
        ...JournalEntry.behaviorCategories.entries.map((category) {
          final catName = category.key;
          final behaviors = category.value;
          final selectedCount = behaviors.where((b) => _selectedBehaviors.contains(b)).length;
          final catIcon = JournalEntry.categoryIcons[catName] ?? Icons.label;

          return Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: ExpansionTile(
              leading: Icon(catIcon, color: WhoopTheme.textSecondary, size: 20),
              title: Row(
                children: [
                  Text(catName.toUpperCase(),
                      style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                          fontWeight: FontWeight.w600, letterSpacing: 0.5)),
                  if (selectedCount > 0) ...[
                    const SizedBox(width: 8),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: WhoopTheme.primary.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text('$selectedCount',
                          style: const TextStyle(color: WhoopTheme.primary, fontSize: 11, fontWeight: FontWeight.w600)),
                    ),
                  ],
                ],
              ),
              tilePadding: EdgeInsets.zero,
              childrenPadding: const EdgeInsets.only(bottom: 8),
              initiallyExpanded: selectedCount > 0,
              iconColor: WhoopTheme.textSecondary,
              collapsedIconColor: WhoopTheme.textSecondary,
              children: [
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: behaviors.map((b) {
                    final selected = _selectedBehaviors.contains(b);
                    final icon = JournalEntry.behaviorIcons[b];
                    return GestureDetector(
                      onTap: () {
                        setState(() {
                          if (selected) {
                            _selectedBehaviors.remove(b);
                          } else {
                            _selectedBehaviors.add(b);
                          }
                        });
                      },
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                        decoration: BoxDecoration(
                          color: selected ? WhoopTheme.primary.withValues(alpha: 0.15) : WhoopTheme.surface,
                          borderRadius: BorderRadius.circular(20),
                          border: Border.all(
                            color: selected ? WhoopTheme.primary : WhoopTheme.cardBorder,
                            width: selected ? 1.5 : 0.5,
                          ),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            if (icon != null) ...[
                              Icon(icon, size: 14,
                                  color: selected ? WhoopTheme.primary : WhoopTheme.textSecondary),
                              const SizedBox(width: 6),
                            ],
                            Text(b,
                              style: TextStyle(
                                color: selected ? WhoopTheme.primary : WhoopTheme.textPrimary,
                                fontSize: 13,
                                fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                              ),
                            ),
                          ],
                        ),
                      ),
                    );
                  }).toList(),
                ),
              ],
            ),
          );
        }),

        const SizedBox(height: 16),

        // Stress level
        const Text('STRESS LEVEL', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
            fontWeight: FontWeight.w600, letterSpacing: 0.5)),
        const SizedBox(height: 10),
        GlassCard(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          child: Column(
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text('Low', style: TextStyle(color: WhoopTheme.recoveryGreen, fontSize: 12)),
                  Text('$_stressLevel / 5',
                      style: TextStyle(
                        color: _stressColor(_stressLevel),
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                      )),
                  const Text('High', style: TextStyle(color: WhoopTheme.error, fontSize: 12)),
                ],
              ),
              Slider(
                value: _stressLevel.toDouble(),
                min: 1,
                max: 5,
                divisions: 4,
                activeColor: _stressColor(_stressLevel),
                inactiveColor: WhoopTheme.divider,
                onChanged: (v) => setState(() => _stressLevel = v.round()),
              ),
            ],
          ),
        ),
        const SizedBox(height: 24),

        // Notes
        const Text('NOTES', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
            fontWeight: FontWeight.w600, letterSpacing: 0.5)),
        const SizedBox(height: 10),
        TextField(
          controller: _notesController,
          maxLines: 4,
          style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14),
          decoration: InputDecoration(
            hintText: 'How are you feeling today?',
            hintStyle: const TextStyle(color: WhoopTheme.textSecondary),
            filled: true,
            fillColor: WhoopTheme.surface,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: WhoopTheme.cardBorder),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: WhoopTheme.cardBorder),
            ),
          ),
        ),
        const SizedBox(height: 24),

        // Save button
        SizedBox(
          width: double.infinity,
          child: ElevatedButton(
            onPressed: _save,
            style: ElevatedButton.styleFrom(
              backgroundColor: WhoopTheme.primary,
              foregroundColor: WhoopTheme.background,
              padding: const EdgeInsets.symmetric(vertical: 14),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            ),
            child: const Text('Save Entry', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
          ),
        ),
        const SizedBox(height: 80),
      ],
    );
  }

  // ─── IMPACT TAB ────────────────────────────────────────────

  Widget _buildImpactTab() {
    final journal = context.watch<JournalService>();
    final count = journal.entryCount;

    if (count < 7) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.insights, size: 48, color: WhoopTheme.primary.withValues(alpha: 0.3)),
              const SizedBox(height: 16),
              const Text('Keep Logging!',
                  style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              Text(
                'You need at least 7 journal entries to see behavior correlations.\nYou have $count so far.',
                textAlign: TextAlign.center,
                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 14),
              ),
              const SizedBox(height: 24),
              LinearProgressIndicator(
                value: count / 7,
                backgroundColor: WhoopTheme.divider,
                valueColor: const AlwaysStoppedAnimation(WhoopTheme.primary),
                borderRadius: BorderRadius.circular(4),
              ),
              const SizedBox(height: 8),
              Text('$count / 7 entries',
                  style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
            ],
          ),
        ),
      );
    }

    // Load correlations on first view
    if (_correlations == null && !_correlationsLoading) {
      Future.microtask(() => _loadCorrelations());
    }

    if (_correlationsLoading) {
      return const Center(child: CircularProgressIndicator(color: WhoopTheme.primary));
    }

    final correlations = _correlations ?? [];

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // Header
        Text('Based on ${journal.entryCount} journal entries',
            style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
        const SizedBox(height: 16),

        if (correlations.isEmpty) ...[
          GlassCard(
            padding: const EdgeInsets.all(20),
            child: Column(
              children: [
                const Icon(Icons.info_outline, color: WhoopTheme.textSecondary, size: 32),
                const SizedBox(height: 12),
                const Text(
                  'Not enough data for correlations yet.\nEach behavior needs at least 3 entries with and without it.\nPrefetch recovery data in Settings to populate.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 13, height: 1.4),
                ),
              ],
            ),
          ),
        ] else ...[
          const Text('BIGGEST IMPACT ON RECOVERY',
              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                  fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          const SizedBox(height: 12),
          ...correlations.map((c) => _buildCorrelationCard(c)),
        ],
        const SizedBox(height: 80),
      ],
    );
  }

  Widget _buildCorrelationCard(BehaviorCorrelation c) {
    final positive = c.diff >= 0;
    final impactColor = positive ? WhoopTheme.recoveryGreen : WhoopTheme.error;
    final icon = JournalEntry.behaviorIcons[c.behavior] ?? Icons.label;
    final maxVal = [c.avgWith, c.avgWithout].reduce((a, b) => a > b ? a : b);
    final barScale = maxVal > 0 ? 1.0 / maxVal : 1.0;

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header row
            Row(
              children: [
                Icon(icon, color: WhoopTheme.textSecondary, size: 18),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(c.behavior,
                      style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: impactColor.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(
                    '${positive ? '+' : ''}${c.diff.round()}%',
                    style: TextStyle(color: impactColor, fontSize: 13, fontWeight: FontWeight.w700),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // Bar: recovery WITH behavior
            _correlationBar('With', c.avgWith, barScale, WhoopTheme.primary),
            const SizedBox(height: 6),

            // Bar: recovery WITHOUT behavior
            _correlationBar('Without', c.avgWithout, barScale, WhoopTheme.textSecondary),
          ],
        ),
      ),
    );
  }

  Widget _correlationBar(String label, double value, double scale, Color color) {
    final width = (value * scale).clamp(0.0, 1.0);
    return Row(
      children: [
        SizedBox(
          width: 56,
          child: Text(label,
              style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
        ),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: SizedBox(
              height: 16,
              child: Stack(
                children: [
                  Container(color: WhoopTheme.divider.withValues(alpha: 0.2)),
                  FractionallySizedBox(
                    widthFactor: width,
                    child: Container(
                      decoration: BoxDecoration(
                        color: color.withValues(alpha: 0.4),
                        borderRadius: BorderRadius.circular(3),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(width: 8),
        SizedBox(
          width: 36,
          child: Text('${value.round()}%',
              textAlign: TextAlign.right,
              style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
        ),
      ],
    );
  }

  // ─── HISTORY TAB ───────────────────────────────────────────

  Widget _buildHistory() {
    final journal = context.watch<JournalService>();
    final entries = journal.allEntries;
    final api = context.read<ApiService>();

    if (entries.isEmpty) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.book_outlined, color: WhoopTheme.textSecondary, size: 48),
            SizedBox(height: 16),
            Text('No journal entries yet', style: TextStyle(color: WhoopTheme.textSecondary)),
          ],
        ),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: entries.length,
      itemBuilder: (context, index) {
        final entry = entries[index];
        final isToday = entry.date == DateFormat('yyyy-MM-dd').format(DateTime.now());

        // Try to get recovery score from cache
        int? recScore;
        try {
          final cached = api.cache.get<Map<String, dynamic>>('deep_dive:recovery:${entry.date}');
          if (cached != null) {
            final rec = Recovery.fromDeepDive(cached);
            recScore = rec.score.round();
          }
        } catch (_) {}

        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: GestureDetector(
            onTap: () {
              setState(() {
                _selectedDate = entry.date;
                _loadEntry();
              });
              DefaultTabController.of(context).animateTo(0);
            },
            child: GlassCard(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Text(
                        isToday ? 'Today' : DateFormat('MMM d').format(DateTime.parse(entry.date)),
                        style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600),
                      ),
                      const Spacer(),
                      if (recScore != null) ...[
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: _recoveryColor(recScore).withValues(alpha: 0.12),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Text('$recScore%',
                              style: TextStyle(color: _recoveryColor(recScore), fontSize: 11, fontWeight: FontWeight.w600)),
                        ),
                        const SizedBox(width: 6),
                      ],
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: _stressColor(entry.stressLevel).withValues(alpha: 0.12),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          'Stress ${entry.stressLevel}/5',
                          style: TextStyle(color: _stressColor(entry.stressLevel), fontSize: 11, fontWeight: FontWeight.w600),
                        ),
                      ),
                    ],
                  ),
                  if (entry.behaviors.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: entry.behaviors.map((b) => Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: WhoopTheme.primary.withValues(alpha: 0.08),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(b, style: const TextStyle(color: WhoopTheme.primary, fontSize: 11)),
                      )).toList(),
                    ),
                  ],
                  if (entry.notes.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(entry.notes, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12),
                        maxLines: 2, overflow: TextOverflow.ellipsis),
                  ],
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  // ─── HELPERS ───────────────────────────────────────────────

  Color _stressColor(int level) {
    if (level <= 2) return WhoopTheme.recoveryGreen;
    if (level <= 3) return WhoopTheme.warning;
    return WhoopTheme.error;
  }

  Color _recoveryColor(int score) {
    if (score >= 67) return WhoopTheme.recoveryGreen;
    if (score >= 34) return WhoopTheme.warning;
    return WhoopTheme.error;
  }
}
