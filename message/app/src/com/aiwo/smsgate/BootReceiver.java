package com.aiwo.smsgate;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** 开机自启 + 看门狗闹钟：唤醒上传服务；服务被杀时直接上传兜底 */
public class BootReceiver extends BroadcastReceiver {
    public static final String WAKE_CHECK = "com.aiwo.smsgate.WAKE_CHECK";

    @Override
    public void onReceive(Context context, Intent intent) {
        LogStore.init(context);
        String a = intent.getAction();
        LogStore.add("看门狗/开机触发 action=" + a);
        new Config(context).setLastWatchdog(System.currentTimeMillis());
        if (Intent.ACTION_BOOT_COMPLETED.equals(a) || WAKE_CHECK.equals(a)) {
            // 先拉起前台服务，保证本进程在补扫/直传期间不被回收
            UploadService.kick(context);
            // 收件箱补扫兜底：广播被 ROM 拦截时找回漏掉的短信
            InboxScanner.scanAndEnqueueAsync(context);
            // 免服务直接上传兜底：Android 12+ 后台无法启动前台服务时，
            // 看门狗仍能把积压队列传完
            DirectUploader.drainAsync(context, null);
            // 一次性精确闹钟：本次触发后立刻安排下一次
            UploadService.scheduleWatchdog(context);
        }
    }
}
