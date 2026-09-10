import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/google_places_client.dart';

/// Google Places Autocomplete (New) is the primary address source now —
/// tried before OpenStreetMap Nominatim for real civic/commercial addresses,
/// which OSM's free data covers unevenly for Alberta/BC industrial areas.
/// Nominatim/Photon still runs whenever Places comes back empty, which is
/// what keeps LSD / lease-road / wellsite entry working (Places has no
/// concept of those at all).
void main() {
  group('GooglePlacesClient.hitFromPrediction', () {
    test('prefers structuredFormat main/secondary text', () {
      final hit = GooglePlacesClient.hitFromPrediction({
        'text': {'text': '655 30 Ave, Nisku, AB, Canada'},
        'structuredFormat': {
          'mainText': {'text': '655 30 Ave'},
          'secondaryText': {'text': 'Nisku, AB, Canada'},
        },
      });
      expect(hit, isNotNull);
      expect(hit!.streetLine, '655 30 Ave');
      expect(hit.localityLine, 'Nisku, AB, Canada');
      expect(hit.displayAddress, '655 30 Ave, Nisku, AB, Canada');
      expect(hit.houseNumber, '655');
    });

    test('falls back to splitting the plain formatted text on the first comma', () {
      final hit = GooglePlacesClient.hitFromPrediction({
        'text': {'text': '9328 37 Avenue NW, Edmonton, AB, Canada'},
      });
      expect(hit, isNotNull);
      expect(hit!.streetLine, '9328 37 Avenue NW');
      expect(hit.localityLine, 'Edmonton, AB, Canada');
      expect(hit.houseNumber, '9328');
    });

    test('handles a street with no leading civic number', () {
      final hit = GooglePlacesClient.hitFromPrediction({
        'text': {'text': 'Highway 2A, Airdrie, AB, Canada'},
        'structuredFormat': {
          'mainText': {'text': 'Highway 2A'},
          'secondaryText': {'text': 'Airdrie, AB, Canada'},
        },
      });
      expect(hit, isNotNull);
      expect(hit!.houseNumber, isEmpty);
    });

    test('returns null when there is no text at all', () {
      expect(GooglePlacesClient.hitFromPrediction(const {}), isNull);
    });
  });

  test('autocomplete returns nothing for a short query, no network call needed', () async {
    final client = GooglePlacesClient();
    expect(await client.autocomplete('12'), isEmpty);
  });
}
