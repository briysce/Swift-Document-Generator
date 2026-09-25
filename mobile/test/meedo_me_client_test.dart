import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:swift_shipping_label/meedo_me_client.dart';

void main() {
  setUp(MeedoMeClient.resetCooldown);

  test('base url normalizes and defaults to the local runtime', () {
    expect(MeedoMeClient.resolveBaseUrl(), 'http://localhost:1337/v1');
  });

  test('completes a chat turn and returns assistant text', () async {
    late Map<String, dynamic> sent;
    final c = MeedoMeClient(
      client: MockClient((req) async {
        sent = jsonDecode(req.body) as Map<String, dynamic>;
        return http.Response(
          jsonEncode({
            'choices': [
              {
                'message': {'role': 'assistant', 'content': 'PARSED OK'},
              },
            ],
          }),
          200,
        );
      }),
    );
    final out = await c.complete(prompt: 'read this');
    expect(out, 'PARSED OK');
    expect(sent['stream'], isFalse);
    expect((sent['messages'] as List).last['content'][0]['text'], 'read this');
  });

  test('attaches images as base64 image_url parts and uses the vision model',
      () async {
    late Map<String, dynamic> sent;
    final png = Uint8List.fromList([0x89, 0x50, 0x4E, 0x47, 1, 2, 3, 4]);
    final c = MeedoMeClient(
      client: MockClient((req) async {
        sent = jsonDecode(req.body) as Map<String, dynamic>;
        return http.Response(
          jsonEncode({
            'choices': [
              {
                'message': {'content': '{"ok":true}'},
              },
            ],
          }),
          200,
        );
      }),
    );
    final out = await c.completeJson(prompt: 'judge', images: [png]);
    expect(out, {'ok': true});
    expect(sent['model'], MeedoMeClient.visionModel);
    final parts = (sent['messages'] as List).last['content'] as List;
    expect(parts.length, 2);
    expect(parts[1]['image_url']['url'], startsWith('data:image/png;base64,'));
  });

  test('fails open to null when the runtime is unreachable', () async {
    final c = MeedoMeClient(
      client: MockClient((req) async => throw const SocketExceptionStub()),
    );
    expect(await c.complete(prompt: 'x'), isNull);
    // and trips a cooldown so batch callers stop dialling
    expect(MeedoMeClient.isTemporarilyUnavailable, isTrue);
  });

  test('fails open to null on a non-2xx reply', () async {
    final c = MeedoMeClient(
      client: MockClient((req) async => http.Response('model not found', 404)),
    );
    expect(await c.complete(prompt: 'x'), isNull);
  });

  test('extracts JSON that a local model wrapped in prose or fences', () {
    expect(
      MeedoMeClient.extractJsonObject('Sure!\n```json\n{"a":1}\n```\nDone.'),
      {'a': 1},
    );
    expect(
      MeedoMeClient.extractJsonObject('prefix {"nested":{"b":"}"},"c":2} tail'),
      {
        'nested': {'b': '}'},
        'c': 2,
      },
    );
    expect(MeedoMeClient.extractJsonObject('no json here'), isNull);
  });
}

class SocketExceptionStub implements Exception {
  const SocketExceptionStub();
}
