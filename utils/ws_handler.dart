import 'dart:convert';
import 'package:shelf_web_socket/shelf_web_socket.dart';
import 'global.dart';
import 'logger.dart';

var wsHandler = webSocketHandler((webSocket) {
  wsChannels.add(webSocket);
  Logger.success(
    'Websocket connection established. ${wsChannels.length} connection(s) now.',
  );
  webSocket.stream.listen(
    (message) async {
      var wsJson = jsonDecode(message);
      String action = wsJson['action'] ?? '';
      String data = wsJson['data'] ?? '';
      switch (action) {
        case 'ping':
          webSocket.sink.add(jsonEncode({'action': 'pong'}));
          break;
        case 'udp_unlock':
          isProcessing = false;
          currentClientAddr = null;
          currentClientPort = null;
          Logger.info('Session ended. Ready for new input.');
          break;
        default:
          Logger.warn('Unknown action: $action');
      }
    },
    onDone: () async {
      Logger.warn(
        'Websocket connection closed. ${wsChannels.length - 1} connection(s) remaining.',
      );
      wsChannels.remove(webSocket);
    },
    onError: (e) {
      Logger.error('WS Error: $e');
    },
  );
});

void broadcastMessage(String action, dynamic data) {
  final message = jsonEncode({'action': action, 'data': data});

  Logger.info('Broadcasting: $message to ${wsChannels.length} clients');

  for (var ws in wsChannels) {
    try {
      ws.sink.add(message);
    } catch (e) {
      Logger.error('Failed to send to a client: $e');
    }
  }
}
