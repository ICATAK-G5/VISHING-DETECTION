package com.ictak.vishingdetection

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper

/** Reposts the active protection notification after Android reports its dismissal. */
class AudioNotificationDismissedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action
        if (action != AudioCaptureService.ACTION_NOTIFICATION_DISMISSED &&
            action != ControlledAudioForegroundService.ACTION_NOTIFICATION_DISMISSED
        ) return

        // Android allows this foreground-service notification to be swiped away.
        // Repost it after the dismissal has completed; do not stop protection.
        val appContext = context.applicationContext
        Handler(Looper.getMainLooper()).postDelayed(
            {
                if (action == AudioCaptureService.ACTION_NOTIFICATION_DISMISSED) {
                    AudioCaptureService.restoreNotification(appContext)
                } else {
                    ControlledAudioForegroundService.restoreNotification(appContext)
                }
            },
            400,
        )
    }
}
