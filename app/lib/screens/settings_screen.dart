import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:file_picker/file_picker.dart';

import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/theme.dart';
import '../services/auth_service.dart';
import '../services/api_service.dart';
import '../services/ai_service.dart';
import '../services/hydration_service.dart';
import '../services/prefetch_service.dart';
import '../services/weather_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  Map<String, dynamic>? _profile;
  bool _loading = true;
  bool _resumeLastDate = false;

  @override
  void initState() {
    super.initState();
    _fetchProfile();
    _loadStartupSetting();
  }

  Future<void> _loadStartupSetting() async {
    final prefs = await SharedPreferences.getInstance();
    if (mounted) {
      setState(() => _resumeLastDate = prefs.getBool('startup_resume_last_date') ?? false);
    }
  }

  Future<void> _fetchProfile() async {
    final api = context.read<ApiService>();
    // Load cached immediately
    final cached = api.cache.get<Map<String, dynamic>>('profile');
    if (cached != null && mounted) {
      setState(() { _profile = cached; _loading = false; });
    }
    // Try to refresh in background
    try {
      final profile = await api.getProfile(forceRefresh: cached != null);
      if (mounted) setState(() { _profile = profile; _loading = false; });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _logout() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: WhoopTheme.surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Sign Out', style: TextStyle(color: WhoopTheme.textPrimary)),
        content: const Text('Are you sure?', style: TextStyle(color: WhoopTheme.textSecondary)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Sign Out', style: TextStyle(color: WhoopTheme.error))),
        ],
      ),
    );
    if (confirmed == true && mounted) {
      await context.read<AuthService>().logout();
      if (mounted) context.go('/login');
    }
  }

  void _startPrefetch(String label, int days) {
    final prefetch = context.read<PrefetchService>();
    if (prefetch.active) return;
    prefetch.prefetchRange(label, days);
  }

  Future<void> _exportData() async {
    final api = context.read<ApiService>();
    final allData = api.cache.exportAll();
    if (allData.isEmpty) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('No cached data to export'), backgroundColor: WhoopTheme.surface),
        );
      }
      return;
    }

    final selectedDir = await FilePicker.platform.getDirectoryPath(
      dialogTitle: 'Choose export folder',
    );
    if (selectedDir == null) return;

    try {
      final timestamp = DateFormat('yyyyMMdd_HHmmss').format(DateTime.now());
      final exportDir = Directory('$selectedDir/open_whoop_export_$timestamp');
      exportDir.createSync(recursive: true);

      for (final entry in allData.entries) {
        File('${exportDir.path}/${entry.key}').writeAsStringSync(entry.value);
      }

      final summary = {
        'exported_at': DateTime.now().toIso8601String(),
        'file_count': allData.length,
        'files': allData.keys.toList(),
      };
      File('${exportDir.path}/_summary.json').writeAsStringSync(
        const JsonEncoder.withIndent('  ').convert(summary),
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Exported ${allData.length} files to $selectedDir'),
            backgroundColor: WhoopTheme.surface,
            duration: const Duration(seconds: 4),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Export failed: $e'), backgroundColor: WhoopTheme.error),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final firstName = _profile?['first_name'] ?? '';
    final lastName = _profile?['last_name'] ?? '';
    final email = _profile?['email'] ?? '';
    final name = '$firstName $lastName'.trim();
    final api = context.read<ApiService>();
    final cacheCount = api.cache.cachedFileCount;
    final cacheSize = api.cache.cachedSizeBytes;
    final cacheSizeMb = (cacheSize / 1024 / 1024).toStringAsFixed(1);

    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Settings', style: TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // Profile card
            GlassCard(
              padding: const EdgeInsets.all(20),
              child: _loading
                  ? const Center(child: Padding(padding: EdgeInsets.all(16),
                      child: CircularProgressIndicator(color: WhoopTheme.primary, strokeWidth: 2)))
                  : Row(
                      children: [
                        Container(
                          width: 48, height: 48,
                          decoration: BoxDecoration(
                            color: WhoopTheme.primary.withValues(alpha: 0.15),
                            borderRadius: BorderRadius.circular(14),
                          ),
                          child: Center(child: Text(
                            name.isNotEmpty ? name[0].toUpperCase() : '?',
                            style: const TextStyle(color: WhoopTheme.primary, fontSize: 22, fontWeight: FontWeight.bold),
                          )),
                        ),
                        const SizedBox(width: 14),
                        Expanded(child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(name.isNotEmpty ? name : 'User',
                                style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 17, fontWeight: FontWeight.w600)),
                            if (email.toString().isNotEmpty)
                              Padding(padding: const EdgeInsets.only(top: 4),
                                  child: Text(email.toString(), style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13))),
                          ],
                        )),
                      ],
                    ),
            ),
            const SizedBox(height: 24),

            // === APP BEHAVIOR ===
            const Text('APP BEHAVIOR', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                fontWeight: FontWeight.w600, letterSpacing: 0.5)),
            const SizedBox(height: 10),
            _buildStartupSetting(),
            const SizedBox(height: 24),

            // === WEATHER LOCATION ===
            const Text('WEATHER LOCATION', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                fontWeight: FontWeight.w600, letterSpacing: 0.5)),
            const SizedBox(height: 10),
            _buildWeatherLocationSetting(),
            const SizedBox(height: 24),

            // === AI INSIGHTS ===
            const Text('AI INSIGHTS', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                fontWeight: FontWeight.w600, letterSpacing: 0.5)),
            const SizedBox(height: 10),
            _buildAiSettings(),
            const SizedBox(height: 24),

            // === HYDRATION ===
            const Text('HYDRATION', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                fontWeight: FontWeight.w600, letterSpacing: 0.5)),
            const SizedBox(height: 10),
            _buildHydrationSettings(),
            const SizedBox(height: 24),

            // === SYNC DATA ===
            const Text('SYNC DATA', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                fontWeight: FontWeight.w600, letterSpacing: 0.5)),
            const SizedBox(height: 10),
            Consumer<PrefetchService>(
              builder: (context, prefetch, _) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        _syncButton('This Week', 7, prefetch.active),
                        _syncButton('2 Weeks', 14, prefetch.active),
                        _syncButton('1 Month', 30, prefetch.active),
                        _syncButton('2 Months', 60, prefetch.active),
                        _syncButton('3 Months', 90, prefetch.active),
                        _syncButton('6 Months', 180, prefetch.active),
                        _syncButton('All Data', 365, prefetch.active),
                      ],
                    ),
                    if (prefetch.status != null) ...[
                      const SizedBox(height: 12),
                      if (prefetch.active)
                        ClipRRect(
                          borderRadius: BorderRadius.circular(4),
                          child: LinearProgressIndicator(
                            value: prefetch.progress,
                            backgroundColor: WhoopTheme.divider,
                            color: WhoopTheme.primary,
                            minHeight: 4,
                          ),
                        ),
                      const SizedBox(height: 6),
                      Row(
                        children: [
                          Expanded(
                            child: Text(prefetch.status!,
                                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                          ),
                          if (prefetch.active)
                            GestureDetector(
                              onTap: prefetch.cancel,
                              child: const Padding(
                                padding: EdgeInsets.only(left: 8),
                                child: Icon(Icons.close, color: WhoopTheme.error, size: 18),
                              ),
                            ),
                        ],
                      ),
                    ],
                  ],
                );
              },
            ),
            const SizedBox(height: 8),
            Text('$cacheCount files cached ($cacheSizeMb MB)',
                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
            const SizedBox(height: 24),

            // === ACTIONS ===
            _tile(icon: Icons.sensors, label: 'Sensor Data', onTap: () => context.push('/sensor-data')),
            _tile(icon: Icons.file_download_outlined, label: 'Export All Data', onTap: _exportData),
            _tile(
              icon: Icons.delete_outline,
              label: 'Clear Cache',
              onTap: () {
                api.cache.clear();
                setState(() {});
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Cache cleared'), backgroundColor: WhoopTheme.surface),
                );
              },
            ),
            _tile(icon: Icons.logout, label: 'Sign Out', color: WhoopTheme.error, onTap: _logout),
            const SizedBox(height: 32),
            const Center(child: Text('Open Whoop v1.1.0',
                style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12))),
          ],
        ),
      ),
    );
  }

  // ─── Startup Behavior ────────────────────────────────────────

  Widget _buildStartupSetting() {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Row(
        children: [
          const Icon(Icons.today, color: WhoopTheme.textSecondary, size: 20),
          const SizedBox(width: 10),
          const Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Resume last date', style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14)),
                SizedBox(height: 2),
                Text('Open app on last viewed day instead of today',
                    style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
              ],
            ),
          ),
          Switch(
            value: _resumeLastDate,
            onChanged: (v) async {
              final prefs = await SharedPreferences.getInstance();
              await prefs.setBool('startup_resume_last_date', v);
              setState(() => _resumeLastDate = v);
            },
            activeColor: WhoopTheme.primary,
          ),
        ],
      ),
    );
  }

  // ─── Weather Location ─────────────────────────────────────────

  Widget _buildWeatherLocationSetting() {
    return Consumer<WeatherService>(
      builder: (context, weather, _) {
        return GlassCard(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.location_on, color: WhoopTheme.textSecondary, size: 20),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      weather.city.isNotEmpty ? weather.city : 'No location set',
                      style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14),
                    ),
                  ),
                  GestureDetector(
                    onTap: () => _showCitySearchDialog(),
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                      decoration: BoxDecoration(
                        color: WhoopTheme.primary.withOpacity(0.12),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        weather.city.isNotEmpty ? 'Change' : 'Set Location',
                        style: const TextStyle(color: WhoopTheme.primary, fontSize: 12, fontWeight: FontWeight.w600),
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

  Future<void> _showCitySearchDialog() async {
    final weather = context.read<WeatherService>();
    final controller = TextEditingController();
    List<Map<String, dynamic>> results = [];

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          backgroundColor: WhoopTheme.surface,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: const Text('Search City', style: TextStyle(color: WhoopTheme.textPrimary)),
          content: SizedBox(
            width: 300,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: controller,
                  style: const TextStyle(color: WhoopTheme.textPrimary),
                  decoration: const InputDecoration(
                    hintText: 'City name...',
                    hintStyle: TextStyle(color: WhoopTheme.textSecondary),
                    prefixIcon: Icon(Icons.search, color: WhoopTheme.textSecondary),
                  ),
                  onChanged: (q) async {
                    final r = await weather.searchCities(q);
                    setDialogState(() => results = r);
                  },
                ),
                if (results.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 200),
                    child: ListView.builder(
                      shrinkWrap: true,
                      itemCount: results.length,
                      itemBuilder: (_, i) {
                        final city = results[i];
                        return ListTile(
                          dense: true,
                          title: Text(
                            '${city['name']}, ${city['country'] ?? ''}',
                            style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14),
                          ),
                          onTap: () {
                            final lat = (city['latitude'] as num).toDouble();
                            final lon = (city['longitude'] as num).toDouble();
                            final name = city['name'] as String? ?? '';
                            final country = city['country'] as String? ?? '';
                            final label = country.isNotEmpty ? '$name, $country' : name;
                            weather.setLocation(lat, lon, label);
                            Navigator.pop(ctx);
                          },
                        );
                      },
                    ),
                  ),
                ],
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel', style: TextStyle(color: WhoopTheme.textSecondary)),
            ),
          ],
        ),
      ),
    );
  }

  // ─── AI Settings ──────────────────────────────────────────────

  Widget _buildAiSettings() {
    return Consumer<AiService>(
      builder: (context, ai, _) {
        return GlassCard(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.auto_awesome, color: WhoopTheme.primary, size: 20),
                  const SizedBox(width: 10),
                  const Expanded(child: Text('Enable AI Insights',
                      style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
                  Switch(
                    value: ai.enabled,
                    onChanged: ai.hasApiKey ? (v) => ai.setEnabled(v) : null,
                    activeColor: WhoopTheme.primary,
                  ),
                ],
              ),
              const SizedBox(height: 12),
              GestureDetector(
                onTap: () => _showApiKeyDialog(),
                child: Row(
                  children: [
                    const Icon(Icons.key, color: WhoopTheme.textSecondary, size: 18),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        ai.hasApiKey ? 'API key set' : 'Set OpenRouter API key',
                        style: TextStyle(color: ai.hasApiKey ? WhoopTheme.primary : WhoopTheme.textSecondary, fontSize: 13),
                      ),
                    ),
                    const Icon(Icons.chevron_right, color: WhoopTheme.textSecondary, size: 18),
                  ],
                ),
              ),
              const SizedBox(height: 10),
              GestureDetector(
                onTap: () => _showModelPicker(),
                child: Row(
                  children: [
                    const Icon(Icons.smart_toy, color: WhoopTheme.textSecondary, size: 18),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text('Model: ${ai.model.split('/').last}',
                          style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
                    ),
                    const Icon(Icons.chevron_right, color: WhoopTheme.textSecondary, size: 18),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Future<void> _showApiKeyDialog() async {
    final ai = context.read<AiService>();
    final controller = TextEditingController();
    bool obscure = true;
    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          backgroundColor: WhoopTheme.surface,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: const Text('OpenRouter API Key', style: TextStyle(color: WhoopTheme.textPrimary)),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Get your API key at openrouter.ai/keys',
                  style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
              const SizedBox(height: 12),
              TextField(
                controller: controller,
                obscureText: obscure,
                style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14),
                decoration: InputDecoration(
                  hintText: 'sk-or-v1-...',
                  hintStyle: const TextStyle(color: WhoopTheme.textSecondary),
                  prefixIcon: const Icon(Icons.key, color: WhoopTheme.textSecondary, size: 20),
                  suffixIcon: GestureDetector(
                    onTap: () => setDialogState(() => obscure = !obscure),
                    child: Icon(obscure ? Icons.visibility_off : Icons.visibility,
                        color: WhoopTheme.textSecondary, size: 20),
                  ),
                ),
              ),
              if (ai.hasApiKey) ...[
                const SizedBox(height: 8),
                const Row(
                  children: [
                    Icon(Icons.check_circle, color: WhoopTheme.primary, size: 14),
                    SizedBox(width: 6),
                    Text('Key already set', style: TextStyle(color: WhoopTheme.primary, fontSize: 12)),
                  ],
                ),
              ],
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx),
                child: const Text('Cancel', style: TextStyle(color: WhoopTheme.textSecondary))),
            ElevatedButton(
              onPressed: () async {
                if (controller.text.trim().isNotEmpty) {
                  await ai.setApiKey(controller.text.trim());
                  ai.setEnabled(true);
                }
                if (ctx.mounted) Navigator.pop(ctx);
              },
              style: ElevatedButton.styleFrom(
                backgroundColor: WhoopTheme.primary,
                foregroundColor: WhoopTheme.background,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
              ),
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _showModelPicker() async {
    final ai = context.read<AiService>();
    // Fetch models if we don't have them
    if (ai.availableModels.isEmpty) {
      ai.fetchModels();
    }
    final searchController = TextEditingController();
    String searchQuery = '';

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) {
          // Listen to model loading
          final models = ai.availableModels
              .where((m) => searchQuery.isEmpty ||
                  m.name.toLowerCase().contains(searchQuery.toLowerCase()) ||
                  m.id.toLowerCase().contains(searchQuery.toLowerCase()))
              .take(50)
              .toList();

          return AlertDialog(
            backgroundColor: WhoopTheme.surface,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
            title: const Text('Select Model', style: TextStyle(color: WhoopTheme.textPrimary)),
            content: SizedBox(
              width: 340,
              height: 400,
              child: Column(
                children: [
                  TextField(
                    controller: searchController,
                    style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14),
                    decoration: InputDecoration(
                      hintText: 'Search models...',
                      hintStyle: const TextStyle(color: WhoopTheme.textSecondary),
                      prefixIcon: const Icon(Icons.search, color: WhoopTheme.textSecondary, size: 20),
                      suffixIcon: ai.loadingModels
                          ? const Padding(padding: EdgeInsets.all(12),
                              child: SizedBox(width: 16, height: 16,
                                  child: CircularProgressIndicator(color: WhoopTheme.primary, strokeWidth: 2)))
                          : GestureDetector(
                              onTap: () { ai.fetchModels(); setDialogState(() {}); },
                              child: const Icon(Icons.refresh, color: WhoopTheme.textSecondary, size: 20),
                            ),
                    ),
                    onChanged: (q) => setDialogState(() => searchQuery = q),
                  ),
                  const SizedBox(height: 8),
                  Text('${ai.availableModels.length} models available',
                      style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
                  const SizedBox(height: 4),
                  Expanded(
                    child: models.isEmpty && ai.loadingModels
                        ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
                        : ListView.builder(
                            itemCount: models.length,
                            itemBuilder: (_, i) {
                              final model = models[i];
                              final selected = model.id == ai.model;
                              return ListTile(
                                dense: true,
                                contentPadding: const EdgeInsets.symmetric(horizontal: 4),
                                title: Text(model.name,
                                    style: TextStyle(
                                      color: selected ? WhoopTheme.primary : WhoopTheme.textPrimary,
                                      fontSize: 13,
                                      fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                                    ),
                                    maxLines: 1, overflow: TextOverflow.ellipsis),
                                subtitle: Text(model.provider,
                                    style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
                                trailing: selected
                                    ? const Icon(Icons.check, color: WhoopTheme.primary, size: 18)
                                    : null,
                                onTap: () {
                                  ai.setModel(model.id);
                                  Navigator.pop(ctx);
                                },
                              );
                            },
                          ),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('Cancel', style: TextStyle(color: WhoopTheme.textSecondary)),
              ),
            ],
          );
        },
      ),
    );
  }

  // ─── Hydration Settings ───────────────────────────────────────

  Widget _buildHydrationSettings() {
    return Consumer<HydrationService>(
      builder: (context, hydration, _) {
        return GlassCard(
          padding: const EdgeInsets.all(16),
          child: Column(
            children: [
              Row(
                children: [
                  const Icon(Icons.water_drop, color: WhoopTheme.sleepBlue, size: 20),
                  const SizedBox(width: 10),
                  const Expanded(child: Text('Daily Goal',
                      style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
                  Text('${hydration.goalMl} ml',
                      style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
                ],
              ),
              Slider(
                value: hydration.goalMl.toDouble(),
                min: 1000,
                max: 5000,
                divisions: 16,
                activeColor: WhoopTheme.sleepBlue,
                inactiveColor: WhoopTheme.divider,
                onChanged: (v) => hydration.setGoal(v.round()),
              ),
              Row(
                children: [
                  const Icon(Icons.local_drink, color: WhoopTheme.textSecondary, size: 18),
                  const SizedBox(width: 10),
                  const Expanded(child: Text('Glass Size',
                      style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
                  Text('${hydration.glassSizeMl} ml',
                      style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
                ],
              ),
              Slider(
                value: hydration.glassSizeMl.toDouble(),
                min: 100,
                max: 500,
                divisions: 8,
                activeColor: WhoopTheme.sleepBlue,
                inactiveColor: WhoopTheme.divider,
                onChanged: (v) => hydration.setGlassSize(v.round()),
              ),
            ],
          ),
        );
      },
    );
  }

  // ─── Reusable widgets ─────────────────────────────────────────

  Widget _syncButton(String label, int days, bool syncing) {
    return GestureDetector(
      onTap: syncing ? null : () => _startPrefetch(label, days),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        decoration: BoxDecoration(
          color: syncing ? WhoopTheme.divider : WhoopTheme.surface,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: WhoopTheme.divider, width: 0.5),
        ),
        child: Text(label, style: TextStyle(
          color: syncing ? WhoopTheme.textSecondary : WhoopTheme.textPrimary,
          fontSize: 13,
          fontWeight: FontWeight.w500,
        )),
      ),
    );
  }

  Widget _tile({
    required IconData icon,
    required String label,
    required VoidCallback onTap,
    Color color = WhoopTheme.textPrimary,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: GestureDetector(
        onTap: onTap,
        child: GlassCard(
          radius: 14,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
          child: Row(
            children: [
              Icon(icon, color: color, size: 22),
              const SizedBox(width: 14),
              Expanded(child: Text(label, style: TextStyle(color: color, fontSize: 15, fontWeight: FontWeight.w500))),
              Icon(Icons.chevron_right, color: color.withValues(alpha: 0.4), size: 20),
            ],
          ),
        ),
      ),
    );
  }
}
