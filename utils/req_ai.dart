import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:dart_jsonwebtoken/dart_jsonwebtoken.dart';
import 'global.dart';
import 'logger.dart';

class AiService {
  static const String _historyFile = 'history.json';
  static String get _systemPrompt => """${Config.prompt}
你必须使用 JSON 格式回复，格式如下：
{"action": "nod", "text": "回复内容"}
动作名选项: "nod"(点头), "none"(无动作), "shake"(摇头)。
回复不要带任何emoji""";

  /// 生成 JWT
  static String _generateJwt(String apiKey) {
    try {
      final parts = apiKey.split('.');
      if (parts.length != 2) throw Exception('Invalid API Key format');

      final id = parts[0];
      final secret = parts[1];

      final jwt = JWT(
        {
          'api_key': id,
          'exp': DateTime.now().add(Duration(days: 1)).millisecondsSinceEpoch,
          'timestamp': DateTime.now().millisecondsSinceEpoch,
        },
        header: {'alg': 'HS256', 'sign_type': 'SIGN'},
      );

      return jwt.sign(SecretKey(secret), algorithm: JWTAlgorithm.HS256);
    } catch (e) {
      Logger.error('JWT Generation Failed: $e');
      rethrow;
    }
  }

  static Future<List<Map<String, dynamic>>> _updateHistory(
    String role,
    String content,
  ) async {
    List<dynamic> historyList = [];
    final file = File(_historyFile);

    if (file.existsSync()) {
      try {
        final jsonStr = await file.readAsString();
        if (jsonStr.isNotEmpty) {
          historyList = jsonDecode(jsonStr);
        }
      } catch (e) {
        Logger.warn('Failed to parse history.json, starting fresh.');
      }
    }

    /// 追加新消息
    if (content.trim().isNotEmpty) {
      final newMessage = {'role': role, 'content': content};
      historyList.add(newMessage);
    }

    // 限制历史记录长度
    if (historyList.length > 20) {
      historyList = historyList.sublist(historyList.length - 20);
    }

    await file.writeAsString(JsonEncoder.withIndent('  ').convert(historyList));

    return historyList.map((e) => e as Map<String, dynamic>).toList();
  }

  /// 请求 GLM
  static Future<String?> requestGlm(String userMessage) async {
    try {
      final token = _generateJwt(Config.zhipuApiKey);

      // 更新历史记录
      final rawHistory = await _updateHistory('user', userMessage);

      // 构建完整消息列表
      final List<Map<String, dynamic>> allMessages = [
        {'role': 'system', 'content': _systemPrompt},
        ...rawHistory,
      ];
      final validMessages = allMessages.where((msg) {
        final content = msg['content'];
        return content != null && content.toString().trim().isNotEmpty;
      }).toList();
      // -----------------------

      final uri = Uri.parse(
        'https://open.bigmodel.cn/api/paas/v4/chat/completions',
      );

      final response = await http
          .post(
            uri,
            headers: {
              'Authorization': 'Bearer $token',
              'Content-Type': 'application/json',
            },
            body: jsonEncode({
              'model': Config.model,
              'messages': validMessages, // 使用清洗后的列表
            }),
          )
          .timeout(Duration(seconds: 60));

      if (response.statusCode == 200) {
        final data = jsonDecode(utf8.decode(response.bodyBytes));
        final content = data['choices'][0]['message']['content'];
        await _updateHistory('assistant', content);

        return content;
      } else {
        Logger.error('AI API Error: ${response.statusCode} ${response.body}');
        return '{"action": "none", "text": "接口出错了捏... code ${response.statusCode}"}';
      }
    } catch (e) {
      Logger.error('Request AI Exception: $e');
      return '{"action": "none", "text": "脑子短路了捏..."}';
    }
  }
}
