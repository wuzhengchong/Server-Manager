package com.aiwo.smsgate;

import android.Manifest;
import android.app.AlarmManager;
import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.PowerManager;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 运行检测：逐项检查可能导致「收不到短信」的环节，
 * 在主界面弹窗展示每一项的通过/异常与修复建议。
 */
public final class Diagnostics {
    private Diagnostics() {
    }

    private static String fmt(long ts) {
        if (ts <= 0) return "从未";
        return new SimpleDateFormat("MM-dd HH:mm:ss", Locale.CHINA).format(new Date(ts));
    }

    /** 执行全部检测并返回报告文本（网络项需已联网，内部阻塞约 4 秒，调用方放线程里） */
    public static String run(Context ctx) {
        Context app = ctx.getApplicationContext();
        Config cfg = new Config(app);
        StringBuilder sb = new StringBuilder();
        int bad = 0;

        // 1. 短信权限
        boolean recv = app.checkSelfPermission(Manifest.permission.RECEIVE_SMS)
                == PackageManager.PERMISSION_GRANTED;
        boolean read = app.checkSelfPermission(Manifest.permission.READ_SMS)
                == PackageManager.PERMISSION_GRANTED;
        sb.append(recv && read ? "✓ 短信权限：已授予（接收+读取）"
                : "✗ 短信权限：缺失！" + (recv ? "（有接收无读取）" : (read ? "（有读取无接收）" : ""))
                + " → 应用详情→权限，手动开启");
        sb.append('\n');
        if (!(recv && read)) bad++;

        // 2. 通知权限（Android 13+，前台服务依赖常驻通知）
        if (Build.VERSION.SDK_INT >= 33) {
            NotificationManager nm = (NotificationManager) app.getSystemService(Context.NOTIFICATION_SERVICE);
            boolean notif = nm != null && nm.areNotificationsEnabled();
            sb.append(notif ? "✓ 通知权限：已授予"
                    : "✗ 通知权限：被关闭 → 前台服务可能被系统连带回收，请开启通知");
            sb.append('\n');
            if (!notif) bad++;
        }

        // 3. 电池优化白名单
        PowerManager pm = (PowerManager) app.getSystemService(Context.POWER_SERVICE);
        boolean batt = pm != null && pm.isIgnoringBatteryOptimizations(app.getPackageName());
        sb.append(batt ? "✓ 电池白名单：已加入"
                : "✗ 电池白名单：未加入 → 点主界面「电池白名单」按钮");
        sb.append('\n');
        if (!batt) bad++;

        // 4. 精确闹钟（Android 12+ 看门狗依赖）
        if (Build.VERSION.SDK_INT >= 31) {
            AlarmManager am = (AlarmManager) app.getSystemService(Context.ALARM_SERVICE);
            boolean exact = am != null && am.canScheduleExactAlarms();
            sb.append(exact ? "✓ 精确闹钟：允许"
                    : "△ 精确闹钟：未授权 → 看门狗会被推迟，建议到设置→应用→本应用→闹钟提醒中允许");
            sb.append('\n');
            if (!exact) bad++;
        }

        // 5. 前台服务
        sb.append(UploadService.RUNNING ? "✓ 前台服务：运行中"
                : "✗ 前台服务：未运行 → 点「立即启动」");
        sb.append('\n');
        if (!UploadService.RUNNING) bad++;

        // 6. 收件箱观察者
        sb.append(UploadService.OBSERVER_OK ? "✓ 收件箱观察者：已注册（短信入库实时补扫）"
                : "✗ 收件箱观察者：未注册（服务未运行）");
        sb.append('\n');
        if (!UploadService.OBSERVER_OK) bad++;

        // 7. 同步开关
        sb.append(cfg.enabled() ? "✓ 同步开关：已开启" : "✗ 同步开关：已关闭");
        sb.append('\n');
        if (!cfg.enabled()) bad++;

        // 8. 服务器连通（真 ping）
        boolean alive = Uploader.ping(cfg.serverUrl());
        sb.append(alive ? "✓ 服务器：可达（" + cfg.serverUrl() + "）"
                : "✗ 服务器：不可达（" + cfg.serverUrl() + "）→ 检查地址与服务器是否运行");
        sb.append('\n');
        if (alive) cfg.setLastPing(System.currentTimeMillis());
        else bad++;

        // 9. 关键事件时间线
        sb.append('\n').append("—— 最近事件时间线 ——").append('\n');
        sb.append("上次短信广播触发：").append(fmt(cfg.lastBroadcast())).append('\n');
        sb.append("上次收件箱观察者触发：").append(fmt(cfg.lastObserver())).append('\n');
        sb.append("上次收件箱补扫：").append(fmt(cfg.lastScan())).append('\n');
        sb.append("上次看门狗触发：").append(fmt(cfg.lastWatchdog())).append('\n');
        sb.append("下次看门狗闹钟：").append(fmt(cfg.watchdogAt())).append('\n');
        sb.append("上次服务器探测成功：").append(fmt(cfg.lastPing())).append('\n');
        sb.append("上次上传成功：").append(fmt(cfg.lastOk())).append('\n');
        sb.append("队列待上传：").append(QueueStore.count(app)).append(" 条 · 累计转发 ")
                .append(cfg.forwarded()).append(" 条");

        // 10. 结论
        sb.append('\n').append('\n');
        if (bad == 0) {
            sb.append("结论：全部通过。若仍收不到短信，看时间线：\n")
              .append("· 广播时间在更新 → 转发链路正常，查服务器端\n")
              .append("· 广播一直「从未」→ ROM 拦截广播，靠观察者/看门狗兜底（最长 10 分钟）\n")
              .append("· 都没触发 → 回到本页顶部按保活指引设置自启动/耗电管理");
        } else {
            sb.append("结论：发现 ").append(bad).append(" 项异常，请按 ✗ 项修复后重新检测。");
        }
        return sb.toString();
    }
}
