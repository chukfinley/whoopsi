import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/theme.dart';
import '../services/auth_service.dart';
import '../services/weather_service.dart';
import '../services/ai_service.dart';

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _pageController = PageController();
  int _currentPage = 0;

  static const _totalPages = 3;

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  void _next() {
    if (_currentPage < _totalPages - 1) {
      _pageController.nextPage(duration: const Duration(milliseconds: 300), curve: Curves.easeInOut);
    } else {
      _finish();
    }
  }

  Future<void> _finish() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('onboarding_complete', true);
    if (mounted) context.go('/');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: WhoopTheme.background,
      body: SafeArea(
        child: Column(
          children: [
            // Skip button
            Align(
              alignment: Alignment.topRight,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: GestureDetector(
                  onTap: _finish,
                  child: const Text('Skip', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 14)),
                ),
              ),
            ),
            // Pages
            Expanded(
              child: PageView(
                controller: _pageController,
                onPageChanged: (i) => setState(() => _currentPage = i),
                children: [
                  _WelcomePage(onNext: _next),
                  _WeatherSetupPage(onNext: _next),
                  _AiSetupPage(onNext: _finish),
                ],
              ),
            ),
            // Dots
            Padding(
              padding: const EdgeInsets.only(bottom: 32),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: List.generate(_totalPages, (i) => Container(
                  margin: const EdgeInsets.symmetric(horizontal: 4),
                  width: i == _currentPage ? 24 : 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: i == _currentPage ? WhoopTheme.primary : WhoopTheme.divider,
                    borderRadius: BorderRadius.circular(4),
                  ),
                )),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _WelcomePage extends StatelessWidget {
  final VoidCallback onNext;
  const _WelcomePage({required this.onNext});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            width: 80, height: 80,
            decoration: BoxDecoration(
              color: WhoopTheme.primary.withOpacity(0.12),
              borderRadius: BorderRadius.circular(24),
            ),
            child: const Icon(Icons.favorite, color: WhoopTheme.primary, size: 40),
          ),
          const SizedBox(height: 32),
          const Text('Welcome to Open Whoop',
              style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 28, fontWeight: FontWeight.bold),
              textAlign: TextAlign.center),
          const SizedBox(height: 16),
          const Text(
            'Your open-source health companion.\nTrack recovery, sleep, strain, and more.',
            style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 16, height: 1.5),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 48),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: onNext,
              style: ElevatedButton.styleFrom(
                backgroundColor: WhoopTheme.primary,
                foregroundColor: WhoopTheme.background,
                padding: const EdgeInsets.symmetric(vertical: 16),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
              ),
              child: const Text('Get Started', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            ),
          ),
        ],
      ),
    );
  }
}

class _WeatherSetupPage extends StatefulWidget {
  final VoidCallback onNext;
  const _WeatherSetupPage({required this.onNext});

  @override
  State<_WeatherSetupPage> createState() => _WeatherSetupPageState();
}

class _WeatherSetupPageState extends State<_WeatherSetupPage> {
  final _searchController = TextEditingController();
  List<Map<String, dynamic>> _results = [];
  bool _searching = false;
  bool _set = false;

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _search(String query) async {
    if (query.length < 2) return;
    setState(() => _searching = true);
    final weather = context.read<WeatherService>();
    _results = await weather.searchCities(query);
    if (mounted) setState(() => _searching = false);
  }

  void _selectCity(Map<String, dynamic> city) {
    final lat = (city['latitude'] as num).toDouble();
    final lon = (city['longitude'] as num).toDouble();
    final name = city['name'] as String? ?? '';
    final country = city['country'] as String? ?? '';
    final label = country.isNotEmpty ? '$name, $country' : name;

    context.read<WeatherService>().setLocation(lat, lon, label);
    setState(() { _set = true; _results = []; });
    _searchController.clear();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.wb_sunny, color: WhoopTheme.warning, size: 48),
          const SizedBox(height: 24),
          const Text('Weather', style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 24, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          const Text('Set your location for weather context',
              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 14), textAlign: TextAlign.center),
          const SizedBox(height: 24),
          TextField(
            controller: _searchController,
            style: const TextStyle(color: WhoopTheme.textPrimary),
            decoration: InputDecoration(
              hintText: 'Search city...',
              hintStyle: const TextStyle(color: WhoopTheme.textSecondary),
              prefixIcon: const Icon(Icons.search, color: WhoopTheme.textSecondary),
              suffixIcon: _searching ? const SizedBox(width: 20, height: 20,
                  child: Padding(padding: EdgeInsets.all(12),
                      child: CircularProgressIndicator(color: WhoopTheme.primary, strokeWidth: 2))) : null,
            ),
            onChanged: _search,
          ),
          if (_results.isNotEmpty) ...[
            const SizedBox(height: 8),
            Container(
              constraints: const BoxConstraints(maxHeight: 200),
              decoration: BoxDecoration(
                color: WhoopTheme.surface,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: WhoopTheme.cardBorder),
              ),
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: _results.length,
                itemBuilder: (_, i) {
                  final city = _results[i];
                  final name = city['name'] ?? '';
                  final country = city['country'] ?? '';
                  return ListTile(
                    dense: true,
                    title: Text('$name, $country', style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14)),
                    onTap: () => _selectCity(city),
                  );
                },
              ),
            ),
          ],
          if (_set) ...[
            const SizedBox(height: 16),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.check_circle, color: WhoopTheme.primary, size: 20),
                const SizedBox(width: 8),
                Consumer<WeatherService>(
                  builder: (_, w, __) => Text(w.city,
                      style: const TextStyle(color: WhoopTheme.primary, fontSize: 14, fontWeight: FontWeight.w600)),
                ),
              ],
            ),
          ],
          const SizedBox(height: 32),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: widget.onNext,
              style: ElevatedButton.styleFrom(
                backgroundColor: WhoopTheme.primary,
                foregroundColor: WhoopTheme.background,
                padding: const EdgeInsets.symmetric(vertical: 16),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
              ),
              child: Text(_set ? 'Continue' : 'Skip', style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            ),
          ),
        ],
      ),
    );
  }
}

class _AiSetupPage extends StatefulWidget {
  final VoidCallback onNext;
  const _AiSetupPage({required this.onNext});

  @override
  State<_AiSetupPage> createState() => _AiSetupPageState();
}

class _AiSetupPageState extends State<_AiSetupPage> {
  final _keyController = TextEditingController();
  bool _saved = false;

  @override
  void dispose() {
    _keyController.dispose();
    super.dispose();
  }

  Future<void> _saveKey() async {
    if (_keyController.text.trim().isEmpty) return;
    final ai = context.read<AiService>();
    await ai.setApiKey(_keyController.text.trim());
    ai.setEnabled(true);
    setState(() => _saved = true);
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.auto_awesome, color: WhoopTheme.primary, size: 48),
          const SizedBox(height: 24),
          const Text('AI Insights', style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 24, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          const Text(
            'Get personalized insights powered by AI.\nProvide your OpenRouter API key (optional).',
            style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 14, height: 1.4),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 24),
          TextField(
            controller: _keyController,
            obscureText: true,
            style: const TextStyle(color: WhoopTheme.textPrimary),
            decoration: const InputDecoration(
              hintText: 'sk-or-...',
              hintStyle: TextStyle(color: WhoopTheme.textSecondary),
              prefixIcon: Icon(Icons.key, color: WhoopTheme.textSecondary),
            ),
          ),
          const SizedBox(height: 12),
          if (!_saved)
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: _saveKey,
                style: OutlinedButton.styleFrom(
                  foregroundColor: WhoopTheme.primary,
                  side: const BorderSide(color: WhoopTheme.primary),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                ),
                child: const Text('Save API Key'),
              ),
            ),
          if (_saved) ...[
            const Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.check_circle, color: WhoopTheme.primary, size: 20),
                SizedBox(width: 8),
                Text('API key saved', style: TextStyle(color: WhoopTheme.primary, fontSize: 14, fontWeight: FontWeight.w600)),
              ],
            ),
          ],
          const SizedBox(height: 32),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: widget.onNext,
              style: ElevatedButton.styleFrom(
                backgroundColor: WhoopTheme.primary,
                foregroundColor: WhoopTheme.background,
                padding: const EdgeInsets.symmetric(vertical: 16),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
              ),
              child: const Text('Finish Setup', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            ),
          ),
        ],
      ),
    );
  }
}
