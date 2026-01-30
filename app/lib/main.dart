import 'package:dynamic_color/dynamic_color.dart';
import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:provider/provider.dart';

import 'core/router.dart';
import 'core/theme.dart';
import 'services/auth_service.dart';
import 'services/api_service.dart';
import 'services/ble_service.dart';
import 'services/storage_service.dart';
import 'services/sensor_db_service.dart';
import 'services/analysis_service.dart';
import 'services/upload_service.dart';
import 'services/sync_orchestrator.dart';
import 'services/activity_tracker_service.dart';
import 'services/hydration_service.dart';
import 'services/journal_service.dart';
import 'services/weather_service.dart';
import 'services/ai_service.dart';
import 'services/prefetch_service.dart';
import 'services/chat_service.dart';

final FlutterLocalNotificationsPlugin flutterLocalNotificationsPlugin =
    FlutterLocalNotificationsPlugin();

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Initialize notifications
  const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
  const initSettings = InitializationSettings(android: androidInit);
  await flutterLocalNotificationsPlugin.initialize(settings: initSettings);

  final cache = StorageService();
  await cache.init();
  final authService = AuthService();
  await authService.init();
  final sensorDb = SensorDbService();
  await sensorDb.init();
  final bleService = BleService();
  bleService.sensorDb = sensorDb;
  final uploadService = UploadService(authService, sensorDb, bleService);
  bleService.uploadService = uploadService;
  final syncOrchestrator = SyncOrchestrator();
  bleService.onSyncComplete = () => syncOrchestrator.notifyCloudDataStale();
  final activityTracker = ActivityTrackerService();
  await activityTracker.init();
  final hydrationService = HydrationService();
  await hydrationService.init();
  final journalService = JournalService();
  await journalService.init();
  final weatherService = WeatherService();
  await weatherService.init();
  final aiService = AiService();
  await aiService.init();
  final prefetchService = PrefetchService(flutterLocalNotificationsPlugin);
  final chatService = ChatService();
  await chatService.init();
  // Auto-refresh weather on launch if location is set
  if (weatherService.hasLocation) weatherService.refresh();
  runApp(OpenWhoopApp(
    authService: authService,
    cache: cache,
    bleService: bleService,
    sensorDb: sensorDb,
    uploadService: uploadService,
    syncOrchestrator: syncOrchestrator,
    activityTracker: activityTracker,
    hydrationService: hydrationService,
    journalService: journalService,
    weatherService: weatherService,
    aiService: aiService,
    prefetchService: prefetchService,
    chatService: chatService,
  ));
}

class OpenWhoopApp extends StatelessWidget {
  final AuthService authService;
  final StorageService cache;
  final BleService bleService;
  final SensorDbService sensorDb;
  final UploadService uploadService;
  final SyncOrchestrator syncOrchestrator;
  final ActivityTrackerService activityTracker;
  final HydrationService hydrationService;
  final JournalService journalService;
  final WeatherService weatherService;
  final AiService aiService;
  final PrefetchService prefetchService;
  final ChatService chatService;
  const OpenWhoopApp({
    super.key,
    required this.authService,
    required this.cache,
    required this.bleService,
    required this.sensorDb,
    required this.uploadService,
    required this.syncOrchestrator,
    required this.activityTracker,
    required this.hydrationService,
    required this.journalService,
    required this.weatherService,
    required this.aiService,
    required this.prefetchService,
    required this.chatService,
  });

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: authService),
        ChangeNotifierProvider.value(value: bleService),
        ChangeNotifierProvider.value(value: sensorDb),
        Provider(create: (_) {
          final api = ApiService(authService, cache: cache);
          prefetchService.setApi(api);
          chatService.setDependencies(aiService, api, weatherService);
          return api;
        }),
        Provider(create: (_) => AnalysisService(sensorDb: sensorDb, storage: cache)),
        ChangeNotifierProvider.value(value: uploadService),
        ChangeNotifierProvider.value(value: syncOrchestrator),
        ChangeNotifierProvider.value(value: activityTracker),
        ChangeNotifierProvider.value(value: hydrationService),
        ChangeNotifierProvider.value(value: journalService),
        ChangeNotifierProvider.value(value: weatherService),
        ChangeNotifierProvider.value(value: aiService),
        ChangeNotifierProvider.value(value: prefetchService),
        ChangeNotifierProvider.value(value: chatService),
      ],
      child: DynamicColorBuilder(
        builder: (lightDynamic, darkDynamic) {
          return MaterialApp.router(
            title: 'Open Whoop',
            debugShowCheckedModeBanner: false,
            themeMode: ThemeMode.dark,
            darkTheme: WhoopTheme.buildTheme(darkDynamic),
            theme: WhoopTheme.buildTheme(darkDynamic),
            routerConfig: createRouter(authService),
          );
        },
      ),
    );
  }
}
