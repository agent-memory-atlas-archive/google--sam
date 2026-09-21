import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:http/http.dart' as http;

Future<http.Response> exchangeAuthorizationCode({
  required Uri tokenUrl,
  required String clientId,
  required String code,
  required String redirectUri,
  required String verifier,
  http.Client? client,
}) async {
  final binding = WidgetsBinding.instance;
  while (binding.lifecycleState != AppLifecycleState.resumed) {
    final resumed = Completer<void>();
    final listener = AppLifecycleListener(onResume: () {
      if (!resumed.isCompleted) resumed.complete();
    });
    try {
      await resumed.future;
    } finally {
      listener.dispose();
    }
  }

  final requestClient = client ?? http.Client();
  try {
    return await requestClient.post(
      tokenUrl,
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: {
        'grant_type': 'authorization_code',
        'client_id': clientId,
        'code': code,
        'redirect_uri': redirectUri,
        'code_verifier': verifier,
      },
    );
  } finally {
    if (client == null) requestClient.close();
  }
}
