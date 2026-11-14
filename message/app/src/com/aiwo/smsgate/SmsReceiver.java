package com.aiwo.smsgate;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.provider.Telephony;
import android.telephony.SmsMessage;
import android.util.Log;

import org.json.JSONObject;

/** 收到新短信 → 入队 → 免服务直接上传（不依赖服务存活，Android 12+ 可靠） */
public class SmsReceiver extends BroadcastReceiver {
    private static final String TAG = "SmsGate";

    @Override
    public void onReceive(Context context, Intent intent) {
        try {
            LogStore.init(context);
            String action = intent.getAction();
            LogStore.add("短信广播触发 action=" + action);
            new Config(context).setLastBroadcast(System.currentTimeMillis());
            if (!Telephony.Sms.Intents.SMS_RECEIVED_ACTION.equals(action)) return;
            Config cfg = new Config(context);
            if (!cfg.enabled()) {
                LogStore.add("同步已停止，忽略本条短信");
                return;
            }

            SmsMessage[] msgs = Telephony.Sms.Intents.getMessagesFromIntent(intent);
            if (msgs == null || msgs.length == 0) {
                LogStore.add("广播未携带 PDU（ROM 异常），依赖收件箱补扫兜底");
                InboxScanner.scanAndEnqueueAsync(context);
                return;
            }

            // 同一 Intent 的多段 PDU 可能属于一条长短信，按序拼接
            StringBuilder sb = new StringBuilder();
            String sender = null;
            long ts = 0;
            for (SmsMessage m : msgs) {
                if (m == null) continue;
                if (sender == null) sender = m.getOriginatingAddress();
                String part = m.getMessageBody();
                if (part != null) sb.append(part);
                long t = m.getTimestampMillis();
                if (t > ts) ts = t;
            }
            if (sender == null || sb.length() == 0) return;

            String devId = cfg.deviceId();
            if (devId == null || devId.isEmpty()) {
                devId = Device.deviceId(context);
                cfg.setDeviceId(devId);
            }
            // 指纹去重：广播路径与收件箱补扫路径只处理一次
            String fp = SeenStore.fingerprint(sender, sb.toString(), ts);
            if (!SeenStore.markIfNew(context, fp)) {
                LogStore.add("收到短信 [" + sender + "] " + sb.length()
                        + " 字，为 1 分钟内重复投递，跳过");
                return;
            }
            JSONObject rec = QueueStore.makeRecord(
                    devId, cfg.deviceName(), sender, sb.toString(), ts);
            QueueStore.add(context, rec);
            LogStore.add("收到短信 [" + sender + "] " + sb.length() + " 字，已入队（当前 "
                    + QueueStore.count(context) + " 条）");

            // 关键：在广播内直接上传（goAsync 延长广播窗口 + WakeLock 保 CPU），
            // 服务被 ROM 杀死也能秒传；服务活着时其轮询会自然跳过空队列
            final PendingResult pr = goAsync();
            DirectUploader.drainAsync(context, ok -> {
                UploadService.kick(context);  // 尽力而为：让存活的服务刷新通知状态
                pr.finish();
            });
        } catch (Exception e) {
            Log.e(TAG, "onReceive", e);
        }
    }
}
