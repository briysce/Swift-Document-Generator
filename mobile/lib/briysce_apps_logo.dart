import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

/// "briysce apps" sub-brand lockup: the white briysce wordmark with "APPS"
/// right-aligned directly beneath it — signals this app is part of the
/// briysce apps development collection.
///
/// The wordmark itself is briysce's approved white vector master
/// (`assets/images/briysce_wordmark_paper.svg`, per
/// `briysce/brand/GUIDELINES.md`) rendered at native resolution via
/// [SvgPicture] so it stays crisp at any splash-screen size/DPI.
///
/// The wordmark is bespoke lettering, not a licensed typeface (see brand
/// guidelines §4), so an exact font match for "APPS" is not possible. This
/// approximates it with the app's existing bold geometric sans (Montserrat
/// Bold) in the same white, tracked out like a logotype sub-label.
class BriysceAppsLockup extends StatelessWidget {
  const BriysceAppsLockup({super.key, this.width = 132});

  /// Rendered width of the wordmark; the "APPS" label matches this width.
  final double width;

  @override
  Widget build(BuildContext context) {
    // Wordmark viewBox is 3279x1010 (~3.247:1).
    final wordmarkHeight = width / (3279 / 1010);
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        SizedBox(
          width: width,
          height: wordmarkHeight,
          child: SvgPicture.asset(
            'assets/images/briysce_wordmark_paper.svg',
            fit: BoxFit.contain,
            alignment: Alignment.centerLeft,
          ),
        ),
        SizedBox(height: wordmarkHeight * 0.16),
        Text(
          'APPS',
          textAlign: TextAlign.right,
          style: TextStyle(
            fontFamily: 'Montserrat',
            fontWeight: FontWeight.w700,
            color: Colors.white,
            fontSize: wordmarkHeight * 0.62,
            height: 1.0,
            letterSpacing: wordmarkHeight * 0.22,
          ),
        ),
      ],
    );
  }
}
