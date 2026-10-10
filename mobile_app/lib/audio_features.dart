import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:web_socket_channel/io.dart';

const _audioMint = Color(0xFF5DE0BD);
const _audioMuted = Color(0xFF9AACC0);
const _audioPermissionChannel =
    MethodChannel('com.ictak.vishingdetection/audio');
const _controlledAudioChannel =
    MethodChannel('com.ictak.vishingdetection/controlled_audio');
const _webrtcPcmChannel =
    MethodChannel('com.ictak.vishingdetection/webrtc_pcm');

/// Consent-based controlled audio modes, separate from Cellular Protection.
class AudioFeaturesPanel extends StatefulWidget {
  const AudioFeaturesPanel({
    required this.websocketUrl,
    required this.deviceId,
    required this.token,
    required this.fingerprint,
    required this.controlMessages,
    required this.pairedPhones,
    required this.sendControl,
    required this.enabled,
    required this.onMicrophoneUseChanged,
    super.key,
  });

  final String websocketUrl;
  final String deviceId;
  final String token;
  final String fingerprint;
  final Stream<Map<String, dynamic>> controlMessages;
  final List<Map<String, dynamic>> pairedPhones;
  final void Function(Map<String, dynamic>) sendControl;
  final bool enabled;
  final ValueChanged<bool> onMicrophoneUseChanged;

  @override
  State<AudioFeaturesPanel> createState() => _AudioFeaturesPanelState();
}

class _AudioFeaturesPanelState extends State<AudioFeaturesPanel> {
  final _recorder = AudioRecorder();
  final _callId = TextEditingController();
  RTCPeerConnection? _peer;
  MediaStream? _microphone;
  IOWebSocketChannel? _signaling;
  StreamSubscription<dynamic>? _signalSubscription;
  RTCPeerConnection? _phonePeer;
  StreamSubscription<Map<String, dynamic>>? _controlSubscription;
  RTCDataChannel? _voiceChannel;
  MediaStream? _callMicrophone;
  String? _activeCallId;
  String? _peerName;
  String? _activePeerId;
  final List<RTCIceCandidate> _pendingRemoteCandidates = [];
  BytesBuilder? _incomingClip;
  String? _incomingClipId;
  int _incomingClipSize = 0;
  String? _incomingClipHash;
  Completer<bool>? _clipAcceptance;
  String? _outgoingClipId;
  String _role = 'near';
  String? _localRecording;
  String? _localRecordingCallId;
  String _liveStatus = 'stopped';
  String _phoneCallStatus = 'Not in a phone-to-phone call.';
  String _messageStatus = 'No voice message recorded.';
  bool _recording = false;
  bool _busy = false;
  bool _inviteDialogOpen = false;

  @override
  void initState() {
    super.initState();
    _callId.text = _newCallId();
    _controlSubscription = widget.controlMessages.listen(_handleControlMessage);
    _controlledAudioChannel.setMethodCallHandler((call) async {
      if (call.method == 'stopRequested') {
        try {
          await _controlledAudioChannel
              .invokeMethod<bool>('consumeStopRequested');
        } catch (_) {}
        if (_phonePeer != null) {
          await _stopPhoneCall();
        } else if (_recording) {
          await _toggleRecording();
        } else {
          await _stopLiveAudio();
        }
      }
    });
    unawaited(_consumePendingStopRequest());
  }

  Future<void> _consumePendingStopRequest() async {
    try {
      final pending = await _controlledAudioChannel
              .invokeMethod<bool>('consumeStopRequested') ??
          false;
      if (!pending || !mounted) return;
      if (_phonePeer != null) {
        await _stopPhoneCall();
      } else if (_recording) {
        await _toggleRecording();
      } else {
        await _stopLiveAudio();
      }
    } catch (_) {}
  }

  @override
  void dispose() {
    _controlledAudioChannel.setMethodCallHandler(null);
    unawaited(_stopForegroundService());
    _signalSubscription?.cancel();
    _signaling?.sink.close();
    _peer?.close();
    _controlSubscription?.cancel();
    _webrtcPcmChannel.invokeMethod<bool>('stopAll');
    _phonePeer?.close();
    _callMicrophone?.dispose();
    for (final track
        in _microphone?.getTracks() ?? const <MediaStreamTrack>[]) {
      track.stop();
    }
    _microphone?.dispose();
    _recorder.dispose();
    _callId.dispose();
    super.dispose();
  }

  String _newCallId() =>
      'call_${DateTime.now().microsecondsSinceEpoch}_${Random.secure().nextInt(1 << 20).toRadixString(16)}';

  HttpClient _pinnedHttpClient() {
    final expected =
        widget.fingerprint.toLowerCase().replaceAll(':', '').trim();
    final client =
        HttpClient(context: SecurityContext(withTrustedRoots: false));
    client.badCertificateCallback = (certificate, host, port) =>
        sha256.convert(certificate.der).toString().toLowerCase() == expected;
    return client;
  }

  Uri _endpoint(String path, {bool https = false}) {
    final current = Uri.parse(widget.websocketUrl);
    return current.replace(
      scheme:
          https ? (current.scheme == 'wss' ? 'https' : 'http') : current.scheme,
      path: path,
      query: null,
      fragment: null,
    );
  }

  Future<bool> _confirm(String title, String body, String action) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(body, style: const TextStyle(height: 1.45)),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel')),
            FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: Text(action)),
          ],
        ),
      ) ??
      false;

  Future<void> _startLiveAudio() async {
    if (!widget.enabled || _busy || _peer != null || _phonePeer != null) return;
    final id = _callId.text.trim();
    if (id.isEmpty ||
        id.length > 64 ||
        !RegExp(r'^[a-zA-Z0-9_-]+$').hasMatch(id)) {
      _showMessage(
          'Use a call ID containing letters, numbers, underscores, or hyphens.');
      return;
    }
    if (!await _confirm(
      'Start a controlled digital audio session?',
      'After you continue, Android will ask for microphone and notification access. The app sends this phone’s microphone as a live WebRTC audio track to the paired desktop. It is separate from cellular calling and does not capture SIM-call audio. An ongoing notification with Stop remains visible while streaming, including when the app is backgrounded. The desktop temporarily buffers up to 60 seconds for explicit playback. Call ID and role group streams from multiple phones.',
      'Continue',
    )) {
      return;
    }

    setState(() {
      _busy = true;
      _liveStatus = 'Requesting microphone permission';
    });
    IOWebSocketChannel? signal;
    RTCPeerConnection? peer;
    MediaStream? mic;
    HttpClient? pinClient;
    try {
      final permitted = await _audioPermissionChannel
              .invokeMethod<bool>('requestControlledAudioPermissions') ??
          false;
      if (!permitted)
        throw StateError(
            'Allow microphone and notification permissions for controlled audio.');
      mic = await navigator.mediaDevices
          .getUserMedia({'audio': true, 'video': false});
      await _controlledAudioChannel
          .invokeMethod<bool>('startForeground', {'mode': 'live'});
      widget.onMicrophoneUseChanged(true);
      peer = await createPeerConnection({'iceServers': []});
      for (final track in mic.getAudioTracks()) {
        await peer.addTrack(track, mic);
      }
      peer.onConnectionState = (state) {
        if (!mounted) return;
        setState(() => _liveStatus = switch (state) {
              RTCPeerConnectionState.RTCPeerConnectionStateConnected =>
                'Connected · microphone streaming',
              RTCPeerConnectionState.RTCPeerConnectionStateConnecting =>
                'Establishing encrypted media path',
              RTCPeerConnectionState.RTCPeerConnectionStateDisconnected =>
                'Media disconnected',
              RTCPeerConnectionState.RTCPeerConnectionStateFailed =>
                'Media connection failed',
              RTCPeerConnectionState.RTCPeerConnectionStateClosed => 'Stopped',
              _ => 'Negotiating media connection',
            });
      };
      final offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      await _waitForIce(peer);
      final localDescription = await peer.getLocalDescription();
      if (localDescription == null || localDescription.sdp == null) {
        throw StateError('Could not prepare the local WebRTC offer.');
      }

      pinClient = _pinnedHttpClient();
      signal = IOWebSocketChannel.connect(
        _endpoint('/api/v1/mobile/webrtc'),
        customClient: pinClient,
        headers: {
          'Authorization': 'Bearer ${widget.token}',
          'X-Device-ID': widget.deviceId,
        },
      );
      await signal.ready.timeout(const Duration(seconds: 12));
      final answer = Completer<Map<String, dynamic>>();
      final channel = signal;
      final subscription = channel.stream.listen((raw) {
        if (raw is! String) return;
        final message = jsonDecode(raw);
        if (message is! Map<String, dynamic>) return;
        if (message['type'] == 'webrtc.answer' && !answer.isCompleted) {
          answer.complete(message);
        } else if (message['type'] == 'webrtc.error' && !answer.isCompleted) {
          answer.completeError(StateError(message['detail']?.toString() ??
              'Desktop rejected the WebRTC session.'));
        }
      }, onError: (Object error) {
        if (!answer.isCompleted) answer.completeError(error);
      }, onDone: () {
        if (!answer.isCompleted)
          answer.completeError(
              StateError('Desktop closed the signaling connection.'));
      });
      channel.sink.add(jsonEncode({
        'type': 'webrtc.offer',
        'protocol_version': 1,
        'call_id': id,
        'role': _role,
        'sdp': localDescription.sdp,
        'sdp_type': 'offer',
      }));
      final remote = await answer.future.timeout(const Duration(seconds: 25));
      await peer.setRemoteDescription(RTCSessionDescription(
        remote['sdp']?.toString(),
        remote['sdp_type']?.toString() ?? 'answer',
      ));
      if (!mounted) {
        await subscription.cancel();
        await peer.close();
        await mic.dispose();
        await channel.sink.close();
        pinClient.close();
        return;
      }
      setState(() {
        _peer = peer;
        _microphone = mic;
        _signaling = channel;
        _signalSubscription = subscription;
        _liveStatus = 'Connecting media · call $id · $_role';
      });
      pinClient = null;
      signal = null;
      peer = null;
      mic = null;
    } catch (error) {
      widget.onMicrophoneUseChanged(false);
      await _stopForegroundService();
      await peer?.close();
      await mic?.dispose();
      await signal?.sink.close();
      pinClient?.close();
      if (mounted)
        setState(() => _liveStatus = 'Connection needs attention: $error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _waitForIce(RTCPeerConnection peer) async {
    if (peer.iceGatheringState ==
        RTCIceGatheringState.RTCIceGatheringStateComplete) return;
    final done = Completer<void>();
    peer.onIceGatheringState = (state) {
      if (state == RTCIceGatheringState.RTCIceGatheringStateComplete &&
          !done.isCompleted) {
        done.complete();
      }
    };
    await done.future.timeout(const Duration(seconds: 12));
  }

  Future<void> _stopLiveAudio() async {
    final id = _callId.text.trim();
    try {
      _signaling?.sink.add(jsonEncode(
          {'type': 'webrtc.stop', 'protocol_version': 1, 'call_id': id}));
    } catch (_) {}
    await _signalSubscription?.cancel();
    await _signaling?.sink.close();
    await _peer?.close();
    final microphone = _microphone;
    if (microphone != null) {
      for (final track in microphone.getTracks()) {
        await track.stop();
      }
      await microphone.dispose();
    }
    await _stopForegroundService();
    if (mounted) {
      setState(() {
        _peer = null;
        _microphone = null;
        _signaling = null;
        _signalSubscription = null;
        _liveStatus = 'Stopped';
      });
    }
    widget.onMicrophoneUseChanged(false);
  }

  Future<void> _startPhoneCall(String peerId) async {
    if (!widget.enabled ||
        _busy ||
        _peer != null ||
        _phonePeer != null ||
        _recording) return;
    final peers = widget.pairedPhones
        .where((item) =>
            item['connected'] == true && item['device_id'] != widget.deviceId)
        .toList();
    if (peers.isEmpty) {
      _showMessage('Connect another paired phone to this desktop first.');
      return;
    }
    if (!peers.any((item) => item['device_id'] == peerId)) {
      _showMessage('That phone is no longer connected to the desktop.');
      return;
    }
    final peer = peers.firstWhere((item) => item['device_id'] == peerId);
    if (!await _confirm(
        'Start a controlled app call?',
        'This calls ${peer['device_name']}. Both phones exchange live microphone audio inside this app using WebRTC; this is not a SIM call. The target phone must accept. Its near microphone and received far audio are separately streamed to this paired desktop dashboard for analysis.',
        'Call phone')) return;
    // An app-to-app outgoing call is the far/caller side. Stop cellular
    // protection on this phone so it cannot produce a second capture stream.
    try {
      await _audioPermissionChannel.invokeMethod<bool>('stopAudioSession');
    } catch (_) {}
    _activePeerId = peerId;
    _peerName = peer['device_name']?.toString() ?? 'paired phone';
    setState(
        () => _phoneCallStatus = 'Calling $_peerName through the paired desktop…');
    widget.sendControl({'type': 'call.request', 'peer_device_id': peerId});
  }

  Future<void> _handleControlMessage(Map<String, dynamic> message) async {
    final type = message['type']?.toString();
    final id = message['call_id']?.toString();
    if (type == 'call.invite') {
      if (_inviteDialogOpen || _phonePeer != null) {
        widget.sendControl({'type': 'call.reject', 'call_id': id});
        return;
      }
      _activeCallId = id;
      _activePeerId = message['peer_device_id']?.toString();
      _peerName = message['peer_name']?.toString() ?? 'paired phone';
      _inviteDialogOpen = true;
      final accepted = await _confirm(
          'Incoming app call',
          '${_peerName} wants to start a live app-to-app voice call. If accepted, your microphone and the other phone’s incoming voice track will be sent separately to the paired desktop dashboard. This is not a cellular call.',
          'Accept and share audio');
      _inviteDialogOpen = false;
      if (!mounted || _activeCallId != id) return;
      widget.sendControl(
          {'type': accepted ? 'call.accept' : 'call.reject', 'call_id': id});
      if (!accepted) _resetPhoneCall('Call declined.');
      return;
    }
    if (id != null && _activeCallId != null && id != _activeCallId) return;
    try {
      switch (type) {
        case 'desktop.disconnected':
          if (_phonePeer != null)
            await _stopPhoneCall(
                sendHangup: false,
                status: 'Desktop signaling disconnected · call ended.');
          break;
        case 'call.ringing':
          _activeCallId = id;
          _activePeerId = message['peer_device_id']?.toString() ?? _activePeerId;
          _peerName = message['peer_name']?.toString();
          if (mounted)
            setState(() =>
                _phoneCallStatus = 'Ringing ${_peerName ?? 'paired phone'}…');
          break;
        case 'call.ready':
          _activeCallId = id;
          _activePeerId = message['peer_device_id']?.toString() ?? _activePeerId;
          _peerName = message['peer_name']?.toString();
          await _preparePhoneCall(message['role']?.toString() ?? '');
          break;
        case 'call.offer':
          final peer = _phonePeer;
          if (peer == null) throw StateError('Call media is not ready.');
          await peer.setRemoteDescription(
              RTCSessionDescription(message['sdp']?.toString(), 'offer'));
          await _flushRemoteCandidates();
          final answer = await peer.createAnswer();
          await peer.setLocalDescription(answer);
          final local = await peer.getLocalDescription();
          widget.sendControl({
            'type': 'call.answer',
            'call_id': id,
            'sdp': local?.sdp,
            'sdp_type': 'answer'
          });
          break;
        case 'call.answer':
          await _phonePeer?.setRemoteDescription(
              RTCSessionDescription(message['sdp']?.toString(), 'answer'));
          await _flushRemoteCandidates();
          break;
        case 'call.ice':
          final candidate = message['candidate'];
          if (candidate is Map) {
            final item = RTCIceCandidate(
                candidate['candidate']?.toString(),
                candidate['sdpMid']?.toString(),
                candidate['sdpMLineIndex'] as int?);
            if (await _phonePeer?.getRemoteDescription() == null) {
              _pendingRemoteCandidates.add(item);
            } else {
              await _phonePeer?.addCandidate(item);
            }
          }
          break;
        case 'call.voice':
          // Voice-message data is carried on the negotiated WebRTC data channel.
          break;
        case 'call.connected':
          if (mounted)
            setState(() => _phoneCallStatus =
                'Connected · $_role · ${_peerName ?? 'paired phone'}');
          break;
        case 'call.rejected':
          await _stopPhoneCall(
              sendHangup: false, status: 'The other phone declined the call.');
          break;
        case 'call.ended':
          await _stopPhoneCall(
              sendHangup: false,
              status: message['reason']?.toString() ?? 'Call ended.');
          break;
        case 'call.error':
          final detail =
              message['detail']?.toString() ?? 'Call request failed.';
          if (_phonePeer != null)
            await _stopPhoneCall(sendHangup: false, status: detail);
          else
            _resetPhoneCall(detail);
          break;
      }
    } catch (error) {
      await _stopPhoneCall(
          sendHangup: true, status: 'Call setup failed: $error');
    }
  }

  Future<void> _preparePhoneCall(String role) async {
    if (_phonePeer != null) return;
    if (role != 'near' && role != 'far')
      throw StateError('Desktop assigned an invalid call role.');
    final permitted = await _audioPermissionChannel
            .invokeMethod<bool>('requestControlledAudioPermissions') ??
        false;
    if (!permitted)
      throw StateError(
          'Allow microphone and notification permissions for the call.');
    final mic = await navigator.mediaDevices.getUserMedia({
      'audio': {
        'echoCancellation': true,
        'noiseSuppression': true,
        'autoGainControl': true,
      },
      'video': false,
    });
    await _controlledAudioChannel
        .invokeMethod<bool>('startForeground', {'mode': 'live'});
    // Keep the call on the private earpiece route by default. Forcing
    // loudspeaker on both phones can create acoustic feedback/howling.
    await Helper.setSpeakerphoneOn(false);
    final peer = await createPeerConnection({'iceServers': []});
    final track = mic.getAudioTracks().first;
    await peer.addTrack(track, mic);
    _callMicrophone = mic;
    _phonePeer = peer;
    _role = role;
    final localTrackId = track.id;
    if (localTrackId == null || localTrackId.isEmpty)
      throw StateError('WebRTC did not provide a local audio track ID.');
    peer.onIceCandidate = (candidate) {
      if (candidate.candidate != null)
        widget.sendControl({
          'type': 'call.ice',
          'call_id': _activeCallId,
          'candidate': candidate.toMap()
        });
    };
    peer.onDataChannel = _attachVoiceChannel;
    peer.onTrack = (event) {
      if (event.track.kind != 'audio' || role != 'near') return;
      final remoteTrackId = event.track.id;
      if (remoteTrackId == null || remoteTrackId.isEmpty) return;
      unawaited(Helper.setSpeakerphoneOn(false));
      unawaited(_startDesktopTap(remoteTrackId, 'far'));
    };
    peer.onConnectionState = (state) {
      if (state == RTCPeerConnectionState.RTCPeerConnectionStateConnected) {
        widget
            .sendControl({'type': 'call.connected', 'call_id': _activeCallId});
        if (mounted)
          setState(() => _phoneCallStatus =
              'Connected · $_role · ${_peerName ?? 'paired phone'}');
      } else if (mounted) {
        setState(() => _phoneCallStatus = switch (state) {
              RTCPeerConnectionState.RTCPeerConnectionStateConnecting =>
                'Connecting securely to ${_peerName ?? 'paired phone'}…',
              RTCPeerConnectionState.RTCPeerConnectionStateDisconnected =>
                'Call connection interrupted',
              RTCPeerConnectionState.RTCPeerConnectionStateFailed =>
                'Call connection failed · check same Wi-Fi',
              RTCPeerConnectionState.RTCPeerConnectionStateClosed =>
                'Call ended',
              _ => _phoneCallStatus,
            });
      }
    };
    widget.onMicrophoneUseChanged(true);
    if (role == 'near') {
      final init = RTCDataChannelInit()..ordered = true;
      _attachVoiceChannel(await peer.createDataChannel('voice-messages', init));
      await _startDesktopTap(localTrackId, 'near');
      final offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      final local = await peer.getLocalDescription();
      widget.sendControl({
        'type': 'call.offer',
        'call_id': _activeCallId,
        'sdp': local?.sdp,
        'sdp_type': 'offer'
      });
    }
    if (mounted) setState(() => _phoneCallStatus = 'Starting $_role audio…');
  }

  Future<void> _startDesktopTap(String trackId, String role) async {
    final callId = _activeCallId;
    if (callId == null) throw StateError('Missing desktop-authorized call ID.');
    await _webrtcPcmChannel.invokeMethod<bool>('startTap', {
      'trackId': trackId,
      'streamId': '${role}_${DateTime.now().microsecondsSinceEpoch}',
      'trackRole': role,
      'websocketUrl': _endpoint('/api/v1/mobile/audio').toString(),
      'deviceId': widget.deviceId,
      'token': widget.token,
      'fingerprint': widget.fingerprint,
      'callId': callId,
    });
  }

  void _attachVoiceChannel(RTCDataChannel channel) {
    _voiceChannel = channel;
    channel.onMessage =
        (message) => unawaited(_handleVoiceDataMessage(message));
  }

  Future<void> _handleVoiceDataMessage(RTCDataChannelMessage message) async {
    final channel = _voiceChannel;
    if (channel == null) return;
    if (message.isBinary) {
      final buffer = _incomingClip;
      if (buffer == null) return;
      buffer.add(message.binary);
      if (buffer.length > _incomingClipSize) {
        _incomingClip = null;
        await channel.send(RTCDataChannelMessage(jsonEncode({
          'type': 'voice.reject',
          'reason': 'Clip exceeded its declared size.'
        })));
        return;
      }
      return;
    }
    Map<String, dynamic> payload;
    try {
      payload = Map<String, dynamic>.from(jsonDecode(message.text) as Map);
    } catch (_) {
      return;
    }
    switch (payload['type']) {
      case 'voice.offer':
        final size = payload['size'];
        final transferId = payload['transfer_id']?.toString();
        if (size is! int ||
            size <= 0 ||
            size > 25 * 1024 * 1024 ||
            transferId == null) {
          await channel.send(RTCDataChannelMessage(jsonEncode({
            'type': 'voice.reject',
            'reason': 'Clip is invalid or exceeds 25 MB.'
          })));
          return;
        }
        final accepted = await _confirm(
            'Receive voice message?',
            '${payload['name'] ?? 'Voice message'} · ${(size / 1024).ceil()} KB. The clip will be transferred to this phone over the encrypted app call. You can then send it to the paired desktop for temporary analysis.',
            'Receive clip');
        if (!accepted || !mounted) {
          await channel.send(RTCDataChannelMessage(
              jsonEncode({'type': 'voice.reject', 'transfer_id': transferId})));
          return;
        }
        _incomingClip = BytesBuilder(copy: false);
        _incomingClipId = transferId;
        _incomingClipSize = size;
        _incomingClipHash = payload['sha256']?.toString();
        await channel.send(RTCDataChannelMessage(
            jsonEncode({'type': 'voice.accept', 'transfer_id': transferId})));
        if (mounted)
          setState(() => _messageStatus =
              'Receiving voice message from ${_peerName ?? 'paired phone'}…');
        break;
      case 'voice.accept':
        if (payload['transfer_id'] == _outgoingClipId &&
            _clipAcceptance != null &&
            !_clipAcceptance!.isCompleted) _clipAcceptance!.complete(true);
        break;
      case 'voice.reject':
        if ((payload['transfer_id'] == null ||
                payload['transfer_id'] == _outgoingClipId) &&
            _clipAcceptance != null &&
            !_clipAcceptance!.isCompleted) _clipAcceptance!.complete(false);
        if (mounted)
          setState(() => _messageStatus = payload['reason']?.toString() ??
              'The other phone declined the voice message.');
        break;
      case 'voice.complete':
        if (payload['transfer_id'] != _incomingClipId || _incomingClip == null)
          return;
        final bytes = _incomingClip!.takeBytes();
        _incomingClip = null;
        if (bytes.length != _incomingClipSize) {
          if (mounted)
            setState(() =>
                _messageStatus = 'Voice message transfer was incomplete.');
          return;
        }
        final expectedHash = payload['sha256']?.toString();
        if (expectedHash == null ||
            expectedHash != _incomingClipHash ||
            sha256.convert(bytes).toString() != expectedHash) {
          if (mounted)
            setState(
                () => _messageStatus = 'Voice message integrity check failed.');
          return;
        }
        final directory = await getTemporaryDirectory();
        final safeName = 'received_${_newCallId()}.m4a';
        final file =
            File('${directory.path}${Platform.pathSeparator}$safeName');
        await file.writeAsBytes(bytes, flush: true);
        if (mounted)
          setState(() {
            _localRecording = file.path;
            _localRecordingCallId = _activeCallId;
            _messageStatus =
                'Voice message received · ready to send to desktop.';
          });
        break;
    }
  }

  Future<void> _sendRecordedClipToPeer() async {
    final path = _localRecording;
    final channel = _voiceChannel;
    if (path == null || channel == null || _phonePeer == null || _busy) return;
    final file = File(path);
    final size = await file.length();
    if (size <= 0 || size > 25 * 1024 * 1024) {
      _showMessage('Voice messages must be between 1 byte and 25 MB.');
      return;
    }
    if (!await _confirm(
        'Send voice message?',
        'Send this recorded clip to ${_peerName ?? 'the other phone'} over the active encrypted app call? The recipient must accept before the file is transferred.',
        'Send clip')) return;
    final transferId = _newCallId();
    _outgoingClipId = transferId;
    final bytes = await file.readAsBytes();
    final digest = sha256.convert(bytes).toString();
    final acceptance = Completer<bool>();
    _clipAcceptance = acceptance;
    setState(() =>
        _messageStatus = 'Waiting for the other phone to accept the clip…');
    await channel.send(RTCDataChannelMessage(jsonEncode({
      'type': 'voice.offer',
      'transfer_id': transferId,
      'name': path.split(Platform.pathSeparator).last,
      'size': size,
      'sha256': digest
    })));
    try {
      if (!await acceptance.future.timeout(const Duration(seconds: 90))) return;
      for (var offset = 0; offset < bytes.length; offset += 12 * 1024) {
        final end = min(offset + 12 * 1024, bytes.length);
        await channel.send(RTCDataChannelMessage.fromBinary(
            Uint8List.fromList(bytes.sublist(offset, end))));
        while ((await channel.getBufferedAmount()) > 512 * 1024) {
          await Future<void>.delayed(const Duration(milliseconds: 30));
        }
      }
      await channel.send(RTCDataChannelMessage(jsonEncode({
        'type': 'voice.complete',
        'transfer_id': transferId,
        'sha256': digest
      })));
      if (mounted)
        setState(() => _messageStatus =
            'Voice message sent to ${_peerName ?? 'the other phone'}.');
    } on TimeoutException {
      if (mounted)
        setState(() => _messageStatus =
            'The other phone did not respond to the voice message request.');
    } catch (error) {
      if (mounted)
        setState(
            () => _messageStatus = 'Voice message transfer failed: $error');
    } finally {
      _clipAcceptance = null;
      _outgoingClipId = null;
    }
  }

  Future<void> _flushRemoteCandidates() async {
    final peer = _phonePeer;
    if (peer == null || await peer.getRemoteDescription() == null) return;
    for (final candidate
        in List<RTCIceCandidate>.from(_pendingRemoteCandidates)) {
      await peer.addCandidate(candidate);
    }
    _pendingRemoteCandidates.clear();
  }

  Future<void> _stopPhoneCall(
      {bool sendHangup = true, String status = 'Call ended.'}) async {
    final id = _activeCallId;
    if (sendHangup && id != null)
      widget.sendControl({'type': 'call.end', 'call_id': id});
    await _webrtcPcmChannel.invokeMethod<bool>('stopAll');
    await _voiceChannel?.close();
    _voiceChannel = null;
    _incomingClip = null;
    _incomingClipId = null;
    _incomingClipHash = null;
    await _phonePeer?.close();
    final mic = _callMicrophone;
    if (mic != null) {
      for (final track in mic.getTracks()) {
        await track.stop();
      }
      await mic.dispose();
    }
    await _stopForegroundService();
    _resetPhoneCall(status);
  }

  void _resetPhoneCall(String status) {
    _phonePeer = null;
    _callMicrophone = null;
    _activeCallId = null;
    _activePeerId = null;
    _peerName = null;
    _role = 'near';
    _pendingRemoteCandidates.clear();
    widget.onMicrophoneUseChanged(false);
    if (mounted) setState(() => _phoneCallStatus = status);
  }

  Future<void> _toggleRecording() async {
    if (_busy ||
        _peer != null ||
        _phonePeer != null ||
        (!_recording && !widget.enabled)) return;
    if (_recording) {
      final path = await _recorder.stop();
      if (!mounted) return;
      setState(() {
        _recording = false;
        _localRecording = path;
        _messageStatus = path == null
            ? 'Recording was not saved.'
            : 'Voice message ready to send.';
      });
      await _stopForegroundService();
      widget.onMicrophoneUseChanged(false);
      return;
    }
    if (!await _confirm(
      'Record a voice message?',
      'Recording starts only after you confirm. An ongoing notification provides a Stop action while recording, including if the app is backgrounded. The clip is sent over the paired encrypted connection, converted to 16 kHz mono PCM WAV, and stored on this desktop for up to 24 hours. You can delete it from the desktop dashboard at any time.',
      'Start recording',
    )) {
      return;
    }
    try {
      final permitted = await _audioPermissionChannel
              .invokeMethod<bool>('requestControlledAudioPermissions') ??
          false;
      if (!permitted) {
        _showMessage(
            'Allow microphone and notification permissions to record a voice message.');
        return;
      }
      if (!await _recorder.hasPermission()) {
        _showMessage(
            'Microphone permission is required to record a voice message.');
        return;
      }
      final directory = await getTemporaryDirectory();
      final path =
          '${directory.path}${Platform.pathSeparator}${_newCallId()}.m4a';
      await _controlledAudioChannel
          .invokeMethod<bool>('startForeground', {'mode': 'recording'});
      await _recorder.start(
        const RecordConfig(
            encoder: AudioEncoder.aacLc,
            sampleRate: 16000,
            numChannels: 1,
            bitRate: 32000),
        path: path,
      );
      if (mounted) {
        setState(() {
          _recording = true;
          _localRecording = null;
          _localRecordingCallId = null;
          _messageStatus = 'Recording · press Stop when finished.';
        });
      }
      widget.onMicrophoneUseChanged(true);
    } catch (error) {
      await _stopForegroundService();
      widget.onMicrophoneUseChanged(false);
      _showMessage('Could not start recording: $error');
    }
  }

  Future<void> _stopForegroundService() async {
    try {
      await _controlledAudioChannel.invokeMethod<bool>('stopForeground');
    } catch (_) {}
  }

  Future<void> _uploadRecording() async {
    final path = _localRecording;
    if (path == null || !widget.enabled || _busy) return;
    final id = _localRecordingCallId ?? _activeCallId ?? _callId.text.trim();
    if (id.isEmpty || !RegExp(r'^[a-zA-Z0-9_-]{1,64}$').hasMatch(id)) {
      _showMessage('Enter a valid call ID before sending.');
      return;
    }
    setState(() {
      _busy = true;
      _messageStatus = 'Uploading securely…';
    });
    final client = IOClient(_pinnedHttpClient());
    try {
      final request = http.MultipartRequest(
          'POST', _endpoint('/api/v1/mobile/voice-messages', https: true))
        ..headers.addAll({
          'Authorization': 'Bearer ${widget.token}',
          'X-Device-ID': widget.deviceId
        })
        ..fields['call_id'] = id
        ..files.add(await http.MultipartFile.fromPath('audio', path));
      final response =
          await client.send(request).timeout(const Duration(minutes: 2));
      final body = jsonDecode(await response.stream.bytesToString());
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw StateError(body is Map
            ? body['detail']?.toString() ??
                'Upload failed (${response.statusCode}).'
            : 'Upload failed (${response.statusCode}).');
      }
      await File(path).delete().catchError((_) => File(path));
      if (mounted) {
        setState(() {
          _localRecording = null;
          _localRecordingCallId = null;
          _messageStatus =
              'Voice message delivered to desktop · ${body['duration_seconds']} seconds · retained up to 24 hours.';
        });
      }
    } catch (error) {
      if (mounted) setState(() => _messageStatus = 'Send failed: $error');
    } finally {
      client.close();
      if (mounted) setState(() => _busy = false);
    }
  }

  void _showMessage(String message) {
    if (mounted)
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(18),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('CONTROLLED DIGITAL AUDIO',
                style: TextStyle(
                    color: _audioMuted,
                    fontSize: 11,
                    letterSpacing: 1.5,
                    fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            const Text('App-to-desktop audio',
                style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            const Text(
                'Start a user-controlled WebRTC microphone stream directly to the desktop, or record a voice message and send it for desktop-side analysis. These features do not use cellular call audio. Run only one microphone mode at a time; the live stream is one-way to the desktop and can be stopped here.',
                style:
                    TextStyle(color: _audioMuted, fontSize: 12, height: 1.45)),
            const SizedBox(height: 14),
            const Text('Phone-to-phone controlled call',
                style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 5),
            const Text(
                'Choose a paired phone below to call it. The receiving phone must accept. The desktop assigns the call ID and roles automatically: the caller is far, and the receiving phone is near. Only the receiving phone sends the two analysis streams to the desktop.',
                style:
                    TextStyle(color: _audioMuted, fontSize: 12, height: 1.4)),
            const SizedBox(height: 10),
            if (widget.pairedPhones
                .where((item) => item['device_id'] != widget.deviceId)
                .isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('No other paired phones yet.',
                    style: TextStyle(color: _audioMuted, fontSize: 12)),
              )
            else
              ...widget.pairedPhones
                  .where((item) => item['device_id'] != widget.deviceId)
                  .map((phone) {
                final id = phone['device_id']?.toString() ?? '';
                final name = phone['device_name']?.toString() ?? 'Paired phone';
                final connected = phone['connected'] == true;
                final active = id == _activePeerId;
                final canCall = widget.enabled && !_busy && !_recording &&
                    _peer == null && _phonePeer == null && connected &&
                    _activePeerId == null;
                final canEnd = active && _activeCallId != null;
                return Card(
                  margin: const EdgeInsets.only(bottom: 6),
                  child: ListTile(
                    dense: true,
                    leading: Icon(
                      connected ? Icons.phone_android : Icons.phone_disabled,
                      color: connected ? _audioMint : _audioMuted,
                    ),
                    title: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis),
                    subtitle: Text(active
                        ? _phoneCallStatus
                        : connected
                            ? 'Connected to this desktop'
                            : 'Offline · reconnect this phone to call'),
                    trailing: IconButton(
                      tooltip: active ? 'End call with $name' : 'Call $name',
                      onPressed: canEnd
                          ? _stopPhoneCall
                          : canCall
                              ? () => _startPhoneCall(id)
                              : null,
                      icon: Icon(active ? Icons.call_end : Icons.call),
                      color: active ? Colors.redAccent : _audioMint,
                    ),
                  ),
                );
              }),
            const SizedBox(height: 8),
            Text(_phoneCallStatus,
                style: const TextStyle(
                    color: _audioMint,
                    fontSize: 12,
                    fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            if (_localRecording != null && _phonePeer != null) ...[
              const SizedBox(height: 7),
              OutlinedButton.icon(
                  onPressed: _busy ? null : _sendRecordedClipToPeer,
                  icon: const Icon(Icons.send),
                  label: const Text('Send recorded clip to call peer')),
            ],
            const SizedBox(height: 12),
            const Text('Direct phone-to-desktop stream · role assigned automatically as near',
                style: TextStyle(color: _audioMuted, fontSize: 11)),
            const SizedBox(height: 8),
            Text(_liveStatus,
                style: const TextStyle(
                    color: _audioMint, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: _busy || _recording
                  ? null
                  : (_peer != null
                      ? _stopLiveAudio
                      : widget.enabled
                          ? _startLiveAudio
                          : null),
              icon: Icon(_peer == null ? Icons.call : Icons.call_end),
              label: Text(_peer == null
                  ? 'Start live audio to desktop'
                  : 'Stop live audio'),
              style: FilledButton.styleFrom(
                  backgroundColor: _audioMint,
                  foregroundColor: const Color(0xFF101827),
                  minimumSize: const Size.fromHeight(46)),
            ),
            const Divider(height: 28),
            const Text('Voice message',
                style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            Text(_messageStatus,
                style: const TextStyle(
                    color: _audioMuted, fontSize: 12, height: 1.4)),
            const SizedBox(height: 8),
            Row(children: [
              Expanded(
                  child: OutlinedButton.icon(
                      onPressed: _busy || _peer != null || _phonePeer != null
                          ? null
                          : _recording
                              ? _toggleRecording
                              : widget.enabled
                                  ? _toggleRecording
                                  : null,
                      icon: Icon(_recording ? Icons.stop : Icons.mic),
                      label: Text(_recording
                          ? 'Stop recording'
                          : 'Record voice message'))),
              if (_localRecording != null) ...[
                const SizedBox(width: 8),
                Expanded(
                    child: FilledButton.icon(
                        onPressed:
                            !widget.enabled || _busy ? null : _uploadRecording,
                        icon: const Icon(Icons.upload),
                        label: const Text('Send to desktop'))),
              ],
            ]),
            if (_busy)
              const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: LinearProgressIndicator()),
          ]),
        ),
      );
}
