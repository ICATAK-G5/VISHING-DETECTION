import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:web_socket_channel/io.dart';

void main() => runApp(const VishingMobileApp());

const _ink = Color(0xFF101827);
const _mint = Color(0xFF5DE0BD);
const _muted = Color(0xFF9AACC0);
const _storage = FlutterSecureStorage();

class VishingMobileApp extends StatelessWidget {
  const VishingMobileApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Vishing Detection',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          brightness: Brightness.dark,
          scaffoldBackgroundColor: _ink,
          colorScheme: ColorScheme.fromSeed(
              seedColor: _mint, brightness: Brightness.dark),
          useMaterial3: true,
          fontFamily: 'Roboto',
          cardTheme: CardThemeData(
              color: const Color(0xFF172538),
              elevation: 0,
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(20))),
        ),
        home: const ConnectionHome(),
      );
}

enum LinkState {
  starting,
  notPaired,
  pairing,
  awaitingApproval,
  connecting,
  connected,
  disconnected,
  error
}

class ConnectionHome extends StatefulWidget {
  const ConnectionHome({super.key});

  @override
  State<ConnectionHome> createState() => _ConnectionHomeState();
}

class _ConnectionHomeState extends State<ConnectionHome> {
  String? _deviceId;
  String? _deviceToken;
  String? _websocketUrl;
  String? _pin;
  String? _desktopName;
  String? _error;
  bool _pinMismatchObserved = false;
  LinkState _state = LinkState.starting;
  IOWebSocketChannel? _channel;
  Timer? _heartbeat;
  bool _keepConnecting = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    var id = await _storage.read(key: 'device_id');
    if (id == null) {
      final random = Random.secure();
      id =
          'phone_${base64UrlEncode(List<int>.generate(18, (_) => random.nextInt(256))).replaceAll('=', '')}';
      await _storage.write(key: 'device_id', value: id);
    }
    _deviceId = id;
    _deviceToken = await _storage.read(key: 'device_token');
    _websocketUrl = await _storage.read(key: 'desktop_websocket_url');
    _pin = await _storage.read(key: 'desktop_tls_pin');
    _desktopName = await _storage.read(key: 'desktop_name');
    if (!mounted) return;
    setState(() => _state =
        _deviceToken == null ? LinkState.notPaired : LinkState.disconnected);
    if (_deviceToken != null) _connectWithRetry();
  }

  HttpClient _pinnedClient(String fingerprint) {
    final normalized = fingerprint.toLowerCase().replaceAll(':', '').trim();
    final context = SecurityContext(withTrustedRoots: false);
    final client = HttpClient(context: context);
    client.badCertificateCallback = (certificate, host, port) {
      final matches =
          sha256.convert(certificate.der).toString().toLowerCase() ==
              normalized;
      if (!matches) _pinMismatchObserved = true;
      return matches;
    };
    return client;
  }

  Future<void> _scan() async {
    final payload = await Navigator.of(context).push<Map<String, dynamic>>(
      MaterialPageRoute(builder: (_) => const ScanPairingPage()),
    );
    if (payload != null) await _beginPairing(payload);
  }

  Future<void> _manualEntry() async {
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (context) => const ManualPairingDialog(),
    );
    if (result != null) await _beginPairing(result);
  }

  Future<void> _beginPairing(Map<String, dynamic> payload) async {
    IOClient? ioClient;
    try {
      if (payload['version'] != 1 ||
          payload['https_url'] is! String ||
          payload['tls_fingerprint'] is! String ||
          payload['pairing_code'] is! String) {
        throw const FormatException(
            'This pairing code is incomplete or uses an unsupported version.');
      }
      final expires =
          DateTime.tryParse(payload['expires_at']?.toString() ?? '');
      if (expires != null && expires.isBefore(DateTime.now())) {
        throw const FormatException(
            'This pairing code has expired. Start a new code on the desktop.');
      }
      final base = Uri.parse(payload['https_url'].toString());
      if (base.scheme != 'https' || base.host.isEmpty) {
        throw const FormatException('The desktop address must use HTTPS.');
      }
      if ((Platform.isAndroid || Platform.isIOS) &&
          (base.host == '127.0.0.1' ||
              base.host == 'localhost' ||
              base.host == '::1')) {
        throw const FormatException(
            'This QR points to 127.0.0.1, which is this phone, not the desktop. On the desktop, enable local-network access, restart the service, wait until the dashboard says “LAN enabled”, then start pairing again and scan the new QR code.');
      }
      final pin = payload['tls_fingerprint'].toString();
      final claimSecret = _randomSecret();
      _pinMismatchObserved = false;
      final client = _pinnedClient(pin);
      ioClient = IOClient(client);
      if (mounted) {
        setState(() {
          _state = LinkState.pairing;
          _error = null;
          _desktopName = payload['desktop_name']?.toString();
        });
      }
      final response = await ioClient
          .post(
            base.resolve('/api/v1/mobile/pairing/request'),
            headers: {'content-type': 'application/json'},
            body: jsonEncode({
              'pairing_code': payload['pairing_code'],
              'device_id': _deviceId,
              'device_name': Platform.isAndroid ? 'Android phone' : 'iPhone',
              'claim_secret': claimSecret
            }),
          )
          .timeout(const Duration(seconds: 12));
      if (response.statusCode != 200) {
        throw Exception(_serverMessage(response.body));
      }
      final request = jsonDecode(response.body) as Map<String, dynamic>;
      if (mounted) setState(() => _state = LinkState.awaitingApproval);
      final requestId = request['request_id'].toString();
      String? token;
      final deadline = DateTime.now().add(const Duration(minutes: 3));
      while (DateTime.now().isBefore(deadline)) {
        await Future<void>.delayed(const Duration(seconds: 2));
        final status = await ioClient
            .post(
              base.resolve('/api/v1/mobile/pairing/$requestId'),
              headers: {'content-type': 'application/json'},
              body: jsonEncode({'claim_secret': claimSecret}),
            )
            .timeout(const Duration(seconds: 12));
        if (status.statusCode != 200) {
          throw Exception(_serverMessage(status.body));
        }
        final result = jsonDecode(status.body) as Map<String, dynamic>;
        if (result['status'] == 'rejected') {
          throw Exception('The desktop declined this pairing request.');
        }
        if (result['status'] == 'approved') {
          token = result['device_token']?.toString();
          break;
        }
        if (!mounted) return;
      }
      if (token == null || token.isEmpty) {
        throw Exception('Pairing timed out. Start a new code on the desktop.');
      }
      final ws = (payload['websocket_url'] as String?) ??
          base.replace(scheme: 'wss', path: '/api/v1/mobile/ws').toString();
      await _storage.write(key: 'device_token', value: token);
      await _storage.write(key: 'desktop_websocket_url', value: ws);
      await _storage.write(key: 'desktop_tls_pin', value: pin);
      await _storage.write(
          key: 'desktop_name',
          value: payload['desktop_name']?.toString() ??
              'Vishing Detection Desktop');
      _deviceToken = token;
      _websocketUrl = ws;
      _pin = pin;
      _desktopName =
          payload['desktop_name']?.toString() ?? 'Vishing Detection Desktop';
      if (!mounted) return;
      setState(() => _state = LinkState.connecting);
      _connectWithRetry();
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _state = LinkState.error;
        _error = _pinMismatchObserved
            ? 'The desktop certificate does not match this pairing code. Check the desktop identity and scan a fresh code.'
            : _friendlyPairingError(error, payload['https_url']?.toString());
      });
    } finally {
      ioClient?.close();
    }
  }

  String _friendlyPairingError(Object error, String? endpoint) {
    final detail = error.toString();
    final lower = detail.toLowerCase();
    final cannotReach = lower.contains('connection refused') ||
        lower.contains('errno = 111') ||
        lower.contains('errno = 10061') ||
        lower.contains('connection timed out') ||
        lower.contains('failed host lookup');
    if (!cannotReach) {
      return detail.replaceFirst(
          RegExp(r'^(Exception|FormatException):\s*'), '');
    }
    final uri = endpoint == null ? null : Uri.tryParse(endpoint);
    final destination = uri == null ? 'the desktop' : '${uri.host}:${uri.port}';
    return 'Cannot reach the desktop at $destination. Make sure both devices are on the same Wi-Fi. On the desktop, enable local-network access and restart the service; wait for “LAN enabled”, then start pairing again and scan a fresh QR code. The desktop dashboard should show the phone request while it waits for approval.';
  }

  String _serverMessage(String body) {
    try {
      return (jsonDecode(body) as Map<String, dynamic>)['detail']?.toString() ??
          'The desktop rejected the request.';
    } catch (_) {
      return 'The desktop could not complete the request.';
    }
  }

  String _randomSecret() {
    final random = Random.secure();
    return base64UrlEncode(List<int>.generate(36, (_) => random.nextInt(256)))
        .replaceAll('=', '');
  }

  Future<void> _connectWithRetry() async {
    if (_keepConnecting ||
        _deviceToken == null ||
        _websocketUrl == null ||
        _pin == null) {
      return;
    }
    _keepConnecting = true;
    var delay = 1;
    while (_keepConnecting && mounted && _deviceToken != null) {
      try {
        if (mounted) setState(() => _state = LinkState.connecting);
        _pinMismatchObserved = false;
        final statusClient = IOClient(_pinnedClient(_pin!));
        final statusUri = Uri.parse(_websocketUrl!).replace(
          scheme: 'https',
          path: '/api/v1/mobile/status/${Uri.encodeComponent(_deviceId!)}',
          query: null,
        );
        late http.Response deviceStatus;
        try {
          deviceStatus = await statusClient.get(statusUri, headers: {
            'authorization': 'Bearer $_deviceToken',
          }).timeout(const Duration(seconds: 12));
        } finally {
          statusClient.close();
        }
        if (deviceStatus.statusCode == 403) {
          _keepConnecting = false;
          if (mounted) {
            setState(() {
              _state = LinkState.error;
              _error =
                  'This phone is no longer paired. Forget this desktop and pair again.';
            });
          }
          break;
        }
        if (deviceStatus.statusCode != 200) {
          throw Exception('The desktop could not confirm this phone pairing.');
        }
        final client = _pinnedClient(_pin!);
        final channel = IOWebSocketChannel.connect(
          Uri.parse(_websocketUrl!),
          customClient: client,
          headers: {
            'authorization': 'Bearer $_deviceToken',
            'x-device-id': _deviceId!
          },
          pingInterval: const Duration(seconds: 20),
          connectTimeout: const Duration(seconds: 12),
        );
        _channel = channel;
        await channel.ready.timeout(const Duration(seconds: 14));
        _heartbeat?.cancel();
        _heartbeat = Timer.periodic(const Duration(seconds: 25),
            (_) => channel.sink.add(jsonEncode({'type': 'ping'})));
        if (mounted) {
          setState(() {
            _state = LinkState.connected;
            _error = null;
          });
        }
        await for (final message in channel.stream) {
          if (message is String) {
            final data = jsonDecode(message);
            if (data is Map && data['type'] == 'connected' && mounted) {
              setState(() => _state = LinkState.connected);
            }
          }
        }
      } catch (error) {
        if (_pinMismatchObserved) {
          _keepConnecting = false;
          if (mounted) {
            setState(() {
              _state = LinkState.error;
              _error =
                  'The desktop certificate changed. Check its identity and pair again.';
            });
          }
          break;
        }
        if (mounted && _keepConnecting) {
          setState(() {
            _state = LinkState.disconnected;
            _error =
                'Desktop unavailable. Check the network; the app will reconnect automatically.';
          });
        }
      } finally {
        _heartbeat?.cancel();
        _heartbeat = null;
        _channel?.sink.close();
        _channel = null;
      }
      if (!_keepConnecting || !mounted) break;
      await Future<void>.delayed(Duration(seconds: delay));
      delay = min(delay * 2, 30);
    }
    _keepConnecting = false;
  }

  Future<void> _forget() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Remove this pairing?'),
        content: const Text(
          'This clears the saved desktop connection from this phone. To revoke this phone’s access, use the desktop dashboard.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    _keepConnecting = false;
    _heartbeat?.cancel();
    await _channel?.sink.close();
    _channel = null;
    for (final key in [
      'device_token',
      'desktop_websocket_url',
      'desktop_tls_pin',
      'desktop_name'
    ]) {
      await _storage.delete(key: key);
    }
    if (!mounted) return;
    setState(() {
      _deviceToken = null;
      _websocketUrl = null;
      _pin = null;
      _desktopName = null;
      _error = null;
      _state = LinkState.notPaired;
    });
  }

  @override
  void dispose() {
    _keepConnecting = false;
    _heartbeat?.cancel();
    _channel?.sink.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final connected = _state == LinkState.connected;
    final paired = _deviceToken != null;
    return Scaffold(
      appBar:
          AppBar(title: const Text('Vishing Detection'), centerTitle: false),
      body: SafeArea(
          child: ListView(
              padding: const EdgeInsets.fromLTRB(20, 18, 20, 32),
              children: [
            Card(
                child: Padding(
                    padding: const EdgeInsets.all(22),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('DESKTOP CONNECTION',
                              style: TextStyle(
                                  color: _muted,
                                  fontSize: 11,
                                  letterSpacing: 1.7,
                                  fontWeight: FontWeight.w700)),
                          const SizedBox(height: 10),
                          Text(
                              connected
                                  ? 'Phone connected.'
                                  : _state == LinkState.awaitingApproval
                                      ? 'Waiting for desktop approval.'
                                      : paired
                                          ? 'Trying to connect.'
                                          : 'Connect to your desktop.',
                              style: const TextStyle(
                                  fontSize: 26,
                                  fontWeight: FontWeight.w800,
                                  height: 1.15)),
                          const SizedBox(height: 12),
                          Text(
                              _desktopName ??
                                  'Pair this phone with the Vishing Detection desktop service on your private network.',
                              style:
                                  const TextStyle(color: _muted, height: 1.5)),
                          const SizedBox(height: 23),
                          ConnectionPath(connected: connected),
                          const SizedBox(height: 19),
                          Row(children: [
                            Icon(
                                connected
                                    ? Icons.check_circle
                                    : Icons.circle_outlined,
                                color: connected ? _mint : _muted,
                                size: 17),
                            const SizedBox(width: 9),
                            Text(_stateLabel(_state),
                                style: TextStyle(
                                    color: connected ? _mint : Colors.white70,
                                    fontWeight: FontWeight.w700))
                          ]),
                          if (_error != null) ...[
                            const SizedBox(height: 12),
                            Text(_error!,
                                style: const TextStyle(
                                    color: Color(0xFFFF9DA3), fontSize: 12))
                          ],
                        ]))),
            const SizedBox(height: 16),
            if (!paired) ...[
              FilledButton.icon(
                  onPressed: _state == LinkState.pairing ||
                          _state == LinkState.awaitingApproval
                      ? null
                      : _scan,
                  icon: const Icon(Icons.qr_code_scanner),
                  label: const Text('Scan desktop QR code'),
                  style: FilledButton.styleFrom(
                      backgroundColor: _mint,
                      foregroundColor: _ink,
                      minimumSize: const Size.fromHeight(52))),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                  onPressed: _state == LinkState.pairing ||
                          _state == LinkState.awaitingApproval
                      ? null
                      : _manualEntry,
                  icon: const Icon(Icons.keyboard_alt_outlined),
                  label: const Text('Enter pairing details manually'),
                  style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(50))),
            ] else ...[
              OutlinedButton.icon(
                  onPressed: _forget,
                  icon: const Icon(Icons.link_off),
                  label: const Text('Remove pairing from this phone'),
                  style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(50))),
            ],
            const SizedBox(height: 22),
            const Card(
                child: Padding(
                    padding: EdgeInsets.all(17),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('Connection only',
                              style: TextStyle(fontWeight: FontWeight.w700)),
                          SizedBox(height: 6),
                          Text(
                              'This screen shows whether the phone is paired and connected. Audio capture and analysis are not active. Cellular call audio access depends on the phone and operating system.',
                              style: TextStyle(
                                  color: _muted, fontSize: 12, height: 1.5)),
                        ]))),
          ])),
    );
  }

  String _stateLabel(LinkState state) => switch (state) {
        LinkState.starting => 'Starting',
        LinkState.notPaired => 'Not paired',
        LinkState.pairing => 'Sending pairing request',
        LinkState.awaitingApproval => 'Approve this phone on the desktop',
        LinkState.connecting => 'Connecting securely',
        LinkState.connected => 'Connected to desktop',
        LinkState.disconnected => 'Disconnected · retrying',
        LinkState.error => 'Connection needs attention',
      };
}

class ConnectionPath extends StatelessWidget {
  const ConnectionPath({required this.connected, super.key});
  final bool connected;
  @override
  Widget build(BuildContext context) => Row(children: [
        _node(Icons.smartphone, 'PHONE', connected),
        Expanded(
            child: Container(
                height: 2,
                margin: const EdgeInsets.symmetric(horizontal: 8),
                color: connected ? _mint : Colors.white24)),
        _node(Icons.desktop_windows, 'DESKTOP', connected),
      ]);
  Widget _node(IconData icon, String label, bool connected) =>
      Column(children: [
        Container(
            width: 54,
            height: 54,
            decoration: BoxDecoration(
                color: connected
                    ? _mint.withValues(alpha: .12)
                    : Colors.white.withValues(alpha: .04),
                borderRadius: BorderRadius.circular(15),
                border: Border.all(
                    color: connected
                        ? _mint.withValues(alpha: .5)
                        : Colors.white12)),
            child: Icon(icon, color: connected ? _mint : _muted)),
        const SizedBox(height: 7),
        Text(label,
            style: const TextStyle(
                fontSize: 9,
                letterSpacing: 1.2,
                color: _muted,
                fontWeight: FontWeight.w700))
      ]);
}

class ScanPairingPage extends StatefulWidget {
  const ScanPairingPage({super.key});
  @override
  State<ScanPairingPage> createState() => _ScanPairingPageState();
}

class _ScanPairingPageState extends State<ScanPairingPage> {
  bool _handled = false;
  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(title: const Text('Scan desktop QR code')),
      body: Stack(children: [
        MobileScanner(onDetect: (capture) {
          if (_handled) return;
          final raw =
              capture.barcodes.isEmpty ? null : capture.barcodes.first.rawValue;
          if (raw == null) return;
          try {
            final value = jsonDecode(raw) as Map<String, dynamic>;
            _handled = true;
            Navigator.of(context).pop(value);
          } catch (_) {}
        }),
        Align(
            alignment: Alignment.bottomCenter,
            child: Container(
                width: double.infinity,
                padding: const EdgeInsets.all(22),
                color: Colors.black54,
                child: const Text(
                    'Scan the one-time code shown in the desktop dashboard. Check that the desktop is the one you intended to pair.',
                    textAlign: TextAlign.center,
                    style: TextStyle(height: 1.5)))),
      ]));
}

class ManualPairingDialog extends StatefulWidget {
  const ManualPairingDialog({super.key});
  @override
  State<ManualPairingDialog> createState() => _ManualPairingDialogState();
}

class _ManualPairingDialogState extends State<ManualPairingDialog> {
  final _url = TextEditingController();
  final _code = TextEditingController();
  final _fingerprint = TextEditingController();
  String? _validationError;
  @override
  void dispose() {
    _url.dispose();
    _code.dispose();
    _fingerprint.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('Enter pairing details'),
        content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text(
              'Use the values shown on the desktop dashboard. First enable local-network access, restart the desktop service, and create a new pairing code. Both devices must be on the same Wi-Fi.',
              style: TextStyle(color: _muted, height: 1.45)),
          const SizedBox(height: 14),
          TextField(
              controller: _url,
              onChanged: (_) => setState(() => _validationError = null),
              decoration: const InputDecoration(
                  labelText: 'Desktop HTTPS address',
                  hintText: 'https://192.168.1.10:8443',
                  helperText:
                      'Copy the selected Phone address under Network Access. Do not use 127.0.0.1.')),
          TextField(
              controller: _code,
              onChanged: (_) => setState(() => _validationError = null),
              decoration: const InputDecoration(
                  labelText: 'One-time pairing code',
                  helperText: 'Copy the Fallback code beside the desktop QR.')),
          TextField(
              controller: _fingerprint,
              onChanged: (_) => setState(() => _validationError = null),
              decoration: const InputDecoration(
                  labelText: 'TLS fingerprint',
                  hintText: 'Paste the full SHA-256 fingerprint',
                  helperText:
                      'Copy TLS fingerprint under Network Access on the desktop.'),
              maxLines: 2),
          if (_validationError != null) ...[
            const SizedBox(height: 8),
            Text(_validationError!,
                style: const TextStyle(color: Color(0xFFFF9DA3))),
          ],
        ])),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () {
                final base = _url.text.trim().replaceAll(RegExp(r'/$'), '');
                final code = _code.text.trim().toUpperCase();
                final pin = _fingerprint.text
                    .trim()
                    .replaceAll(':', '')
                    .replaceAll(' ', '');
                final uri = Uri.tryParse(base);
                if (uri == null || uri.scheme != 'https' || uri.host.isEmpty) {
                  setState(() => _validationError =
                      'Enter the full HTTPS desktop address, including :8443.');
                  return;
                }
                if (code.isEmpty) {
                  setState(() => _validationError =
                      'Enter the Fallback code shown beside the desktop QR.');
                  return;
                }
                if (pin.length != 64 ||
                    !RegExp(r'^[a-fA-F0-9]{64}$').hasMatch(pin)) {
                  setState(() => _validationError =
                      'Enter the full 64-character SHA-256 TLS fingerprint from the desktop dashboard.');
                  return;
                }
                Navigator.pop(context, {
                  'version': 1,
                  'https_url': base,
                  'websocket_url': uri
                      .replace(scheme: 'wss', path: '/api/v1/mobile/ws')
                      .toString(),
                  'pairing_code': code,
                  'tls_fingerprint': pin,
                  'desktop_name': 'Vishing Detection Desktop'
                });
              },
              child: const Text('Pair'))
        ],
      );
}
