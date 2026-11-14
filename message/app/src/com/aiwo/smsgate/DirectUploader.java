package com.aiwo.smsgate;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.util.List;

/**
 * 免服务直接上传：在广播接收器等受限场景中，不启动前台服务也能把队列传完。
 * 上传流程（收到短信后触发，总窗口 60 秒）：
 *   1. 存活探测：测试服务器是否存活，连续测 3 遍
 *   2. 逐条上传：每条最多尝试 5 遍，失败轮换连接方法（默认/强制新连接/禁缓存短超时）
 *   3. 失败等 3 秒继续尝试，直到成功或 1 分钟窗口结束
 *   4. 窗口结束仍有积压 → 闹钟指数退避重试（30s → 5min 封顶），成功后清零
 * 全程写入 LogStore，界面可查看。
 */
public final class DirectUploader {
    private static final String TAG = "SmsGate";
    private static final long WINDOW_MS = 60_000;   // 总重试窗口：1 分钟
    private static volatile int backoff = 0;        // 闹钟重试连续失败次数

    public interface Done {
        void run(boolean anySent);
    }

    private DirectUploader() {
    }

    public static void drainAsync(Context ctx, Done done) {
        final Context app = ctx.getApplicationContext();
        final PowerManager.WakeLock wl = ((PowerManager) app.getSystemService(Context.POWER_SERVICE))
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "smsgate:direct");
        wl.setReferenceCounted(false);
        try {
            wl.acquire(WINDOW_MS + 30_000);
        } catch (Exception ignored) {
        }
        Thread t = new Thread(() -> {
            boolean anySent = false;
            long deadline = System.currentTimeMillis() + WINDOW_MS;
            try {
                Config cfg = new Config(app);
                if (cfg.enabled()) {
                    anySent = runWindow(app, cfg, deadline);
                }
            } catch (Exception e) {
                LogStore.add("上传流程异常: " + e);
                Log.w(TAG, "direct drain: " + e);
            } finally {
                try {
                    wl.release();
                } catch (Exception ignored) {
                }
            }
            final boolean sent = anySent;
            if (done != null) done.run(sent);
        }, "smsgate-direct");
        t.start();
    }

    /** 上传主流程（在直接上传线程内执行） */
    private static boolean runWindow(Context app, Config cfg, long deadline) {
        String server = cfg.serverUrl();
        LogStore.add("—— 开始上传流程（窗口 60 秒）——");
        boolean anySent = false;
        while (System.currentTimeMillis() < deadline) {
            List<JSONObject> batch = QueueStore.pending(app, 10);
            if (batch.isEmpty()) {
                LogStore.add("队列已清空，全部上传成功");
                backoff = 0;
                return anySent;
            }

            // 阶段 1：存活探测，连续测试 3 遍
            boolean alive = false;
            for (int i = 1; i <= 3 && !alive; i++) {
                alive = Uploader.ping(server);
                LogStore.add("存活测试 " + i + "/3：" + (alive ? "服务器可达" : "不可达"));
                if (!alive && i < 3) sleepGate(deadline, 1000);
            }
            if (!alive) {
                if (System.currentTimeMillis() >= deadline) break;
                LogStore.add("服务器不可达，等 3 秒重新探测");
                sleepGate(deadline, 3000);
                continue;
            }

            // 阶段 2：逐条上传，每条最多 5 遍、失败轮换方法、间隔等 3 秒
            boolean stuck = false;
            for (JSONObject rec : batch) {
                String tag = rec.optString("sender", "?");
                boolean sent = false;
                for (int attempt = 1; attempt <= 5 && !sent; attempt++) {
                    if (System.currentTimeMillis() >= deadline) {
                        LogStore.add("已达 1 分钟窗口上限，剩余消息稍后自动重试");
                        stuck = true;
                        break;
                    }
                    int method = (attempt - 1) % 3;
                    sent = Uploader.upload(server, rec, method);
                    LogStore.add("上传[" + tag + "] 第" + attempt + "遍/方法" + (method + 1)
                            + "：" + (sent ? "成功" : "失败，等 3 秒"));
                    if (!sent && attempt < 5) sleepGate(deadline, 3000);
                }
                if (sent) {
                    QueueStore.removeIds(app, QueueStore.singleton(rec.optString("id")));
                    cfg.setLastOk(System.currentTimeMillis());
                    cfg.addForwarded();
                    anySent = true;
                } else {
                    if (!stuck) LogStore.add("[" + tag + "] 5 遍均未成功，本轮结束");
                    stuck = true;
                }
                if (stuck) break;
            }
            if (stuck) break;
        }
        int remain = QueueStore.count(app);
        LogStore.add("流程结束：本次成功 " + (anySent ? "若干条" : "0 条") + "，剩余 " + remain + " 条");
        if (remain > 0) scheduleRetry(app);
        else backoff = 0;
        return anySent;
    }

    /** 睡眠，但不越过窗口截止时间 */
    private static void sleepGate(long deadline, long ms) {
        long remain = deadline - System.currentTimeMillis();
        if (remain <= 0) return;
        try {
            Thread.sleep(Math.min(ms, remain));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** 窗口结束仍有积压 → 闹钟重试：30s 起指数退避，5 分钟封顶 */
    private static void scheduleRetry(Context app) {
        try {
            int n = Math.min(backoff, 4);
            long delay = Math.min(30_000L << n, 300_000L);
            backoff++;
            AlarmManager am = (AlarmManager) app.getSystemService(Context.ALARM_SERVICE);
            if (am == null) return;
            Intent i = new Intent(app, BootReceiver.class);
            i.setAction(BootReceiver.WAKE_CHECK);
            PendingIntent pi = PendingIntent.getBroadcast(app, 2, i,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    SystemClock.elapsedRealtime() + delay, pi);
            LogStore.add("已排定自动重试：" + delay / 1000 + " 秒后");
        } catch (Exception e) {
            Log.w(TAG, "scheduleRetry: " + e);
        }
    }
}
