package com.aiwo.smsgate;

import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.provider.Telephony;

import org.json.JSONObject;

/**
 * 收件箱补扫兜底：
 * ColorOS 等 ROM 在进程未运行时可能拦截短信广播（自启动管控），导致 SmsReceiver 收不到。
 * 本类用 READ_SMS 权限直接查询系统收件箱中「上次扫描之后入库」的短信，
 * 配合 SeenStore 指纹去重后入队上传。
 * 触发点：看门狗闹钟（约 10 分钟）、开机、用户打开 App 时。
 */
public final class InboxScanner {
    private static final String SP = "inbox_scan";

    private InboxScanner() {
    }

    public static void scanAndEnqueueAsync(Context ctx) {
        final Context app = ctx.getApplicationContext();
        Thread t = new Thread(() -> {
            try {
                Config cfg = new Config(app);
                if (!cfg.enabled()) return;
                if (app.checkSelfPermission(android.Manifest.permission.READ_SMS)
                        != PackageManager.PERMISSION_GRANTED) return;

                cfg.setLastScan(System.currentTimeMillis());

                android.content.SharedPreferences sp =
                        app.getSharedPreferences(SP, Context.MODE_PRIVATE);
                long last = sp.getLong("last", 0);
                long now = System.currentTimeMillis();
                if (last == 0) {
                    // 首次运行只记录基线，不扫历史
                    sp.edit().putLong("last", now).apply();
                    return;
                }

                Cursor c = app.getContentResolver().query(
                        Uri.parse("content://sms/inbox"),
                        new String[]{Telephony.Sms.Inbox.ADDRESS,
                                Telephony.Sms.Inbox.BODY,
                                Telephony.Sms.Inbox.DATE},
                        Telephony.Sms.Inbox.DATE + ">?",
                        new String[]{String.valueOf(last)},
                        Telephony.Sms.Inbox.DATE + " ASC");
                if (c == null) return;
                long maxDate = last;
                int added = 0;
                while (c.moveToNext() && added < 50) {
                    String addr = c.getString(0);
                    String body = c.getString(1);
                    long date = c.getLong(2);
                    if (date > maxDate) maxDate = date;
                    if (addr == null || addr.isEmpty() || body == null || body.isEmpty()) continue;
                    String fp = SeenStore.fingerprint(addr, body, date);
                    if (!SeenStore.markIfNew(app, fp)) continue;   // 广播路径已处理
                    QueueStore.add(app, QueueStore.makeRecord(
                            cfg.deviceId(), cfg.deviceName(), addr, body, date));
                    added++;
                }
                c.close();
                sp.edit().putLong("last", Math.max(maxDate, last)).apply();
                if (added > 0) {
                    LogStore.add("补扫收件箱：发现 " + added + " 条短信未走广播通道（已补传）");
                    DirectUploader.drainAsync(app, null);
                }
            } catch (Exception e) {
                LogStore.add("补扫收件箱失败: " + e);
            }
        }, "smsgate-scan");
        t.start();
    }
}
