package com.ictak.vishingdetection

import android.os.SystemClock
import com.cloudwebrtc.webrtc.LocalTrack
import com.cloudwebrtc.webrtc.audio.LocalAudioTrack
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import org.webrtc.AudioTrack
import org.webrtc.AudioTrackSink
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/** Taps WebRTC's existing decoded/captured PCM; it never opens a second AudioRecord. */
class WebRtcPcmTap(
    trackId: String,
    localTrack: LocalTrack?,
    remoteTrack: AudioTrack?,
    websocketUrl: String,
    deviceId: String,
    deviceToken: String,
    certificatePin: String,
    private val callId: String,
    private val streamId: String,
    private val trackRole: String,
) {
    private val track = (localTrack?.track as? AudioTrack) ?: remoteTrack
        ?: throw IllegalArgumentException("WebRTC audio track $trackId was not found")
    private val localAudioTrack = localTrack as? LocalAudioTrack
    private val client: OkHttpClient = buildPinnedClient(certificatePin)
    private val output = ArrayBlockingQueue<ByteArray>(100)
    private val executor = Executors.newSingleThreadScheduledExecutor()
    private val sessionId = UUID.randomUUID().toString()
    @Volatile private var ready = false
    @Volatile private var closed = false
    private var frameSamples = ShortArray(320)
    private var frameSampleCount = 0
    private var resamplePhase = 0L
    private var filterState = 0.0
    private var sequence = 0L
    private var sender: ScheduledFuture<*>? = null
    private var socket: WebSocket? = null

    private val sink = AudioTrackSink { buffer, bitsPerSample, sampleRate, channels, frames, _ ->
        acceptSamples(buffer, bitsPerSample, sampleRate, channels, frames)
    }

    init {
        require(trackRole == "near" || trackRole == "far") { "Track role must be near or far" }
        if (localAudioTrack != null) localAudioTrack.addSink(sink) else track.addSink(sink)
        val request = Request.Builder()
            .url(websocketUrl)
            .header("Authorization", "Bearer $deviceToken")
            .header("X-Device-Id", deviceId)
            .build()
        socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                socket = webSocket
                webSocket.send(JSONObject()
                    .put("type", "monitor.start")
                    .put("protocol_version", 1)
                    .put("mode", "controlled_webrtc")
                    .put("call_id", callId)
                    .put("stream_id", streamId)
                    .put("track_id", trackRole)
                    .toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    when (JSONObject(text).optString("type")) {
                        "monitor.ready" -> webSocket.send(JSONObject()
                            .put("type", "audio.start")
                            .put("protocol_version", 1)
                            .put("session_id", sessionId)
                            .put("source", "controlled_webrtc")
                            .put("sample_rate", 16000)
                            .put("channels", 1)
                            .put("sample_format", "pcm_s16le")
                            .put("frame_duration_ms", 20)
                            .put("frame_bytes", 640)
                            .toString())
                        "audio.accepted" -> {
                            ready = true
                            sender = executor.scheduleAtFixedRate(::sendOneFrame, 0, 20, TimeUnit.MILLISECONDS)
                        }
                    }
                } catch (_: Exception) {
                    close()
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                close()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                close()
            }
        })
    }

    @Synchronized
    private fun acceptSamples(buffer: ByteBuffer, bitsPerSample: Int, sampleRate: Int, channels: Int, frames: Int) {
        if (closed || bitsPerSample != 16 || sampleRate < 16000 || channels < 1) return
        val input = buffer.duplicate().order(ByteOrder.LITTLE_ENDIAN)
        val availableFrames = minOf(frames, input.remaining() / (2 * channels))
        for (frame in 0 until availableFrames) {
            var mono = 0
            repeat(channels) { mono += input.short.toInt() }
            val sample = (mono / channels).coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt()).toShort()
            val cutoffHz = 7200.0
            val alpha = if (sampleRate > 16000) {
                1.0 - kotlin.math.exp(-2.0 * Math.PI * cutoffHz / sampleRate)
            } else {
                1.0
            }
            filterState += alpha * (sample - filterState)
            resamplePhase += 16000L
            if (resamplePhase >= sampleRate) {
                resamplePhase -= sampleRate.toLong()
                frameSamples[frameSampleCount++] = filterState.toInt()
                    .coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt()).toShort()
                if (frameSampleCount == 320) {
                    val pcm = ByteBuffer.allocate(640).order(ByteOrder.LITTLE_ENDIAN)
                    frameSamples.forEach { pcm.putShort(it) }
                    if (!output.offer(pcm.array())) {
                        socket?.close(1013, "Audio tap queue is full")
                        close()
                        return
                    }
                    frameSampleCount = 0
                }
            }
        }
    }

    private fun sendOneFrame() {
        if (closed || !ready) return
        val pcm = output.poll() ?: return
        val current = socket ?: return
        if (current.queueSize() > 64 * 1024) {
            close()
            return
        }
        val packet = ByteBuffer.allocate(656).order(ByteOrder.BIG_ENDIAN)
            .put(byteArrayOf('V'.code.toByte(), 'D'.code.toByte(), 'A'.code.toByte(), '1'.code.toByte()))
            .putInt((sequence and 0xffffffffL).toInt())
            .putLong(SystemClock.elapsedRealtimeNanos())
            .put(pcm)
            .array()
        if (current.send(ByteString.of(*packet))) sequence++ else close()
    }

    @Synchronized
    fun close() {
        if (closed) return
        closed = true
        ready = false
        sender?.cancel(false)
        sender = null
        if (localAudioTrack != null) localAudioTrack.removeSink(sink) else track.removeSink(sink)
        try {
            socket?.send(JSONObject().put("type", "monitor.stop").put("protocol_version", 1).toString())
        } catch (_: Exception) { }
        socket?.close(1000, "Audio tap stopped")
        socket = null
        client.dispatcher.executorService.shutdown()
        executor.shutdownNow()
        output.clear()
    }

    private fun buildPinnedClient(pin: String): OkHttpClient {
        val expectedPin = pin.lowercase().replace(":", "").trim()
        require(expectedPin.matches(Regex("[0-9a-f]{64}"))) { "Saved desktop certificate pin is invalid" }
        val trustManager = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                val certificate = chain?.firstOrNull() ?: throw java.security.cert.CertificateException("Desktop sent no certificate")
                certificate.checkValidity()
                val actual = MessageDigest.getInstance("SHA-256").digest(certificate.encoded).joinToString("") { "%02x".format(it) }
                if (actual != expectedPin) throw java.security.cert.CertificateException("Desktop certificate fingerprint changed")
            }
        }
        val sslContext = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trustManager), null) }
        return OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            .hostnameVerifier { _, sslSession ->
                try {
                    val certificate = sslSession.peerCertificates.firstOrNull() as? X509Certificate ?: return@hostnameVerifier false
                    MessageDigest.getInstance("SHA-256").digest(certificate.encoded).joinToString("") { "%02x".format(it) } == expectedPin
                } catch (_: Exception) { false }
            }
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .writeTimeout(8, TimeUnit.SECONDS)
            .build()
    }
}
