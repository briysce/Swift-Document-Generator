import 'dart:io';

import 'package:flutter/material.dart';

/// Shared brand tokens — keep in sync with Windows `fill_shipping_label.py`
/// and PDF brand stripe (`PdfColor` `#CE4E30`).
class SwiftColors {
  static const accent = Color(0xFFCE4E30);
  static const accentHover = Color(0xFFB8442A);
  static const accentPressed = Color(0xFFA33C26);
  static const accentSoft = Color(0xFFF8EBE7);
  static const accentOn = Color(0xFFFFE8E0);
  static const bg = Color(0xFFF4F2EF);
  static const surface = Color(0xFFFFFFFF);
  static const ink = Color(0xFF1A1A1A);
  static const muted = Color(0xFF6B6B6B);
  /// Darker than [muted] — ~7:1 on white, real headroom above the 4.5:1 AA
  /// floor for small all-caps field labels (muted's own ~5.3:1 reads weak in
  /// practice at 12px/all-caps, especially under warehouse-floor glare).
  static const inputLabel = Color(0xFF595959);
  static const border = Color(0xFFE6E2DC);
  /// Slightly cooler panel wash for desktop chrome (rails / sidebars).
  static const panel = Color(0xFFF7F5F2);
  static const railSelected = Color(0xFFF3E4DF);
  static const inputFill = Color(0xFFFAFAF8);
  /// Slightly elevated light sheets (date picker, menus, chips) — never ink.
  static const elevated = Color(0xFFEDE9E3);

  // Dark-mode counterparts (Windows + Android).
  static const darkBg = Color(0xFF121417);
  static const darkSurface = Color(0xFF1C1F24);
  static const darkPanel = Color(0xFF16191E);
  static const darkInk = Color(0xFFF2F0EC);
  static const darkMuted = Color(0xFFA3A29C);
  static const darkBorder = Color(0xFF2E333A);
  static const darkAccentSoft = Color(0xFF3A221C);
  static const darkRailSelected = Color(0xFF4A2A22);
  static const darkInputFill = Color(0xFF15181C);
  static const darkElevated = Color(0xFF2A2E35);
}

/// Spacing scale — prefer these over magic numbers in chrome widgets.
class SwiftSpace {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
}

/// Corner radii: desktop tighter, mobile more touch-friendly.
class SwiftRadius {
  static double card(bool desktop) => desktop ? 8 : 14;
  static double control(bool desktop) => desktop ? 6 : 10;
  static double dialog(bool desktop) => desktop ? 10 : 16;
  static double chip(bool desktop) => desktop ? 6 : 8;
}

class SwiftTheme {
  static ThemeData light({
    double fontScale = 1.0,
    String fontFamily = 'Helvetica',
  }) =>
      _build(
        brightness: Brightness.light,
        fontScale: fontScale,
        fontFamily: fontFamily,
      );

  static ThemeData dark({
    double fontScale = 1.0,
    String fontFamily = 'Helvetica',
  }) =>
      _build(
        brightness: Brightness.dark,
        fontScale: fontScale,
        fontFamily: fontFamily,
      );

  static ThemeData _build({
    required Brightness brightness,
    required double fontScale,
    required String fontFamily,
  }) {
    final desktop = Platform.isWindows;
    final dark = brightness == Brightness.dark;
    final scale = fontScale.clamp(0.85, 1.35);

    final bg = dark ? SwiftColors.darkBg : SwiftColors.bg;
    final surface = dark ? SwiftColors.darkSurface : SwiftColors.surface;
    final ink = dark ? SwiftColors.darkInk : SwiftColors.ink;
    final muted = dark ? SwiftColors.darkMuted : SwiftColors.muted;
    // Dark mode's muted is already light-on-dark (high contrast); only light
    // mode's field-label color needed more headroom above the AA floor.
    final inputLabelColor = dark ? SwiftColors.darkMuted : SwiftColors.inputLabel;
    final border = dark ? SwiftColors.darkBorder : SwiftColors.border;
    final accentSoft =
        dark ? SwiftColors.darkAccentSoft : SwiftColors.accentSoft;
    final railSelected =
        dark ? SwiftColors.darkRailSelected : SwiftColors.railSelected;
    final inputFill =
        dark ? SwiftColors.darkInputFill : SwiftColors.inputFill;
    final chromePanel = dark ? SwiftColors.darkPanel : SwiftColors.panel;
    // M3 date picker / menus use surfaceContainer* — light mode must stay light.
    // (Previously light used ink here, which made the calendar black/unreadable.)
    final elevated = dark ? SwiftColors.darkElevated : SwiftColors.elevated;

    final cardR = SwiftRadius.card(desktop);
    final controlR = SwiftRadius.control(desktop);
    final dialogR = SwiftRadius.dialog(desktop);

    final base = ColorScheme.fromSeed(
      seedColor: SwiftColors.accent,
      primary: SwiftColors.accent,
      surface: surface,
      brightness: brightness,
    );

    final labelStyle = TextStyle(
      fontFamily: fontFamily,
      fontFamilyFallback: const ['Helvetica Neue', 'Arial', 'sans-serif'],
      fontSize: 12 * scale,
      fontWeight: FontWeight.w500,
      color: inputLabelColor,
      letterSpacing: 0.3,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      fontFamily: fontFamily,
      fontFamilyFallback: const ['Helvetica Neue', 'Arial', 'sans-serif'],
      visualDensity:
          desktop ? VisualDensity.compact : VisualDensity.standard,
      colorScheme: base.copyWith(
        primary: SwiftColors.accent,
        onPrimary: Colors.white,
        secondary: SwiftColors.accent,
        onSecondary: Colors.white,
        // FilledButton.tonal defaults to a seed-derived secondaryContainer,
        // which for this saturated an accent renders nearly as loud as a
        // plain solid FilledButton — two "tonal vs filled" buttons next to
        // each other (e.g. Find logo vs Upload OA) read as equally primary.
        // Force it to the actual soft tint so tonal buttons read as a real
        // secondary tier app-wide.
        secondaryContainer: accentSoft,
        onSecondaryContainer: SwiftColors.accent,
        surface: surface,
        onSurface: ink,
        onSurfaceVariant: muted,
        outline: border,
        error: const Color(0xFFB3261E),
        // M3 MenuBar / SubmenuButton panels read these — keep them on-theme
        // so dark mode menus are not light sheets with near-white labels.
        surfaceContainerLowest: bg,
        surfaceContainerLow: surface,
        surfaceContainer: surface,
        surfaceContainerHigh: elevated,
        surfaceContainerHighest: elevated,
      ),
      scaffoldBackgroundColor: bg,
      canvasColor: surface,
      textTheme: ThemeData(brightness: brightness).textTheme.apply(
            fontFamily: fontFamily,
            bodyColor: ink,
            displayColor: ink,
            fontSizeFactor: scale,
          ).copyWith(
            titleLarge: TextStyle(
              fontFamily: fontFamily,
              fontWeight: FontWeight.w600,
              fontSize: (desktop ? 20 : 22) * scale,
              letterSpacing: 0.3,
              color: ink,
            ),
            titleMedium: TextStyle(
              fontFamily: fontFamily,
              fontWeight: FontWeight.w600,
              fontSize: (desktop ? 15 : 16) * scale,
              letterSpacing: 0.3,
              color: ink,
            ),
            titleSmall: TextStyle(
              fontFamily: fontFamily,
              fontWeight: FontWeight.w600,
              fontSize: 13 * scale,
              letterSpacing: 0.4,
              color: ink,
            ),
            labelLarge: TextStyle(
              fontFamily: fontFamily,
              fontWeight: FontWeight.w600,
              fontSize: 13 * scale,
              letterSpacing: 0.4,
              color: ink,
            ),
            bodySmall: TextStyle(
              fontFamily: fontFamily,
              // 13, not 12 — one more step in the scale between the 12px
              // all-caps field labels and 14-16px section headers, so hint
              // captions ("Who the shipment is for") read as their own tier
              // instead of blending into the label tier below them.
              fontSize: 13 * scale,
              color: muted,
              height: 1.35,
            ),
          ),
      appBarTheme: AppBarTheme(
        // Mobile chrome matches Windows surface strip (not full orange block).
        backgroundColor: surface,
        foregroundColor: ink,
        elevation: 0,
        scrolledUnderElevation: desktop ? 0.5 : 0,
        centerTitle: false,
        titleTextStyle: TextStyle(
          fontFamily: fontFamily,
          fontWeight: FontWeight.w600,
          fontSize: (desktop ? 16 : 18) * scale,
          color: ink,
          letterSpacing: 0.4,
        ),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: chromePanel,
        indicatorColor: railSelected,
        indicatorShape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
        ),
        selectedIconTheme: const IconThemeData(
          color: SwiftColors.accent,
          size: 22,
        ),
        unselectedIconTheme: IconThemeData(
          color: muted,
          size: 22,
        ),
        selectedLabelTextStyle: TextStyle(
          fontFamily: fontFamily,
          fontWeight: FontWeight.w600,
          fontSize: 12 * scale,
          color: SwiftColors.accent,
          letterSpacing: 0.3,
        ),
        unselectedLabelTextStyle: TextStyle(
          fontFamily: fontFamily,
          fontWeight: FontWeight.w600,
          fontSize: 12 * scale,
          color: muted,
        ),
      ),
      cardTheme: CardThemeData(
        color: surface,
        // A soft shadow instead of a hairline border on every section —
        // stacked bordered cards (form section, right-rail block, etc.) were
        // reading as boxes-in-boxes; whitespace + a light lift separates
        // them just as clearly without the extra ruled lines.
        elevation: 1,
        shadowColor: Colors.black.withValues(alpha: dark ? 0.4 : 0.10),
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(cardR),
        ),
        margin: EdgeInsets.zero,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: inputFill,
        isDense: true,
        contentPadding: EdgeInsets.symmetric(
          horizontal: 12,
          vertical: desktop ? 8 : 11,
        ),
        labelStyle: labelStyle,
        hintStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 13 * scale,
          color: inputLabelColor,
        ),
        floatingLabelStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 12 * scale,
          fontWeight: FontWeight.w600,
          color: SwiftColors.accent,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(controlR),
          borderSide: BorderSide(color: border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(controlR),
          borderSide: BorderSide(color: border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(controlR),
          borderSide: BorderSide(
            color: SwiftColors.accent,
            width: desktop ? 1.4 : 1.5,
          ),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(controlR),
          borderSide: const BorderSide(color: Color(0xFFB3261E)),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        // No explicit background/foreground here — `FilledButtonThemeData`
        // is shared by both `FilledButton` (solid) and `FilledButton.tonal`,
        // so a hard-coded accent background used to paint BOTH variants
        // identically (e.g. "Upload OA" and "Find logo on the web" reading
        // as two equally-loud primary CTAs). Leaving color unset lets each
        // variant fall through to its own Material 3 default: `primary`/
        // `onPrimary` (both already = accent/white below) for solid, and
        // `secondaryContainer`/`onSecondaryContainer` (= accentSoft/accent,
        // set below) for tonal — a real secondary tier, app-wide, for free.
        style: FilledButton.styleFrom(
          elevation: 0,
          padding: EdgeInsets.symmetric(
            horizontal: desktop ? 18 : 22,
            vertical: desktop ? 12 : 14,
          ),
          textStyle: TextStyle(
            fontFamily: fontFamily,
            fontWeight: FontWeight.w600,
            fontSize: (desktop ? 14 : 15) * scale,
            letterSpacing: 0.4,
          ),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(controlR),
          ),
        ).copyWith(
          overlayColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.pressed)) {
              return Colors.black.withValues(alpha: 0.14);
            }
            if (states.contains(WidgetState.hovered)) {
              return Colors.black.withValues(alpha: 0.08);
            }
            return null;
          }),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: ink,
          side: BorderSide(color: border),
          padding: EdgeInsets.symmetric(
            horizontal: desktop ? 12 : 14,
            vertical: desktop ? 10 : 12,
          ),
          textStyle: TextStyle(
            fontFamily: fontFamily,
            fontWeight: FontWeight.w600,
            fontSize: 13 * scale,
          ),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(controlR),
          ),
        ).copyWith(
          overlayColor: WidgetStatePropertyAll(
            SwiftColors.accent.withValues(alpha: 0.06),
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: muted,
          textStyle: TextStyle(
            fontFamily: fontFamily,
            fontWeight: FontWeight.w600,
            fontSize: 13 * scale,
          ),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: muted,
          hoverColor: SwiftColors.accent.withValues(alpha: 0.08),
        ),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.selected)) {
              return accentSoft;
            }
            return surface;
          }),
          foregroundColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.selected)) {
              return SwiftColors.accent;
            }
            return muted;
          }),
          side: WidgetStatePropertyAll(BorderSide(color: border)),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(controlR),
            ),
          ),
          visualDensity: desktop
              ? VisualDensity.compact
              : VisualDensity.standard,
          padding: WidgetStatePropertyAll(
            EdgeInsets.symmetric(
              horizontal: desktop ? 10 : 8,
              vertical: desktop ? 8 : 10,
            ),
          ),
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        // High contrast: solid ink / dark panel (not washed elevated grey).
        backgroundColor: dark ? const Color(0xFF323842) : SwiftColors.ink,
        contentTextStyle: TextStyle(
          fontFamily: fontFamily,
          color: const Color(0xFFF7F5F2),
          fontSize: 13.5,
          fontWeight: FontWeight.w600,
        ),
        actionTextColor: SwiftColors.accentOn,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(28),
        ),
        elevation: 10,
        insetPadding: EdgeInsets.symmetric(
          horizontal: desktop ? 16 : 32,
          vertical: 10,
        ),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        elevation: desktop ? 8 : 6,
        // Material's default (40, 24) leaves ~80pt of a narrow Android phone's
        // width as pure margin, forcing dialog content (side-by-side fields,
        // etc.) into a much smaller box than it was laid out for. Reclaim
        // most of that on phones; Windows keeps the original default.
        insetPadding: desktop
            ? const EdgeInsets.symmetric(horizontal: 40, vertical: 24)
            : const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(dialogR),
          side: BorderSide(color: border.withValues(alpha: 0.6)),
        ),
        titleTextStyle: TextStyle(
          fontFamily: fontFamily,
          fontWeight: FontWeight.w600,
          fontSize: 18 * scale,
          letterSpacing: 0.3,
          color: ink,
        ),
        contentTextStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 14 * scale,
          color: ink,
          height: 1.4,
        ),
      ),
      datePickerTheme: DatePickerThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        headerBackgroundColor: dark ? elevated : accentSoft,
        headerForegroundColor: ink,
        dayForegroundColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.disabled)) return muted;
          if (states.contains(WidgetState.selected)) return Colors.white;
          return ink;
        }),
        dayBackgroundColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return SwiftColors.accent;
          }
          return null;
        }),
        todayForegroundColor: WidgetStatePropertyAll(SwiftColors.accent),
        todayBackgroundColor: const WidgetStatePropertyAll(Colors.transparent),
        todayBorder: const BorderSide(color: SwiftColors.accent),
        yearForegroundColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.disabled)) return muted;
          if (states.contains(WidgetState.selected)) return Colors.white;
          return ink;
        }),
        yearBackgroundColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return SwiftColors.accent;
          }
          return null;
        }),
        weekdayStyle: TextStyle(
          fontFamily: fontFamily,
          color: muted,
          fontSize: 12 * scale,
        ),
        dayStyle: TextStyle(
          fontFamily: fontFamily,
          color: ink,
          fontSize: 13 * scale,
        ),
        yearStyle: TextStyle(
          fontFamily: fontFamily,
          color: ink,
          fontSize: 13 * scale,
        ),
        rangePickerBackgroundColor: surface,
        rangePickerHeaderBackgroundColor: dark ? elevated : accentSoft,
        rangePickerHeaderForegroundColor: ink,
        dividerColor: border,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(dialogR),
        ),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        elevation: 8,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(dialogR),
          ),
        ),
        dragHandleColor: border,
        showDragHandle: true,
      ),
      tabBarTheme: TabBarThemeData(
        labelColor: SwiftColors.accent,
        unselectedLabelColor: muted,
        indicatorColor: SwiftColors.accent,
        labelStyle: TextStyle(
          fontFamily: fontFamily,
          fontWeight: FontWeight.w600,
          fontSize: 13 * scale,
          letterSpacing: 0.3,
        ),
        unselectedLabelStyle: TextStyle(
          fontFamily: fontFamily,
          fontWeight: FontWeight.w500,
          fontSize: 13 * scale,
        ),
        dividerColor: border,
      ),
      listTileTheme: ListTileThemeData(
        dense: desktop,
        iconColor: muted,
        textColor: ink,
        contentPadding: EdgeInsets.symmetric(
          horizontal: desktop ? 8 : 12,
        ),
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return SwiftColors.accent;
          }
          return Colors.transparent;
        }),
        checkColor: const WidgetStatePropertyAll(Colors.transparent),
        overlayColor: WidgetStatePropertyAll(
          SwiftColors.accent.withValues(alpha: 0.12),
        ),
        side: const BorderSide(color: SwiftColors.accent, width: 2),
        shape: const CircleBorder(),
        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
        visualDensity: VisualDensity.compact,
      ),
      radioTheme: RadioThemeData(
        fillColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return SwiftColors.accent;
          }
          return SwiftColors.accent;
        }),
        overlayColor: WidgetStatePropertyAll(
          SwiftColors.accent.withValues(alpha: 0.12),
        ),
        visualDensity: VisualDensity.compact,
        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return Colors.white;
          }
          return muted;
        }),
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return SwiftColors.accent;
          }
          return border;
        }),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: SwiftColors.accent,
        linearTrackColor: SwiftColors.accentSoft,
      ),
      // Submenu panels defaulted to a light Material surface while menu
      // button foreground used dark-mode ink (near-white) — text vanished
      // until hover. Theme the menu panel + buttons together.
      menuTheme: MenuThemeData(
        style: MenuStyle(
          backgroundColor: WidgetStatePropertyAll(surface),
          shadowColor: WidgetStatePropertyAll(
            Colors.black.withValues(alpha: dark ? 0.55 : 0.22),
          ),
          elevation: const WidgetStatePropertyAll(8),
          padding: const WidgetStatePropertyAll(
            EdgeInsets.symmetric(vertical: 6),
          ),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(controlR),
              side: BorderSide(color: border),
            ),
          ),
        ),
      ),
      menuBarTheme: MenuBarThemeData(
        style: MenuStyle(
          backgroundColor: WidgetStatePropertyAll(surface),
          elevation: const WidgetStatePropertyAll(0),
          padding: const WidgetStatePropertyAll(
            EdgeInsets.symmetric(horizontal: 4),
          ),
          shape: const WidgetStatePropertyAll(
            RoundedRectangleBorder(),
          ),
        ),
      ),
      menuButtonTheme: MenuButtonThemeData(
        style: ButtonStyle(
          foregroundColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.disabled)) {
              return muted.withValues(alpha: 0.55);
            }
            return ink;
          }),
          iconColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.disabled)) {
              return muted.withValues(alpha: 0.55);
            }
            return ink;
          }),
          backgroundColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.hovered) ||
                states.contains(WidgetState.focused) ||
                states.contains(WidgetState.selected)) {
              return accentSoft;
            }
            return Colors.transparent;
          }),
          overlayColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.pressed)) {
              return accentSoft.withValues(alpha: 0.85);
            }
            return null;
          }),
          textStyle: WidgetStatePropertyAll(
            TextStyle(
              fontFamily: fontFamily,
              fontSize: 13 * scale,
              fontWeight: FontWeight.w600,
              color: ink,
            ),
          ),
        ),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: surface,
        surfaceTintColor: Colors.transparent,
        elevation: 6,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(controlR),
          side: BorderSide(color: border),
        ),
        textStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 13 * scale,
          color: ink,
        ),
      ),
      dropdownMenuTheme: DropdownMenuThemeData(
        menuStyle: MenuStyle(
          backgroundColor: WidgetStatePropertyAll(surface),
          surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(controlR),
              side: BorderSide(color: border),
            ),
          ),
        ),
        textStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 13 * scale,
          color: ink,
        ),
      ),
      dividerTheme: DividerThemeData(
        color: border,
        space: 1,
        thickness: 1,
      ),
      tooltipTheme: TooltipThemeData(
        waitDuration: const Duration(milliseconds: 400),
        decoration: BoxDecoration(
          color: elevated,
          borderRadius: BorderRadius.circular(6),
        ),
        textStyle: TextStyle(
          fontFamily: fontFamily,
          fontSize: 12,
          color: Colors.white,
        ),
      ),
      extensions: <ThemeExtension<dynamic>>[
        SwiftChromeColors(
          bg: bg,
          surface: surface,
          panel: chromePanel,
          ink: ink,
          muted: muted,
          border: border,
          accentSoft: accentSoft,
          elevated: elevated,
        ),
      ],
    );
  }
}

/// Runtime chrome colors that flip with light/dark (Windows scaffolds).
class SwiftChromeColors extends ThemeExtension<SwiftChromeColors> {
  const SwiftChromeColors({
    required this.bg,
    required this.surface,
    required this.panel,
    required this.ink,
    required this.muted,
    required this.border,
    this.accentSoft = SwiftColors.accentSoft,
    this.elevated = SwiftColors.elevated,
  });

  final Color bg;
  final Color surface;
  final Color panel;
  final Color ink;
  final Color muted;
  final Color border;
  final Color accentSoft;
  final Color elevated;

  static SwiftChromeColors of(BuildContext context) {
    return Theme.of(context).extension<SwiftChromeColors>() ??
        const SwiftChromeColors(
          bg: SwiftColors.bg,
          surface: SwiftColors.surface,
          panel: SwiftColors.panel,
          ink: SwiftColors.ink,
          muted: SwiftColors.muted,
          border: SwiftColors.border,
        );
  }

  @override
  SwiftChromeColors copyWith({
    Color? bg,
    Color? surface,
    Color? panel,
    Color? ink,
    Color? muted,
    Color? border,
    Color? accentSoft,
    Color? elevated,
  }) {
    return SwiftChromeColors(
      bg: bg ?? this.bg,
      surface: surface ?? this.surface,
      panel: panel ?? this.panel,
      ink: ink ?? this.ink,
      muted: muted ?? this.muted,
      border: border ?? this.border,
      accentSoft: accentSoft ?? this.accentSoft,
      elevated: elevated ?? this.elevated,
    );
  }

  @override
  SwiftChromeColors lerp(ThemeExtension<SwiftChromeColors>? other, double t) {
    if (other is! SwiftChromeColors) return this;
    return SwiftChromeColors(
      bg: Color.lerp(bg, other.bg, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      panel: Color.lerp(panel, other.panel, t)!,
      ink: Color.lerp(ink, other.ink, t)!,
      muted: Color.lerp(muted, other.muted, t)!,
      border: Color.lerp(border, other.border, t)!,
      accentSoft: Color.lerp(accentSoft, other.accentSoft, t)!,
      elevated: Color.lerp(elevated, other.elevated, t)!,
    );
  }
}
