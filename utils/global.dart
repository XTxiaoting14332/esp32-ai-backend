import 'dart:io';

import 'package:web_socket_channel/web_socket_channel.dart';

String version = '0.1.0';

/// 用户配置
class Config {
  /// ws连接地址
  static String wsHost = '';

  /// ws连接端口
  static late int wsPort;

  /// udp监听端口
  static late int udpPort;

  /// 硅基流动API Key
  static String apiKey = '';

  /// 智谱AI API Key
  static String zhipuApiKey = '';

  /// 自定义提示词
  static String prompt = '你是一个可爱的桌宠小风，你的回答必须简短有趣。多用语气词。';

  /// 模型
  static String model = 'glm-4-flash';

  /// 自动解锁时间（秒）
  static int autoUnlockSeconds = 60;
}

List<WebSocketChannel> wsChannels = [];
bool isProcessing = false;
InternetAddress? currentClientAddr;
int? currentClientPort;
