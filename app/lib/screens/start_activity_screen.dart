import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/sport_types.dart';
import '../services/activity_tracker_service.dart';
import '../widgets/gradient_scaffold.dart';

class StartActivityScreen extends StatefulWidget {
  const StartActivityScreen({super.key});

  @override
  State<StartActivityScreen> createState() => _StartActivityScreenState();
}

class _StartActivityScreenState extends State<StartActivityScreen> {
  final _searchController = TextEditingController();
  String _query = '';

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final results = SportTypes.search(_query);

    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Start Activity', style: TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: Column(
        children: [
          // Search field
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
            child: TextField(
              controller: _searchController,
              onChanged: (v) => setState(() => _query = v),
              style: const TextStyle(color: WhoopTheme.textPrimary),
              decoration: InputDecoration(
                hintText: 'Search activity type...',
                hintStyle: const TextStyle(color: WhoopTheme.textSecondary),
                prefixIcon: const Icon(Icons.search, color: WhoopTheme.textSecondary),
                suffixIcon: _query.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear, color: WhoopTheme.textSecondary, size: 18),
                        onPressed: () {
                          _searchController.clear();
                          setState(() => _query = '');
                        },
                      )
                    : null,
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
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: const BorderSide(color: WhoopTheme.primary),
                ),
                contentPadding: const EdgeInsets.symmetric(vertical: 12),
              ),
            ),
          ),

          // Results grid
          Expanded(
            child: results.isEmpty
                ? const Center(
                    child: Text('No matching activities',
                        style: TextStyle(color: WhoopTheme.textSecondary)),
                  )
                : GridView.builder(
                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 80),
                    gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 3,
                      childAspectRatio: 1.0,
                      crossAxisSpacing: 10,
                      mainAxisSpacing: 10,
                    ),
                    itemCount: results.length,
                    itemBuilder: (context, index) {
                      final entry = results[index];
                      return _buildSportTile(entry.key, entry.value);
                    },
                  ),
          ),
        ],
      ),
    );
  }

  Widget _buildSportTile(int sportId, String name) {
    return GestureDetector(
      onTap: () => _startActivity(sportId, name),
      child: Container(
        decoration: BoxDecoration(
          color: WhoopTheme.surface,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: WhoopTheme.cardBorder),
        ),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(SportTypes.icon(sportId), color: WhoopTheme.primary, size: 28),
            const SizedBox(height: 8),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6),
              child: Text(
                name,
                style: const TextStyle(
                  color: WhoopTheme.textPrimary,
                  fontSize: 11,
                  fontWeight: FontWeight.w500,
                ),
                textAlign: TextAlign.center,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _startActivity(int sportId, String name) {
    final tracker = context.read<ActivityTrackerService>();
    tracker.startActivity(sportId, name);
    context.go('/activity-timer');
  }
}
