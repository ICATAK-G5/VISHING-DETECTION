package com.ictak.vishingdetection

import com.cloudwebrtc.webrtc.FlutterWebRTCPlugin
import org.webrtc.AudioTrack
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channelName = "com.ictak.vishingdetection/audio"
    private val controlledAudioChannelName = "com.ictak.vishingdetection/controlled_audio"
    private val permissionRequestCode = 7314
    private val controlledPermissionRequestCode = 7315
    private var permissionResult: MethodChannel.Result? = null
    private var controlledPermissionResult: MethodChannel.Result? = null
    private var controlledAudioChannel: MethodChannel? = null
    private var controlledStopPending = false
    private val pcmTaps = mutableMapOf<String, WebRtcPcmTap>()

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "requestAudioPermissions" -> requestAudioPermissions(result)
                    "requestControlledAudioPermissions" -> requestControlledAudioPermissions(result)
                    "startAudioSession" -> startAudioSession(call.arguments as? Map<*, *>, result)
                    "startAudioTransportTest" -> startAudioTransportTest(call.arguments as? Map<*, *>, result)
                    "stopAudioSession" -> {
                        stopService(Intent(this, AudioCaptureService::class.java))
                        result.success(true)
                    }
                    "audioSessionState" -> result.success(AudioCaptureService.stateSnapshot())
                    else -> result.notImplemented()
                }
            }
        controlledAudioChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, controlledAudioChannelName)
        if (intent?.action == ControlledAudioForegroundService.ACTION_STOP_REQUESTED) {
            controlledStopPending = true
            setIntent(Intent(intent).apply { action = null })
        }
        controlledAudioChannel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "startForeground" -> startControlledAudioForegroundService(call.arguments as? Map<*, *>, result)
                "stopForeground" -> {
                    stopService(Intent(this, ControlledAudioForegroundService::class.java))
                    controlledStopPending = false
                    result.success(true)
                }
                "consumeStopRequested" -> {
                    val pending = controlledStopPending
                    controlledStopPending = false
                    result.success(pending)
                }
                else -> result.notImplemented()
            }
        }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "com.ictak.vishingdetection/webrtc_pcm")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "startTap" -> startWebRtcPcmTap(call.arguments as? Map<*, *>, result)
                    "stopTap" -> {
                        val streamId = (call.arguments as? Map<*, *>)?.get("streamId") as? String
                        if (streamId != null) pcmTaps.remove(streamId)?.close()
                        result.success(true)
                    }
                    "stopAll" -> {
                        pcmTaps.values.toList().forEach(WebRtcPcmTap::close)
                        pcmTaps.clear()
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun startWebRtcPcmTap(arguments: Map<*, *>?, result: MethodChannel.Result) {
        try {
            val values = arguments ?: error("Missing WebRTC audio tap details")
            val trackId = values["trackId"] as? String ?: error("Missing WebRTC audio track ID")
            val streamId = values["streamId"] as? String ?: error("Missing audio stream ID")
            val role = values["trackRole"] as? String ?: error("Missing audio track role")
            val plugin = FlutterWebRTCPlugin.sharedSingleton ?: error("Flutter WebRTC plugin is not ready")
            val localTrack = plugin.getLocalTrack(trackId)
            val remoteTrack = if (localTrack == null) plugin.getRemoteTrack(trackId) as? AudioTrack else null
            val tap = WebRtcPcmTap(
                trackId = trackId,
                localTrack = localTrack,
                remoteTrack = remoteTrack,
                websocketUrl = values["websocketUrl"] as? String ?: error("Missing audio WebSocket URL"),
                deviceId = values["deviceId"] as? String ?: error("Missing paired device ID"),
                deviceToken = values["token"] as? String ?: error("Missing paired device token"),
                certificatePin = values["fingerprint"] as? String ?: error("Missing desktop certificate pin"),
                callId = values["callId"] as? String ?: error("Missing authorized call ID"),
                streamId = streamId,
                trackRole = role,
            )
            pcmTaps.remove(streamId)?.close()
            pcmTaps[streamId] = tap
            result.success(true)
        } catch (error: Exception) {
            result.error("audio_tap_failed", error.message ?: "Could not attach to the WebRTC audio track.", null)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.action == ControlledAudioForegroundService.ACTION_STOP_REQUESTED) {
            controlledStopPending = true
            setIntent(Intent(intent).apply { action = null })
            controlledAudioChannel?.invokeMethod("stopRequested", null)
        }
    }

    private fun requiredPermissions(): Array<String> {
        val permissions = mutableListOf(
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.READ_PHONE_STATE,
        )
        if (Build.VERSION.SDK_INT >= 33) permissions += Manifest.permission.POST_NOTIFICATIONS
        return permissions.toTypedArray()
    }

    private fun permissionsGranted(): Boolean = requiredPermissions().all {
        checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED
    }

    private fun requestAudioPermissions(result: MethodChannel.Result) {
        if (permissionResult != null) {
            result.error("permission_request_in_progress", "A permission request is already in progress.", null)
            return
        }
        val missing = requiredPermissions().filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) {
            result.success(true)
            return
        }
        permissionResult = result
        requestPermissions(missing.toTypedArray(), permissionRequestCode)
    }

    private fun controlledPermissions(): Array<String> {
        val permissions = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) permissions += Manifest.permission.POST_NOTIFICATIONS
        return permissions.toTypedArray()
    }

    private fun requestControlledAudioPermissions(result: MethodChannel.Result) {
        if (controlledPermissionResult != null) {
            result.error("permission_request_in_progress", "A permission request is already in progress.", null)
            return
        }
        val missing = controlledPermissions().filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isEmpty()) {
            result.success(true)
            return
        }
        controlledPermissionResult = result
        requestPermissions(missing.toTypedArray(), controlledPermissionRequestCode)
    }

    private fun controlledPermissionsGranted(): Boolean = controlledPermissions().all {
        checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED
    }

    private fun startControlledAudioForegroundService(arguments: Map<*, *>?, result: MethodChannel.Result) {
        if (!controlledPermissionsGranted()) {
            result.error("permissions_required", "Microphone and notification permissions are required for the visible live audio service.", null)
            return
        }
        try {
            val intent = Intent(this, ControlledAudioForegroundService::class.java).apply {
                putExtra(ControlledAudioForegroundService.EXTRA_MODE, arguments?.get("mode") as? String ?: "live")
            }
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
            result.success(true)
        } catch (error: Exception) {
            result.error("service_start_failed", error.message ?: "Could not start the controlled audio foreground service.", null)
        }
    }

    @Deprecated("Deprecated in Android API 33; retained for the current Activity permission flow")
    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == permissionRequestCode) {
            val result = permissionResult
            permissionResult = null
            result?.success(permissionsGranted())
        } else if (requestCode == controlledPermissionRequestCode) {
            val result = controlledPermissionResult
            controlledPermissionResult = null
            result?.success(controlledPermissionsGranted())
        }
    }

    private fun startAudioSession(arguments: Map<*, *>?, result: MethodChannel.Result) {
        if (!permissionsGranted()) {
            result.error("permissions_required", "Microphone, call-state, and notification permissions are required.", null)
            return
        }
        val websocketUrl = arguments?.get("websocketUrl") as? String
        val deviceId = arguments?.get("deviceId") as? String
        val token = arguments?.get("token") as? String
        val fingerprint = arguments?.get("fingerprint") as? String
        if (websocketUrl.isNullOrBlank() || deviceId.isNullOrBlank() || token.isNullOrBlank() || fingerprint.isNullOrBlank()) {
            result.error("pairing_required", "Pair this phone with a desktop before arming audio capture.", null)
            return
        }
        val intent = Intent(this, AudioCaptureService::class.java).apply {
            putExtra(AudioCaptureService.EXTRA_WEBSOCKET_URL, websocketUrl)
            putExtra(AudioCaptureService.EXTRA_DEVICE_ID, deviceId)
            putExtra(AudioCaptureService.EXTRA_DEVICE_TOKEN, token)
            putExtra(AudioCaptureService.EXTRA_CERTIFICATE_PIN, fingerprint)
        }
        try {
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
            result.success(true)
        } catch (error: Exception) {
            result.error("service_start_failed", error.message ?: "Could not start the audio protection service.", null)
        }
    }

    private fun startAudioTransportTest(arguments: Map<*, *>?, result: MethodChannel.Result) {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            result.error(
                "microphone_permission_required",
                "Android requires microphone permission for this foreground-service type. The diagnostic sends a generated tone and does not read microphone input.",
                null,
            )
            return
        }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            result.error(
                "notification_permission_required",
                "Allow notifications so Android can show the active audio transport test and its Stop control.",
                null,
            )
            return
        }
        val websocketUrl = arguments?.get("websocketUrl") as? String
        val deviceId = arguments?.get("deviceId") as? String
        val token = arguments?.get("token") as? String
        val fingerprint = arguments?.get("fingerprint") as? String
        if (websocketUrl.isNullOrBlank() || deviceId.isNullOrBlank() || token.isNullOrBlank() || fingerprint.isNullOrBlank()) {
            result.error("pairing_required", "Pair this phone with a desktop before testing audio transport.", null)
            return
        }
        val intent = Intent(this, AudioCaptureService::class.java).apply {
            putExtra(AudioCaptureService.EXTRA_WEBSOCKET_URL, websocketUrl)
            putExtra(AudioCaptureService.EXTRA_DEVICE_ID, deviceId)
            putExtra(AudioCaptureService.EXTRA_DEVICE_TOKEN, token)
            putExtra(AudioCaptureService.EXTRA_CERTIFICATE_PIN, fingerprint)
            putExtra(AudioCaptureService.EXTRA_DIAGNOSTIC_TONE, true)
        }
        try {
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
            result.success(true)
        } catch (error: Exception) {
            result.error("service_start_failed", error.message ?: "Could not start the audio transport test.", null)
        }
    }
}
