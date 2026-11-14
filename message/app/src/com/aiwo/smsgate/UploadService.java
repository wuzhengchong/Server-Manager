package com.aiwo.smsgate;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.database.ContentObserver;
import android.net.ConnectivityManager;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.Telephony;
import android.util.Log;

import org.json.JSONObject;

import java.util.List;

/**
 * 前台上传服务：保活核心
 *  - 前台服务 + 常驻通知（Android 8+ 必需，也是保活基础）
 *  - START_STICKY：被系统杀死后自动重启
 *  - 看门狗闹钟：每 10 分钟唤醒检查一次（inexact，不申请精确闹钟权限）
 *  - onTaskRemoved：从最近任务划掉时 2 秒后自动拉起
 *  - 网络恢复 / 新短信到达时立即上传；收件箱 ContentObserver 实时兜底（广播被 ROM 拦截也能秒补）；空闲时 20 秒低频轮询（功耗极低）
 *  - 上传期间短暂持有 WakeLock（60 秒超时），可选屏幕关闭保持唤醒
 *  - 失败退避：30s → 5min，成功后恢复
 */
public class UploadService extends Service {
    private static final String TAG = "SmsGate";
    private static final String CH_ID = "sms_gate_v2";
    private static final int NID = 1;
    private static final long IDLE_POLL_MS = 20_000;      // 空闲轮询
    private static final long WATCHDOG_MS = 10 * 60_000;  // 看门狗间隔

    /** 服务是否存活（检测面板用） */
    public static volatile boolean RUNNING = false;
    /** 收件箱观察者是否已注册成功（检测面板用） */
    public static volatile boolean OBSERVER_OK = false;

    private HandlerThread worker;
    private Handler wh;
    private volatile boolean looping = false;
    private int consecutiveFails = 0;

    private PowerManager.WakeLock uploadLock;  // 上传期间短持
    private PowerManager.WakeLock keepLock;    // 可选长持（费电）

    private final Runnable drainTask = new Runnable() {
        @Override public void run() { drain(); }
    };

    /** 网络恢复立即重试 */
    private final BroadcastReceiver connRecv = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            wh.removeCallbacks(drainTask);
            wh.post(drainTask);
        }
    };

    /**
     * 收件箱内容观察者（ColorOS 拦截短信广播时的实时兜底）：
     * 短信入库由系统电话栈完成、不走本应用广播，只要本服务存活，
     * 收件箱一有新短信就能立刻触发补扫上传（熄屏同样有效）。
     */
    private final Handler uiBus = new Handler(Looper.getMainLooper());
    private ContentObserver smsObserver;
    private final Runnable scanTask = new Runnable() {
        @Override public void run() { InboxScanner.scanAndEnqueueAsync(UploadService.this); }
    };

    public static void kick(Context c) {
        try {
            Config cfg = new Config(c);
            if (!cfg.enabled()) return;
            Intent i = new Intent(c, UploadService.class);
            if (Build.VERSION.SDK_INT >= 26) {
                c.startForegroundService(i);
            } else {
                c.startService(i);
            }
        } catch (Exception e) {
            // Android 14 后台启动限制等场景：服务若已存活会自行轮询队列，
            // 看门狗闹钟也会继续尝试拉起
            Log.w(TAG, "kick: " + e);
        }
    }

    /**
     * 看门狗：精确闹钟（尽量不被 Doze / ColorOS 省电延后）。
     * 一次性闹钟，每次触发后由 BootReceiver 再次安排，避免 repeating 被降级为不精确。
     */
    public static void scheduleWatchdog(Context c) {
        try {
            AlarmManager am = (AlarmManager) c.getSystemService(ALARM_SERVICE);
            if (am == null) return;
            Intent i = new Intent(c, BootReceiver.class);
            i.setAction(BootReceiver.WAKE_CHECK);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
            PendingIntent pi = PendingIntent.getBroadcast(c, 1, i, flags);
            long at = SystemClock.elapsedRealtime() + WATCHDOG_MS;
            if (Build.VERSION.SDK_INT >= 31 && am.canScheduleExactAlarms()) {
                am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
            } else if (Build.VERSION.SDK_INT >= 23) {
                am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
            } else {
                am.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
            }
            // 记录下一轮闹钟时间（墙钟，检测面板展示）
            new Config(c).setWatchdogAt(System.currentTimeMillis() + WATCHDOG_MS);
        } catch (Exception e) {
            Log.w(TAG, "scheduleWatchdog: " + e);
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        RUNNING = true;
        LogStore.init(this);
        startForeground(NID, buildNotification(-1));
        LogStore.add("前台服务启动");

        worker = new HandlerThread("smsgate-upload");
        worker.start();
        wh = new Handler(worker.getLooper());

        registerReceiver(connRecv, new IntentFilter(ConnectivityManager.CONNECTIVITY_ACTION));

        // 监听系统收件箱变化：短信入库即触发补扫（去抖 2 秒，等待完整写入）
        smsObserver = new ContentObserver(uiBus) {
            @Override public void onChange(boolean selfChange) {
                new Config(UploadService.this).setLastObserver(System.currentTimeMillis());
                LogStore.add("收件箱观察者触发（短信入库）→ 2 秒后补扫");
                uiBus.removeCallbacks(scanTask);
                uiBus.postDelayed(scanTask, 2000);
            }
        };
        try {
            getContentResolver().registerContentObserver(
                    Uri.parse("content://sms/inbox"), true, smsObserver);
            // 再监听整表：部分 ROM 只在 sms 而非 sms/inbox 上派发通知
            getContentResolver().registerContentObserver(
                    Uri.parse("content://sms"), true, smsObserver);
            OBSERVER_OK = true;
        } catch (Exception e) {
            LogStore.add("观察者注册失败: " + e);
            Log.w(TAG, "registerContentObserver: " + e);
        }

        // 看门狗：精确闹钟，服务被杀时也能按时唤醒补扫
        scheduleWatchdog(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Config cfg = new Config(this);
        if (!cfg.enabled()) {
            stopSelf();
            return START_NOT_STICKY;
        }
        applyKeepAwake(cfg);
        scheduleWatchdog(this);   // 每次启动都续上下一轮看门狗
        if (!looping) {
            looping = true;
            wh.post(drainTask);
        }
        return START_STICKY;
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // 用户从最近任务划掉 → 2 秒后拉起
        try {
            AlarmManager am = (AlarmManager) getSystemService(ALARM_SERVICE);
            if (am != null) {
                am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP,
                        SystemClock.elapsedRealtime() + 2000, wakePendingIntent());
            }
        } catch (Exception ignored) {}
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public void onDestroy() {
        RUNNING = false;
        OBSERVER_OK = false;
        looping = false;
        LogStore.add("前台服务停止");
        try { unregisterReceiver(connRecv); } catch (Exception ignored) {}
        if (smsObserver != null) {
            try { getContentResolver().unregisterContentObserver(smsObserver); } catch (Exception ignored) {}
            smsObserver = null;
        }
        uiBus.removeCallbacks(scanTask);
        releaseLock(uploadLock);
        releaseLock(keepLock);
        wh.removeCallbacksAndMessages(null);
        if (worker != null) worker.quitSafely();
        boolean wasEnabled = new Config(this).enabled();
        if (wasEnabled) {
            // 非用户主动停止（如系统回收）→ 3 秒后自动重启，并续上看门狗
            try {
                AlarmManager am = (AlarmManager) getSystemService(ALARM_SERVICE);
                if (am != null) {
                    am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP,
                            SystemClock.elapsedRealtime() + 3000, wakePendingIntent());
                }
            } catch (Exception ignored) {}
            scheduleWatchdog(this);
        }
        if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE);
        else stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    // ---------- 上传主循环（后台线程） ----------

    private void drain() {
        try {
            Config cfg = new Config(this);
            if (!cfg.enabled()) {
                stopSelf();
                return;
            }
            List<JSONObject> batch = QueueStore.pending(this, 10);
            if (batch.isEmpty()) {
                consecutiveFails = 0;
                NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
                nm.notify(NID, buildNotification(0));
                wh.postDelayed(drainTask, IDLE_POLL_MS);
                return;
            }

            acquireLock();
            boolean ok = true;
            int sent = 0;
            for (JSONObject rec : batch) {
                if (Uploader.upload(cfg.serverUrl(), rec)) {
                    QueueStore.removeIds(this, QueueStore.singleton(rec.optString("id")));
                    cfg.setLastOk(System.currentTimeMillis());
                    cfg.addForwarded();
                    sent++;
                } else {
                    ok = false;
                    break;
                }
            }
            releaseLock(uploadLock);
            if (sent > 0) {
                LogStore.add("服务通道上传成功 " + sent + " 条");
            } else {
                LogStore.add("服务通道上传失败，将退避重试");
            }

            int remain = QueueStore.count(this);
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            nm.notify(NID, buildNotification(remain));

            long delay;
            if (ok) {
                consecutiveFails = 0;
                delay = remain > 0 ? 2000 : IDLE_POLL_MS;
            } else {
                consecutiveFails++;
                delay = Math.min(30_000L * consecutiveFails, 300_000L); // 30s → 5min
            }
            wh.postDelayed(drainTask, delay);
        } catch (Exception e) {
            Log.e(TAG, "drain", e);
            wh.postDelayed(drainTask, 60_000);
        }
    }

    // ---------- WakeLock ----------

    private void acquireLock() {
        if (uploadLock == null) {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            uploadLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "smsgate:upload");
            uploadLock.setReferenceCounted(false);
        }
        try { uploadLock.acquire(60_000); } catch (Exception ignored) {}
    }

    private void applyKeepAwake(Config cfg) {
        if (cfg.keepAwake()) {
            if (keepLock == null) {
                PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
                keepLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "smsgate:keepalive");
                keepLock.setReferenceCounted(false);
            }
            try { keepLock.acquire(12 * 3600_000L); } catch (Exception ignored) {}
        } else {
            releaseLock(keepLock);
        }
    }

    private void releaseLock(PowerManager.WakeLock lk) {
        try { if (lk != null && lk.isHeld()) lk.release(); } catch (Exception ignored) {}
    }

    // ---------- 闹钟 / 通知 ----------

    private PendingIntent wakePendingIntent() {
        Intent i = new Intent(this, BootReceiver.class);
        i.setAction(BootReceiver.WAKE_CHECK);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getBroadcast(this, 1, i, flags);
    }

    private Notification buildNotification(int queue) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel ch = new NotificationChannel(
                CH_ID, getString(R.string.notice_channel), NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("保持短信转发在后台稳定运行");
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);

        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) b = new Notification.Builder(this, CH_ID);
        else b = new Notification.Builder(this);
        b.setSmallIcon(R.drawable.ic_stat)
         .setContentTitle("短信同步助手运行中")
         .setOngoing(true);
        String text;
        if (queue < 0) text = "正在保持连接";
        else if (queue == 0) text = "已转发 " + new Config(this).forwarded() + " 条 · 队列清空";
        else text = "待上传 " + queue + " 条 · 重试中";
        b.setContentText(text);
        return b.build();
    }
}
