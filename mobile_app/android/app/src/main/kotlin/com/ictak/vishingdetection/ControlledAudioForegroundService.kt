package com.ictak.vishingdetection

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat

/** Keeps a user-started WebRTC microphone session eligible to continue in background. */
class ControlledAudioForegroundService : Service() {
    companion object {
        const val ACTION_NOTIFICATION_DISMISSED = "com.ictak.vishingdetection.DISMISS_CONTROLLED_AUDIO_NOTIFICATION"
        const val ACTION_STOP_REQUESTED = "com.ictak.vishingdetection.STOP_CONTROLLED_AUDIO"
        const val EXTRA_MODE = "controlled_audio_mode"
        private const val CHANNEL_ID = "controlled_audio"
        private const val NOTIFICATION_ID = 4014

        @Volatile private var active = false
        @Volatile private var currentMode = "live"

        fun restoreNotification(context: Context) {
            if (!active) return
            context.getSystemService(NotificationManager::class.java)
                .notify(NOTIFICATION_ID, buildNotification(context, currentMode))
        }

        private fun buildNotification(context: Context, mode: String): Notification {
            val stopIntent = Intent(context, MainActivity::class.java)
                .setAction(ACTION_STOP_REQUESTED)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            val stopPending = PendingIntent.getActivity(
                context,
                4014,
                stopIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val openIntent = context.packageManager.getLaunchIntentForPackage(context.packageName)
            val contentPending = openIntent?.let {
                PendingIntent.getActivity(context, 4015, it, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            }
            val dismissedIntent = Intent(context, AudioNotificationDismissedReceiver::class.java)
                .setAction(ACTION_NOTIFICATION_DISMISSED)
            val dismissedPending = PendingIntent.getBroadcast(
                context,
                4016,
                dismissedIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val live = mode == "live"
            val title = if (live) "Controlled audio streaming" else "Voice message recording"
            val description = if (live) "WebRTC microphone audio is streaming to your paired desktop." else "The user-started voice message is being recorded on this phone."
            val stopDescription = if (live) "User-started WebRTC stream · tap Stop to end the live microphone session." else "User-started voice recording · tap Stop to finish the clip."
            return NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle(title)
                .setContentText(description)
                .setStyle(NotificationCompat.BigTextStyle().bigText(stopDescription))
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(contentPending)
                .setDeleteIntent(dismissedPending)
                .addAction(android.R.drawable.ic_media_pause, "Stop", stopPending)
                .build()
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createNotificationChannel()
        currentMode = intent?.getStringExtra(EXTRA_MODE) ?: "live"
        active = true
        val notification = buildNotification(this, currentMode)
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        active = false
        super.onDestroy()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val channel = NotificationChannel(CHANNEL_ID, "Controlled audio", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Shows when the user-started WebRTC microphone stream is active."
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
}
