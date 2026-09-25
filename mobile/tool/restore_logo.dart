import 'dart:io';
import 'dart:typed_data';

import 'package:image/image.dart' as img;
import 'package:swift_shipping_label/gemini_client.dart';
import 'package:swift_shipping_label/logo_restorer.dart';

/// Live Gemini logo restore (not flutter_test — tests mock HTTP).
Future<void> main(List<String> args) async {
  if (!GeminiClient.isConfigured) {
    stderr.writeln('GEMINI_API_KEY missing');
    exit(1);
  }
  final src = File(
    args.isNotEmpty
        ? args.first
        : r'C:\Users\Brice\OneDrive\Documents\swift_document_generator\customer_logos\bfl_google_source.png',
  );
  if (!src.existsSync()) {
    stderr.writeln('missing ${src.path}');
    exit(1);
  }
  final logosDir = Directory(
    r'C:\Users\Brice\OneDrive\Documents\swift_document_generator\customer_logos',
  );
  stdout.writeln('restoring ${src.path}');
  final out = await LogoRestorer.ensureHighRes(
    src,
    logosDir: logosDir,
    onLog: stdout.writeln,
  );
  final decoded = img.decodeImage(Uint8List.fromList(await out.readAsBytes()));
  stdout.writeln('wrote ${out.path} ${decoded?.width}x${decoded?.height}');
}
