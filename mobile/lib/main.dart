import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_scroll_behavior.dart';
import 'app_splash_screen.dart';
import 'app_storage.dart';
import 'app_theme_scope.dart';
import 'auto_update_scheduler.dart';
import 'pdf/shipping_label_pdf.dart';
import 'pdf_render_options.dart';
import 'startup_sync.dart';
import 'theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (Platform.isAndroid) {
    await SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
  }
  final storage = await AppStorage.open();
  final pdf = await ShippingLabelPdf.load();
  final settings = await storage.loadUiSettings();
  _applySystemUiOverlay(settings.isDark);

  // One shared "Supabase data ready" signal for this launch — presets,
  // signatures, contacts, delivery addresses, carriers, and expired
  // generated-document history purge. AppSplashScreen's progress bar awaits
  // this same instance on both Windows and Android, and HomeScreen reuses
  // its already-in-flight futures so Supabase is only ever hit once per
  // launch. See startup_sync.dart.
  final startupSync = StartupSync(storage);

  runApp(
    SwiftShippingLabelApp(
      storage: storage,
      pdf: pdf,
      initialSettings: settings,
      startupSync: startupSync,
    ),
  );
}

void _applySystemUiOverlay(bool dark) {
  if (!Platform.isAndroid) return;
  SystemChrome.setSystemUIOverlayStyle(
    SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: dark ? Brightness.light : Brightness.dark,
      statusBarBrightness: dark ? Brightness.dark : Brightness.light,
      systemNavigationBarColor: Colors.transparent,
      systemNavigationBarIconBrightness:
          dark ? Brightness.light : Brightness.dark,
      systemNavigationBarDividerColor: Colors.transparent,
    ),
  );
}

/// App-level theme + UI settings (dark mode persists via [AppThemeScope]).
class SwiftShippingLabelApp extends StatefulWidget {
  const SwiftShippingLabelApp({
    super.key,
    required this.storage,
    required this.pdf,
    required this.initialSettings,
    required this.startupSync,
  });

  final AppStorage storage;
  final ShippingLabelPdf pdf;
  final AppUiSettings initialSettings;
  final StartupSync startupSync;

  @override
  State<SwiftShippingLabelApp> createState() => _SwiftShippingLabelAppState();
}

class _SwiftShippingLabelAppState extends State<SwiftShippingLabelApp> {
  late final ValueNotifier<AppUiSettings> _settings =
      ValueNotifier(widget.initialSettings);

  @override
  void dispose() {
    _settings.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AppThemeScope(
      notifier: _settings,
      child: ValueListenableBuilder<AppUiSettings>(
        valueListenable: _settings,
        builder: (context, settings, _) {
          final dark = settings.themePreference == UiThemePreference.dark;
          _applySystemUiOverlay(dark);
          final scale = Platform.isWindows ? settings.uiFontScale : 1.0;
          final font = settings.uiFontFamily;
          return MaterialApp(
            title: 'Swift Document Generator',
            debugShowCheckedModeBanner: false,
            scrollBehavior: const AppScrollBehavior(),
            theme: SwiftTheme.light(fontScale: scale, fontFamily: font),
            darkTheme: SwiftTheme.dark(fontScale: scale, fontFamily: font),
            themeMode: dark ? ThemeMode.dark : ThemeMode.light,
            home: AutoUpdateHost(
              storage: widget.storage,
              // Same branded splash/loading screen on Windows and Android,
              // gated on StartupSync.ready — see app_splash_screen.dart.
              child: AppSplashScreen(
                storage: widget.storage,
                pdf: widget.pdf,
                startupSync: widget.startupSync,
              ),
            ),
          );
        },
      ),
    );
  }
}
