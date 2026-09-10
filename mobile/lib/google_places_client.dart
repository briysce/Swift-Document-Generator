import 'dart:convert';

import 'package:http/http.dart' as http;

import 'gemini_client.dart';
import 'osm_nominatim_client.dart';

/// Google Places API (New) Autocomplete — real, authoritative civic/commercial
/// address data for the common case (a real street address in a real city).
/// Reuses the same `GOOGLE_API_KEY` already configured for Gemini; Places API
/// (New) just needs to be enabled on that key's Google Cloud project.
///
/// Deliberately NOT a replacement for [OsmNominatimClient]: OSM/Nominatim
/// (via Photon) is what makes LSD / lease-road / wellsite addresses that
/// aren't on any commercial map work at all, and Places Autocomplete returns
/// nothing for those queries. Callers should try Places first for the
/// common civic-address case, then fall back to Nominatim so remote-site
/// entry keeps working exactly as before.
class GooglePlacesClient {
  GooglePlacesClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  static bool get isConfigured => _apiKey.isNotEmpty;

  static String get _apiKey => GeminiClient.envValue('GOOGLE_API_KEY');

  /// Up to 8 suggestions, biased to Canada. Returns the same [NominatimHit]
  /// shape the rest of the address-suggest pipeline already expects, so it
  /// drops straight into [mergeAddressSuggestions] alongside OSM/address-book
  /// results. Empty (never throws) on any failure, including "not enabled".
  Future<List<NominatimHit>> autocomplete(String query) async {
    final key = _apiKey;
    final q = query.trim();
    if (key.isEmpty || q.length < 3) return const [];

    try {
      final uri = Uri.parse('https://places.googleapis.com/v1/places:autocomplete');
      final res = await _client
          .post(
            uri,
            headers: {
              'Content-Type': 'application/json',
              'X-Goog-Api-Key': key,
            },
            body: jsonEncode({
              'input': q,
              'includedRegionCodes': ['ca'],
              'languageCode': 'en',
            }),
          )
          .timeout(const Duration(seconds: 8));
      if (res.statusCode < 200 || res.statusCode >= 300) return const [];

      final body = jsonDecode(res.body);
      if (body is! Map) return const [];
      final suggestions = body['suggestions'];
      if (suggestions is! List) return const [];

      final out = <NominatimHit>[];
      for (final s in suggestions) {
        if (s is! Map) continue;
        final prediction = s['placePrediction'];
        if (prediction is! Map) continue;
        final hit = hitFromPrediction(prediction);
        if (hit != null) out.add(hit);
        if (out.length >= 8) break;
      }
      return out;
    } catch (_) {
      return const [];
    }
  }

  /// Exposed (not private) so the response-shape parsing can be unit tested
  /// without mocking HTTP — matches this codebase's existing convention of
  /// testing the pure post-processing of an AI/API client rather than the
  /// network call itself.
  static NominatimHit? hitFromPrediction(Map prediction) {
    final text = prediction['text'];
    final formatted = text is Map ? '${text['text'] ?? ''}'.trim() : '';
    if (formatted.isEmpty) return null;

    // "structuredFormat.mainText" is the street/place line, "secondaryText"
    // is city/province/country — split when present, else fall back to the
    // first comma in the plain formatted text.
    final structured = prediction['structuredFormat'];
    String street = '';
    String locality = '';
    if (structured is Map) {
      final main = structured['mainText'];
      final secondary = structured['secondaryText'];
      street = main is Map ? '${main['text'] ?? ''}'.trim() : '';
      locality = secondary is Map ? '${secondary['text'] ?? ''}'.trim() : '';
    }
    if (street.isEmpty) {
      final comma = formatted.indexOf(',');
      street = comma < 0 ? formatted : formatted.substring(0, comma).trim();
      locality = comma < 0 ? '' : formatted.substring(comma + 1).trim();
    }

    final houseMatch = RegExp(r'^(\d+[A-Za-z]?)\s').firstMatch(street);
    return NominatimHit(
      streetLine: street,
      localityLine: locality,
      displayAddress: formatted,
      houseNumber: houseMatch?.group(1) ?? '',
    );
  }
}
