import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart' as shelf_io;
import 'package:shelf_router/shelf_router.dart';
import '../utils/global.dart';
import '../utils/logger.dart';
import '../utils/ws_handler.dart';
import '../utils/req_ai.dart';

WebSocket? socketToAgent;
int wsStatus = 0;
bool isReconnecting = false;
StreamController<dynamic>? agentStreamController;
InternetAddress? lastClientAddress;
Timer? _autoUnlockTimer;

void main() async {
  if (!File('config.json').existsSync()) {
    Logger.warn('Configuration file not found, creating a new one...');
    Map<String, dynamic> cfg = {
      "host": "0.0.0.0",
      "port": 8032,
      "api_key": "",
      "zhipu_api_key": "",
      "prompt": "",
      "model": "glm-4-flash",
    };
    File(
      'config.json',
    ).writeAsStringSync(JsonEncoder.withIndent('  ').convert(cfg));
    Logger.info('Generated config.json');
  }

  String config = File('config.json').readAsStringSync();
  Map<String, dynamic> configJson = jsonDecode(config);
  Config.wsHost = configJson['host'];
  Config.wsPort = configJson['port'];
  Config.apiKey = configJson['api_key'];
  Config.zhipuApiKey = configJson['zhipu_api_key'];
  Config.prompt = configJson['prompt'];
  Config.model = configJson['model'] ?? Config.model;
  if (Config.wsHost.isEmpty ||
      Config.wsPort == 0 ||
      Config.apiKey.isEmpty ||
      Config.zhipuApiKey.isEmpty ||
      Config.prompt.isEmpty ||
      Config.model.isEmpty) {
    Logger.error('Please complete the configuration in config.json');
    await Future.delayed(Duration(seconds: 5));
    exit(1);
  }

  startUdpAudioServer(Config.wsPort);

  ProcessSignal.sigint.watch().listen((signal) {
    Logger.info('Application shutdown completed');
    exit(0);
  });

  var router = Router();
  router.get('/esp32/backend/ws', (Request request) => wsHandler(request));

  var finalHandler = Pipeline().addMiddleware(customLogRequests()).addHandler((
    Request request,
  ) {
    if (request.url.path.startsWith('esp32/backend/ws')) {
      return wsHandler(request);
    } else if (request.url.path.startsWith('esp32/backend/tts')) {
      final file = File('tts.pcm');
      if (!file.existsSync()) {
        return Response.notFound('TTS file not generated yet.');
      }
      final stat = file.statSync();

      return Response.ok(
        file.openRead(),
        headers: {
          'Content-Type': 'application/octet-stream',
          'Content-Disposition': 'attachment; filename="tts.pcm"',
          'Content-Length': stat.size.toString(),
        },
      );
    }
    return Response.notFound('Not Found');
  });

  var server = await shelf_io.serve(
    finalHandler,
    Config.wsHost,
    Config.wsPort,
    shared: true,
  );

  Logger.success(
    'Backend running in Mixed Mode (TCP & UDP) on port ${Config.wsPort}',
  );
  Logger.info('Listening on http://${server.address.host}:${server.port}');
}

/// UDP 音频接收
void startUdpAudioServer(int port) async {
  try {
    final RawDatagramSocket socket = await RawDatagramSocket.bind(
      InternetAddress.anyIPv4,
      port,
    );
    Logger.info('UDP Audio Listener injected successfully on port $port');

    BytesBuilder audioBuffer = BytesBuilder(copy: false);
    DateTime lastPacketTime = DateTime.now();
    bool isReceiving = false;

    socket.listen((RawSocketEvent event) {
      if (event == RawSocketEvent.read) {
        Datagram? dg = socket.receive();
        if (dg != null) {
          if (isProcessing) return;
          if (currentClientAddr == null) {
            currentClientAddr = dg.address;
            currentClientPort = dg.port;
            Logger.info(
              'Locked UDP session to ${dg.address.address}:${dg.port}',
            );
          }
          if (dg.address == currentClientAddr && dg.port == currentClientPort) {
            if (!isReceiving) {
              Logger.info('Incoming UDP Audio Stream detected...');
              isReceiving = true;
            }
            audioBuffer.add(dg.data);
            lastPacketTime = DateTime.now();
          } else {
            Logger.warn(
              'Ignored packet from ${dg.address.address}:${dg.port} (System Busy)',
            );
          }
        }
      }
    });

    Timer.periodic(Duration(milliseconds: 500), (timer) async {
      if (isReceiving &&
          DateTime.now().difference(lastPacketTime).inMilliseconds > 1200) {
        isReceiving = false;
        isProcessing = true;

        final pcmData = audioBuffer.takeBytes();
        audioBuffer.clear();

        if (pcmData.length > 3200) {
          await _processAudioPipeline(pcmData);
        } else {
          Logger.warn('Audio fragment too short, discarded.');
          broadcastMessage("no_data", "no_data");
        }
      }
    });
  } catch (e) {
    Logger.error('Failed to initialize UDP Listener: $e');
  }
}

Future<void> _processAudioPipeline(List<int> pcmData) async {
  try {
    final file = File('audio_record.wav');
    final wavHeader = _buildWavHeader(pcmData.length, 16000, 1, 16);
    await file.writeAsBytes([...wavHeader, ...pcmData]);
    Logger.success('Voice fragment saved: ${file.absolute.path}');

    final userText = await _asr(file);
    if (userText == null || userText.isEmpty) {
      Logger.warn('ASR returned empty or invalid text');
      broadcastMessage("no_data", "no_data");
      return;
    }
    Logger.info('ASR Result: $userText');

    Logger.info("Requesting AI Service...");
    final aiResponse = await AiService.requestGlm(userText);
    if (aiResponse == null) {
      Logger.error('AI Service returned null');
      return;
    }
    String aiText;
    String aiAction;
    try {
      var aiJson = jsonDecode(aiResponse);
      aiText = aiJson['text'] ?? '';
      aiAction = aiJson['action'] ?? 'none';
    } catch (e) {
      Logger.warn('AI did not return valid JSON, using raw text.');
      aiText = aiResponse;
      aiAction = 'none';
    }

    if (aiText.isEmpty) {
      Logger.warn('AI returned empty text');
      return;
    }
    Logger.info('AI Response: $aiText');
    Logger.info('AI Action: $aiAction');

    final audioReply = await _textToSpeech(aiText);
    if (audioReply == null) {
      Logger.error('TTS generation failed or timed out');
      broadcastMessage("no_data", "no_data");
      return;
    }
    try {
      final ttsFile = File('tts.pcm');
      await ttsFile.writeAsBytes(audioReply);
      Logger.success('TTS audio saved to: ${ttsFile.absolute.path}');
      broadcastMessage("play_audio", aiAction);
      Logger.warn(
        'Waiting for client to finish playing (Auto-unlock in 60s)...',
      );
      _autoUnlockTimer?.cancel();

      _autoUnlockTimer = Timer(Duration(seconds: 60), () {
        if (isProcessing || currentClientAddr != null) {
          Logger.warn(
            'Client unlock timeout (60s). Forcing UDP session unlock.',
          );
          isProcessing = false;
          currentClientAddr = null;
          currentClientPort = null;
        }
      });
      // ------------------------------------
    } catch (e) {
      Logger.error('Failed to save TTS file: $e');
      // 出错时也应该考虑是否需要立即解锁
      isProcessing = false;
    }
  } catch (e) {
    Logger.error('Pipeline Error: $e');
    // 管道级错误也要解锁，否则死锁
    isProcessing = false;
    currentClientAddr = null;
    currentClientPort = null;
  }
}

// 日志中间件支持
Middleware customLogRequests() {
  return (Handler innerHandler) {
    return (Request request) async {
      final watch = Stopwatch()..start();
      final response = await innerHandler(request);
      Logger.api(
        request.method,
        response.statusCode,
        '${request.url} ${watch.elapsedMilliseconds}ms',
      );
      return response;
    };
  };
}

/// 调用硅基流动 API
Future<String?> _asr(File audioFile) async {
  try {
    Logger.info('Sending audio file to ASR API: ${audioFile.path}');
    var uri = Uri.parse('https://api.siliconflow.cn/v1/audio/transcriptions');
    var request = http.MultipartRequest('POST', uri);

    request.headers['Authorization'] = 'Bearer ${Config.apiKey}';
    request.fields['model'] = 'FunAudioLLM/SenseVoiceSmall';
    request.files.add(
      await http.MultipartFile.fromPath('file', audioFile.path),
    );
    var streamedResponse = await request.send().timeout(
      Duration(seconds: 10),
      onTimeout: () {
        throw TimeoutException('ASR Request timed out');
      },
    );

    var response = await http.Response.fromStream(streamedResponse);

    if (response.statusCode == 200) {
      var decodedBody = utf8.decode(response.bodyBytes);
      var json = jsonDecode(decodedBody);
      return json['text'];
    } else {
      Logger.error('ASR API Error: ${response.statusCode} ${response.body}');
      return null;
    }
  } catch (e) {
    Logger.error('ASR Exception: $e');
    return null;
  }
}

/// 构建 WAV 文件头
Uint8List _buildWavHeader(
  int dataLength,
  int sampleRate,
  int channels,
  int bitsPerSample,
) {
  final byteRate = sampleRate * channels * bitsPerSample ~/ 8;
  final blockAlign = channels * bitsPerSample ~/ 8;
  final totalDataLen = dataLength + 36;

  final buffer = BytesBuilder();

  // RIFF chunk
  buffer.add('RIFF'.codeUnits);
  buffer.add(_int32ToBytes(totalDataLen));
  buffer.add('WAVE'.codeUnits);

  // fmt chunk
  buffer.add('fmt '.codeUnits);
  buffer.add(_int32ToBytes(16)); // audio format length
  buffer.add(_int16ToBytes(1)); // PCM = 1
  buffer.add(_int16ToBytes(channels));
  buffer.add(_int32ToBytes(sampleRate));
  buffer.add(_int32ToBytes(byteRate));
  buffer.add(_int16ToBytes(blockAlign));
  buffer.add(_int16ToBytes(bitsPerSample));

  // data chunk
  buffer.add('data'.codeUnits);
  buffer.add(_int32ToBytes(dataLength));

  return buffer.toBytes();
}

List<int> _int32ToBytes(int value) {
  return Uint8List(4)..buffer.asByteData().setInt32(0, value, Endian.little);
}

List<int> _int16ToBytes(int value) {
  return Uint8List(2)..buffer.asByteData().setInt16(0, value, Endian.little);
}

/// TTS
Future<Uint8List?> _textToSpeech(String text) async {
  try {
    Logger.info('Requesting TTS for text: $text');
    var uri = Uri.parse('https://api.siliconflow.cn/v1/audio/speech');

    var response = await http
        .post(
          uri,
          headers: {
            'Authorization': 'Bearer ${Config.apiKey}',
            'Content-Type': 'application/json',
          },
          body: jsonEncode({
            "model": "FunAudioLLM/CosyVoice2-0.5B",
            "input": text,
            "voice": "FunAudioLLM/CosyVoice2-0.5B:diana",
            "response_format": "pcm",
            "sample_rate": 16000,
          }),
        )
        .timeout(
          Duration(seconds: 10),
          onTimeout: () {
            throw TimeoutException('TTS Request timed out');
          },
        );

    if (response.statusCode == 200) {
      return response.bodyBytes;
    } else {
      Logger.error('TTS API Error: ${response.statusCode}');
      return null;
    }
  } catch (e) {
    Logger.error('TTS Exception: $e');
    return null;
  }
}
