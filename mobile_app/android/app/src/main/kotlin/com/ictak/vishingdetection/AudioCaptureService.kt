package com.ictak.vishingdetection

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.Context
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.telephony.PhoneStateListener
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import androidx.core.app.NotificationCompat
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

class AudioCaptureService : Service() {
    companion object {
        const val EXTRA_WEBSOCKET_URL = "websocket_url"
        const val EXTRA_DEVICE_ID = "device_id"
        const val EXTRA_DEVICE_TOKEN = "device_token"
        const val EXTRA_CERTIFICATE_PIN = "certificate_pin"
        const val EXTRA_DIAGNOSTIC_TONE = "diagnostic_tone"
        private const val ACTION_STOP = "com.ictak.vishingdetection.STOP_AUDIO"
        const val ACTION_NOTIFICATION_DISMISSED = "com.ictak.vishingdetection.DISMISS_AUDIO_NOTIFICATION"
        private const val NOTIFICATION_CHANNEL = "audio_protection"
        private const val NOTIFICATION_ID = 4013

        @Volatile private var currentStatus = "stopped"
        @Volatile private var currentMessage = "Call capture is off."
        @Volatile private var currentActive = false
        @Volatile private var currentNotificationTitle = "Armed · microphone off"
        @Volatile private var currentNotificationDetail = "Waiting for Android to report a cellular call in progress."

        fun stateSnapshot(): Map<String, Any> = mapOf(
            "status" to currentStatus,
            "message" to currentMessage,
            "active" to currentActive,
        )

        fun restoreNotification(context: Context) {
            if (!currentActive) return
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.notify(
                NOTIFICATION_ID,
                buildNotification(context, currentNotificationTitle, currentNotificationDetail),
            )
        }

        private fun buildNotification(context: Context, title: String, detail: String): Notification {
            val stopIntent = Intent(context, AudioCaptureService::class.java).setAction(ACTION_STOP)
            val stopPendingIntent = PendingIntent.getService(
                context,
                1,
                stopIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val dismissedIntent = Intent(context, AudioNotificationDismissedReceiver::class.java)
                .setAction(ACTION_NOTIFICATION_DISMISSED)
            val dismissedPendingIntent = PendingIntent.getBroadcast(
                context,
                3,
                dismissedIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val openIntent = context.packageManager.getLaunchIntentForPackage(context.packageName)
            val contentIntent = openIntent?.let {
                PendingIntent.getActivity(context, 2, it, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            }
            return NotificationCompat.Builder(context, NOTIFICATION_CHANNEL)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle(title)
                .setContentText(detail)
                .setStyle(NotificationCompat.BigTextStyle().bigText(detail))
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(contentIntent)
                .setDeleteIntent(dismissedPendingIntent)
                .addAction(android.R.drawable.ic_media_pause, "Stop", stopPendingIntent)
                .build()
        }
    }

    private val executor: ScheduledExecutorService = Executors.newScheduledThreadPool(2)
    @Volatile private var running = false
    @Volatile private var remoteConnected = false
    @Volatile private var monitorReady = false
    @Volatile private var callState = TelephonyManager.CALL_STATE_IDLE
    @Volatile private var captureActive = false
    @Volatile private var socket: WebSocket? = null
    private var client: OkHttpClient? = null
    private var audioRecord: AudioRecord? = null
    private var captureThread: Thread? = null
    private var telephonyManager: TelephonyManager? = null
    private var telephonyCallback: TelephonyCallback? = null
    private var legacyPhoneStateListener: PhoneStateListener? = null
    private var websocketUrl = ""
    private var deviceId = ""
    private var deviceToken = ""
    private var certificatePin = ""
    private var reconnectAttempt = 0
    private var frameSequence = 0L
    private var droppedFrames = 0
    private var consecutiveBackpressure = 0
    private var sessionId: String? = null
    private var latencyProbeTask: ScheduledFuture<*>? = null
    private var diagnosticToneTask: ScheduledFuture<*>? = null
    private var diagnosticConnectTimeout: ScheduledFuture<*>? = null
    @Volatile private var diagnosticToneMode = false
    @Volatile private var diagnosticToneStarted = false
    private val pendingLatencyProbes = ConcurrentHashMap<String, Long>()
    private val stateLock = Any()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action
        if (action == ACTION_STOP) {
            stopSession("stopped", "Call capture was stopped.")
            stopSelf()
            return START_NOT_STICKY
        }
        if (!running) {
            websocketUrl = intent?.getStringExtra(EXTRA_WEBSOCKET_URL).orEmpty()
            deviceId = intent?.getStringExtra(EXTRA_DEVICE_ID).orEmpty()
            deviceToken = intent?.getStringExtra(EXTRA_DEVICE_TOKEN).orEmpty()
            certificatePin = intent?.getStringExtra(EXTRA_CERTIFICATE_PIN).orEmpty()
            diagnosticToneMode = intent?.getBooleanExtra(EXTRA_DIAGNOSTIC_TONE, false) == true
            if (websocketUrl.isBlank() || deviceId.isBlank() || deviceToken.isBlank() || certificatePin.isBlank()) {
                stopSelf()
                return START_NOT_STICKY
            }
            running = true
            createNotificationChannel()
            if (diagnosticToneMode) {
                updateState("arming", "Preparing synthetic audio transport test; the microphone is not being read.")
            } else {
                updateState("arming", "Arming call capture · microphone is off.")
            }
            startAsMicrophoneForegroundService()
            if (!diagnosticToneMode) registerCallStateListener()
            connectToDesktop()
            if (diagnosticToneMode) {
                diagnosticConnectTimeout = executor.schedule({
                    if (running && diagnosticToneMode && !diagnosticToneStarted) {
                        stopSession("error", "Diagnostic tone test could not connect to the desktop audio monitor.")
                        stopSelf()
                    }
                }, 15, TimeUnit.SECONDS)
            }
        }
        return START_NOT_STICKY
    }

    private fun startAsMicrophoneForegroundService() {
        val notification = if (diagnosticToneMode) {
            buildNotification(this, "Audio transport test", "Synthetic 440 Hz tone · no microphone input is used.")
        } else {
            buildNotification(this, "Armed · microphone off", "Waiting for Android to report a cellular call in progress.")
        }
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL,
                "Call protection",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Visible status and stop control for call protection."
            }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun updateState(status: String, message: String) {
        currentStatus = status
        currentMessage = message
        currentActive = running
        if (running) {
            val (title, detail) = when (status) {
                "diagnostic_tone" -> "Audio transport test" to "Sending a synthetic 440 Hz tone · no microphone input is used."
                "capturing" -> "Capturing microphone" to "Android reports a cellular call in progress. Call audio may be incomplete."
                "incoming_call" -> "Call protection armed" to "Incoming call detected · microphone remains off while it is ringing."
                "waiting_for_connection" -> "Call protection armed" to "Desktop unavailable · microphone is off while reconnecting."
                "capture_unavailable" -> "Call protection armed" to message
                "error" -> "Call protection needs attention" to message
                else -> "Armed · microphone off" to "Waiting for Android to report a cellular call in progress."
            }
            currentNotificationTitle = title
            currentNotificationDetail = detail
            val manager = getSystemService(NotificationManager::class.java)
            manager.notify(NOTIFICATION_ID, buildNotification(this, title, detail))
        }
    }

    private fun registerCallStateListener() {
        if (checkSelfPermission(Manifest.permission.READ_PHONE_STATE) != PackageManager.PERMISSION_GRANTED) {
            updateState("error", "Call-state permission is unavailable. Stop and start protection again after allowing it.")
            return
        }
        telephonyManager = getSystemService(TelephonyManager::class.java)
        val manager = telephonyManager ?: return
        try {
            if (Build.VERSION.SDK_INT >= 31) {
                val callback = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
                    override fun onCallStateChanged(state: Int) = handleCallState(state)
                }
                telephonyCallback = callback
                manager.registerTelephonyCallback(mainExecutor, callback)
            } else {
                @Suppress("DEPRECATION")
                val listener = object : PhoneStateListener() {
                    @Suppress("DEPRECATION")
                    override fun onCallStateChanged(state: Int, phoneNumber: String?) {
                        handleCallState(state)
                    }
                }
                legacyPhoneStateListener = listener
                @Suppress("DEPRECATION")
                manager.listen(listener, PhoneStateListener.LISTEN_CALL_STATE)
            }
        } catch (error: SecurityException) {
            updateState("error", "Android did not allow call-state monitoring: ${error.message ?: "permission unavailable"}")
        }
    }

    private fun handleCallState(state: Int) {
        callState = state
        when (state) {
            TelephonyManager.CALL_STATE_RINGING -> {
                stopCapture("Call is ringing; microphone remains off.")
                if (remoteConnected) updateState("incoming_call", "Incoming cellular call detected. Capture starts after Android reports off-hook.")
            }
            TelephonyManager.CALL_STATE_OFFHOOK -> {
                if (remoteConnected) startCaptureIfReady()
                else updateState("waiting_for_connection", "Call is active, but the desktop is unavailable. Microphone is off until connection returns.")
            }
            else -> stopCapture("Call ended; microphone is off.")
        }
    }

    private fun buildPinnedClient(): OkHttpClient {
        val expectedPin = certificatePin.lowercase().replace(":", "").trim()
        require(expectedPin.matches(Regex("[0-9a-f]{64}"))) { "The saved desktop certificate pin is invalid." }
        val trustManager = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                val certificate = chain?.firstOrNull() ?: throw java.security.cert.CertificateException("Desktop sent no certificate.")
                certificate.checkValidity()
                val actualPin = MessageDigest.getInstance("SHA-256")
                    .digest(certificate.encoded)
                    .joinToString("") { byte -> "%02x".format(byte) }
                if (actualPin != expectedPin) throw java.security.cert.CertificateException("Desktop certificate fingerprint changed.")
            }
        }
        val sslContext = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trustManager), null) }
        return OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            // The local desktop certificate is self-signed and pinned during
            // pairing. Its SAN can lag a DHCP/VPN address change, so the exact
            // saved certificate fingerprint is the server identity check.
            .hostnameVerifier { _, session ->
                try {
                    val certificate = session.peerCertificates.firstOrNull() as? X509Certificate
                        ?: return@hostnameVerifier false
                    val actualPin = MessageDigest.getInstance("SHA-256")
                        .digest(certificate.encoded)
                        .joinToString("") { byte -> "%02x".format(byte) }
                    actualPin == expectedPin
                } catch (_: Exception) {
                    false
                }
            }
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .writeTimeout(8, TimeUnit.SECONDS)
            .build()
    }

    private fun connectToDesktop() {
        if (!running || remoteConnected || socket != null) return
        try {
            val pinnedClient = client ?: buildPinnedClient().also { client = it }
            val request = Request.Builder()
                .url(websocketUrl)
                .header("Authorization", "Bearer $deviceToken")
                .header("X-Device-Id", deviceId)
                .build()
            socket = pinnedClient.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    socket = webSocket
                    remoteConnected = false
                    monitorReady = false
                    updateState("arming", "Secure audio connection opened; waiting for desktop monitor confirmation. Microphone is off.")
                    val monitorStartQueued = webSocket.send(JSONObject()
                        .put("type", "monitor.start")
                        .put("protocol_version", 1)
                        .put("mode", if (diagnosticToneMode) "audio_transport_test" else "cellular_protection")
                        .put("call_detection", if (diagnosticToneMode) "none" else "android_telephony_state")
                        .toString())
                    if (!monitorStartQueued) {
                        updateState("waiting_for_connection", "The desktop audio monitor request could not be sent. Microphone is off while reconnecting.")
                        webSocket.close(1011, "Could not send monitor start")
                    }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    try {
                        val message = JSONObject(text)
                        when (message.optString("type")) {
                            "monitor.ready" -> {
                                monitorReady = true
                                remoteConnected = true
                                reconnectAttempt = 0
                                startLatencyProbes()
                                if (diagnosticToneMode) {
                                    diagnosticConnectTimeout?.cancel(false)
                                    diagnosticConnectTimeout = null
                                    startSyntheticToneStream()
                                }
                                else if (callState == TelephonyManager.CALL_STATE_OFFHOOK) startCaptureIfReady()
                                else if (callState == TelephonyManager.CALL_STATE_RINGING) {
                                    updateState("incoming_call", "Incoming cellular call detected. Capture starts after Android reports off-hook.")
                                } else {
                                    updateState("armed", "Desktop audio monitor confirmed. Microphone is off while waiting for Android call state.")
                                }
                            }
                            "audio.ready" -> Unit
                            "audio.probe_ack" -> {
                                val probeId = message.optString("probe_id")
                                val sentNs = pendingLatencyProbes.remove(probeId)
                                if (sentNs != null) {
                                    val roundTripMs = ((SystemClock.elapsedRealtimeNanos() - sentNs).coerceAtLeast(0L)) / 1_000_000.0
                                    sendControl(JSONObject()
                                        .put("type", "audio.metrics")
                                        .put("protocol_version", 1)
                                        .put("dropped_frames", droppedFrames)
                                        .put("round_trip_ms", roundTripMs)
                                        .put("estimated_one_way_ms", roundTripMs / 2.0))
                                }
                            }
                        }
                    } catch (_: Exception) { }
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    remoteConnected = false
                    monitorReady = false
                    webSocket.close(code, reason)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    if (code == 4401 || code == 4403) {
                        handleAuthorizationRevoked(webSocket)
                    } else {
                        handleSocketLost(webSocket, "Desktop closed the audio connection (code $code)${reason.takeIf { it.isNotBlank() }?.let { ": $it" }.orEmpty()}.")
                    }
                }

                override fun onFailure(webSocket: WebSocket, error: Throwable, response: Response?) {
                    if (response?.code == 401 || response?.code == 403) {
                        handleAuthorizationRevoked(webSocket)
                    } else {
                        val responseDetail = response?.let { " (HTTP ${it.code}${it.message.takeIf(String::isNotBlank)?.let { message -> ": $message" }.orEmpty()})" }.orEmpty()
                        val errorDetail = error.message?.takeIf(String::isNotBlank) ?: error.javaClass.simpleName
                        handleSocketLost(webSocket, "Audio connection failed$responseDetail: $errorDetail")
                    }
                }
            })
        } catch (error: Exception) {
            socket = null
            remoteConnected = false
            monitorReady = false
            updateState("waiting_for_connection", "Could not open the desktop audio connection: ${error.message ?: error.javaClass.simpleName}. Microphone remains off while reconnecting.")
            scheduleReconnect()
        }
    }

    @Synchronized
    private fun startLatencyProbes() {
        latencyProbeTask?.cancel(false)
        latencyProbeTask = executor.scheduleAtFixedRate({
            if (!running || !remoteConnected) return@scheduleAtFixedRate
            val current = socket ?: return@scheduleAtFixedRate
            val probeId = UUID.randomUUID().toString()
            val sentNs = SystemClock.elapsedRealtimeNanos()
            pendingLatencyProbes[probeId] = sentNs
            if (pendingLatencyProbes.size > 8) {
                pendingLatencyProbes.entries.firstOrNull()?.let { pendingLatencyProbes.remove(it.key) }
            }
            current.send(JSONObject()
                .put("type", "audio.probe")
                .put("protocol_version", 1)
                .put("probe_id", probeId)
                .put("phone_elapsed_ns", sentNs)
                .toString())
        }, 1, 2, TimeUnit.SECONDS)
    }

    @Synchronized
    private fun handleSocketLost(lostSocket: WebSocket, detail: String) {
        if (socket !== lostSocket) return
        socket = null
        remoteConnected = false
        monitorReady = false
        if (diagnosticToneMode) {
            stopSession("error", "Diagnostic tone test interrupted: $detail")
            stopSelf()
            return
        }
        stopCapture("Desktop connection lost. Microphone stopped while reconnecting.")
        if (running) {
            updateState("waiting_for_connection", "$detail. Microphone is off while the app reconnects.")
            scheduleReconnect()
        }
    }

    @Synchronized
    private fun handleAuthorizationRevoked(lostSocket: WebSocket) {
        if (socket !== lostSocket) return
        socket = null
        remoteConnected = false
        monitorReady = false
        stopSession("error", "This phone is no longer paired. Stop call protection and pair again.")
        stopSelf()
    }

    @Synchronized
    private fun scheduleReconnect() {
        if (!running || remoteConnected || socket != null) return
        reconnectAttempt = (reconnectAttempt + 1).coerceAtMost(6)
        val delaySeconds = (1L shl (reconnectAttempt - 1)).coerceAtMost(30)
        executor.schedule({ connectToDesktop() }, delaySeconds, TimeUnit.SECONDS)
    }

    @Synchronized
    private fun sendControl(message: JSONObject): Boolean {
        val current = socket ?: return false
        if (!remoteConnected || !monitorReady) return false
        return current.send(message.toString())
    }

    @Synchronized
    private fun startCaptureIfReady() {
        if (!running || !remoteConnected || callState != TelephonyManager.CALL_STATE_OFFHOOK || captureActive) return
        if (!hasNotificationPermission()) {
            updateState("capture_unavailable", "Notifications are disabled. Enable them so the active call-capture service remains visible, then try again.")
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            updateState("capture_unavailable", "Microphone permission is no longer available. Stop protection and grant it again.")
            return
        }
        try {
            val minimum = AudioRecord.getMinBufferSize(
                16000,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            if (minimum <= 0) throw IllegalStateException("This device cannot provide 16 kHz mono microphone input.")
            val record = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                16000,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minimum * 2, 640 * 6),
            )
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                record.release()
                throw IllegalStateException("Android could not open the microphone.")
            }
            val id = UUID.randomUUID().toString()
            val start = JSONObject()
                .put("type", "audio.start")
                .put("protocol_version", 1)
                .put("session_id", id)
                .put("source", "cellular_microphone")
                .put("sample_rate", 16000)
                .put("channels", 1)
                .put("sample_format", "pcm_s16le")
                .put("frame_duration_ms", 20)
                .put("frame_bytes", 640)
            if (!sendControl(start)) {
                record.release()
                updateState("waiting_for_connection", "Desktop connection is not ready. Microphone remains off.")
                return
            }
            record.startRecording()
            if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                record.release()
                sendControl(JSONObject().put("type", "audio.stop").put("protocol_version", 1).put("session_id", id))
                throw IllegalStateException("Android did not start microphone capture.")
            }
            audioRecord = record
            sessionId = id
            frameSequence = 0
            droppedFrames = 0
            consecutiveBackpressure = 0
            captureActive = true
            updateState("capturing", "Android reports a cellular call in progress (dialing, active, or on hold). Microphone streaming; direct call audio is not guaranteed.")
            captureThread = Thread({ captureLoop(record, id) }, "vishing-audio-capture").apply { start() }
        } catch (error: SecurityException) {
            updateState("capture_unavailable", "Android denied microphone access: ${error.message ?: "permission unavailable"}")
        } catch (error: Exception) {
            updateState("capture_unavailable", error.message ?: "Microphone capture is unavailable on this device.")
        }
    }

    private fun captureLoop(record: AudioRecord, id: String) {
        val pcm = ByteArray(640)
        var filled = 0
        try {
            while (captureActive && running && callState == TelephonyManager.CALL_STATE_OFFHOOK) {
                if (!hasNotificationPermission()) {
                    throw IllegalStateException("Notifications were disabled. Microphone capture stopped so the service stays visible.")
                }
                val count = record.read(pcm, filled, pcm.size - filled, AudioRecord.READ_BLOCKING)
                if (count < 0) throw IllegalStateException("Microphone read failed ($count).")
                filled += count
                if (filled < pcm.size) continue
                filled = 0
                val current = socket
                if (!remoteConnected || current == null) break
                if (current.queueSize() > 64 * 1024) {
                    droppedFrames++
                    consecutiveBackpressure++
                    if (consecutiveBackpressure >= 10) throw IllegalStateException("Desktop is not keeping up with live audio. Capture stopped instead of buffering a delay.")
                    continue
                }
                consecutiveBackpressure = 0
                val frame = ByteBuffer.allocate(656).order(ByteOrder.BIG_ENDIAN)
                    .put(byteArrayOf('V'.code.toByte(), 'D'.code.toByte(), 'A'.code.toByte(), '1'.code.toByte()))
                    .putInt((frameSequence and 0xffffffffL).toInt())
                    .putLong(SystemClock.elapsedRealtimeNanos())
                    .put(pcm)
                    .array()
                if (!current.send(ByteString.of(*frame))) throw IllegalStateException("Desktop WebSocket is closed.")
                frameSequence++
                if (frameSequence % 50L == 0L) {
                    sendControl(JSONObject()
                        .put("type", "audio.metrics")
                        .put("protocol_version", 1)
                        .put("dropped_frames", droppedFrames))
                }
            }
        } catch (error: Exception) {
            if (captureActive) {
                updateState("capture_unavailable", error.message ?: "Live microphone capture stopped.")
                stopCapture(error.message ?: "Live microphone capture stopped.")
            }
        } finally {
            try {
                if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop()
            } catch (_: IllegalStateException) { }
            record.release()
            synchronized(this) {
                if (audioRecord === record) audioRecord = null
                if (sessionId == id) sessionId = null
            }
        }
    }

    private fun hasNotificationPermission(): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun startSyntheticToneStream() {
        if (!running || !diagnosticToneMode || diagnosticToneStarted || !remoteConnected || !monitorReady) return
        val id = UUID.randomUUID().toString()
        val start = JSONObject()
            .put("type", "audio.start")
            .put("protocol_version", 1)
            .put("session_id", id)
            .put("source", "diagnostic_tone")
            .put("sample_rate", 16000)
            .put("channels", 1)
            .put("sample_format", "pcm_s16le")
            .put("frame_duration_ms", 20)
            .put("frame_bytes", 640)
        if (!sendControl(start)) {
            stopSession("error", "Desktop did not accept the synthetic audio test session.")
            stopSelf()
            return
        }
        sessionId = id
        frameSequence = 0
        droppedFrames = 0
        consecutiveBackpressure = 0
        diagnosticToneStarted = true
        updateState("diagnostic_tone", "Sending a synthetic 440 Hz tone to the desktop. The microphone is not being read.")

        var sampleIndex = 0L
        val startedAt = SystemClock.elapsedRealtimeNanos()
        diagnosticToneTask = executor.scheduleAtFixedRate({
            if (!running || !diagnosticToneMode || sessionId != id) return@scheduleAtFixedRate
            if (SystemClock.elapsedRealtimeNanos() - startedAt >= TimeUnit.SECONDS.toNanos(6)) {
                stopSession("stopped", "Synthetic audio transport test completed. No microphone input was used.")
                stopSelf()
                return@scheduleAtFixedRate
            }
            val current = socket
            if (!remoteConnected || current == null || !monitorReady) {
                stopSession("error", "Synthetic audio transport test was interrupted because the desktop disconnected.")
                stopSelf()
                return@scheduleAtFixedRate
            }
            if (current.queueSize() > 64 * 1024) {
                stopSession("error", "Desktop is not keeping up with the diagnostic tone stream.")
                stopSelf()
                return@scheduleAtFixedRate
            }

            val pcm = ByteBuffer.allocate(640).order(ByteOrder.LITTLE_ENDIAN)
            repeat(320) {
                val angle = 2.0 * Math.PI * 440.0 * sampleIndex / 16000.0
                pcm.putShort((10000.0 * kotlin.math.sin(angle)).toInt().toShort())
                sampleIndex++
            }
            val frame = ByteBuffer.allocate(656).order(ByteOrder.BIG_ENDIAN)
                .put(byteArrayOf('V'.code.toByte(), 'D'.code.toByte(), 'A'.code.toByte(), '1'.code.toByte()))
                .putInt((frameSequence and 0xffffffffL).toInt())
                .putLong(SystemClock.elapsedRealtimeNanos())
                .put(pcm.array())
                .array()
            if (!current.send(ByteString.of(*frame))) {
                stopSession("error", "The desktop WebSocket closed during the diagnostic tone test.")
                stopSelf()
                return@scheduleAtFixedRate
            }
            frameSequence++
            if (frameSequence % 50L == 0L) {
                sendControl(JSONObject()
                    .put("type", "audio.metrics")
                    .put("protocol_version", 1)
                    .put("dropped_frames", droppedFrames))
            }
        }, 0, 20, TimeUnit.MILLISECONDS)
    }

    @Synchronized
    private fun stopCapture(reason: String) {
        if (!captureActive) {
            if (running && callState != TelephonyManager.CALL_STATE_OFFHOOK && remoteConnected) {
                val state = if (callState == TelephonyManager.CALL_STATE_RINGING) "incoming_call" else "armed"
                updateState(state, reason)
            }
            return
        }
        captureActive = false
        val id = sessionId
        if (id != null) {
            sendControl(JSONObject()
                .put("type", "audio.metrics")
                .put("protocol_version", 1)
                .put("dropped_frames", droppedFrames))
            sendControl(JSONObject().put("type", "audio.stop").put("protocol_version", 1).put("session_id", id))
        }
        try {
            if (audioRecord?.recordingState == AudioRecord.RECORDSTATE_RECORDING) audioRecord?.stop()
        } catch (_: IllegalStateException) { }
        val status = when {
            !running -> "stopped"
            !remoteConnected -> "waiting_for_connection"
            callState == TelephonyManager.CALL_STATE_RINGING -> "incoming_call"
            else -> "armed"
        }
        updateState(status, reason)
    }

    private fun stopSession(status: String, message: String) {
        running = false
        captureActive = false
        val record = audioRecord
        try { record?.stop() } catch (_: Exception) { }
        try {
            if (captureThread !== Thread.currentThread()) captureThread?.join(500)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        if (audioRecord === record && record != null) {
            try { record.release() } catch (_: Exception) { }
            audioRecord = null
        }
        val current = socket
        latencyProbeTask?.cancel(false)
        latencyProbeTask = null
        diagnosticToneTask?.cancel(false)
        diagnosticToneTask = null
        diagnosticConnectTimeout?.cancel(false)
        diagnosticConnectTimeout = null
        pendingLatencyProbes.clear()
        if (current != null) {
            sessionId?.let { id ->
                sendControl(JSONObject()
                    .put("type", "audio.stop")
                    .put("protocol_version", 1)
                    .put("session_id", id))
            }
            sendControl(JSONObject().put("type", "monitor.stop").put("protocol_version", 1))
            current.close(1000, "User stopped call protection")
        }
        sessionId = null
        diagnosticToneStarted = false
        diagnosticToneMode = false
        socket = null
        remoteConnected = false
        monitorReady = false
        if (Build.VERSION.SDK_INT >= 31) {
            telephonyCallback?.let { telephonyManager?.unregisterTelephonyCallback(it) }
        } else {
            @Suppress("DEPRECATION")
            telephonyManager?.listen(legacyPhoneStateListener, PhoneStateListener.LISTEN_NONE)
        }
        telephonyCallback = null
        legacyPhoneStateListener = null
        client?.dispatcher?.executorService?.shutdown()
        client?.connectionPool?.evictAll()
        client = null
        updateState(status, message)
        if (Build.VERSION.SDK_INT >= 24) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    override fun onDestroy() {
        stopSession("stopped", "Call capture ended.")
        executor.shutdownNow()
        super.onDestroy()
    }
}
