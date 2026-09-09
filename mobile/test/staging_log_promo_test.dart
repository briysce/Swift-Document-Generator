import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/changelog.dart';
import 'package:swift_shipping_label/sibling_apps.dart';
import 'package:swift_shipping_label/staging_log_promo.dart';

void main() {
  test('changelog campaign lists Swift Staging & Shipping Log intro', () {
    expect(AppChangelog.campaignId, 'whats_new_1_1_95');
    expect(AppChangelog.maxShows, 3);
    final bullets = AppChangelog.sections
        .expand((s) => s.bullets)
        .join(' ');
    expect(bullets, contains('Swift Staging & Shipping Log'));
  });

  test('promo Try it today targets the same sibling app as MORE APPS', () {
    expect(siblingStagingTracker.id, 'staging-tracker');
    expect(slstPromoIconAsset, siblingStagingTracker.iconAsset);
    expect(slstPromoStills, hasLength(4));
  });
}
