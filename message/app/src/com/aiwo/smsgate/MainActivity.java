package com.aiwo.smsgate;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {

    private static final int REQ_PERMS = 11;

    private TextView tvStatus;
    private TextView tvLog;
    private EditText etServer, etDevName;
    private Switch swEnabled, swKeep;
    private Config cfg;

    private final android.os.Handler ui = new android.os.Handler();
    private final Runnable uiLoop = new Runnable() {
        @Override
        public void run() {
            refreshLog();
            ui.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        LogStore.init(this);
        cfg = new Config(this);

        tvStatus = findViewById(R.id.tvStatus);
        tvLog = findViewById(R.id.tvLog);
        etServer = findViewById(R.id.etServer);
        etDevName = findViewById(R.id.etDevName);
        swEnabled = findViewById(R.id.swEnabled);
        swKeep = findViewById(R.id.swKeep);

        Button btnStart = findViewById(R.id.btnStart);
        Button btnStop = findViewById(R.id.btnStop);
        Button btnTest = findViewById(R.id.btnTest);
        Button btnBattery = findViewById(R.id.btnBattery);
        Button btnGuide = findViewById(R.id.btnGuide);
        Button btnDiag = findViewById(R.id.btnDiag);
        Button btnClear = findViewById(R.id.btnClear);
        Button btnCopyLog = findViewById(R.id.btnCopyLog);
        Button btnClearLog = findViewById(R.id.btnClearLog);

        etServer.setText(cfg.serverUrl());
        etDevName.setText(cfg.deviceName());
        swKeep.setChecked(cfg.keepAwake());
        if (cfg.deviceId() == null || cfg.deviceId().isEmpty()) {
            cfg.setDeviceId(Device.deviceId(this));
        }

        btnStart.setOnClickListener(v -> startAll());
        btnStop.setOnClickListener(v -> stopAll());
        btnTest.setOnClickListener(v -> testUpload());
        btnBattery.setOnClickListener(v -> requestBatteryWhite());
        btnGuide.setOnClickListener(v -> showGuide());
        btnDiag.setOnClickListener(v -> runDiagnostics());
        btnClear.setOnClickListener(v -> {
            QueueStore.clear(this);
            refreshStatus();
            toast("本地队列已清空");
        });
        btnCopyLog.setOnClickListener(v -> {
            try {
                android.content.ClipboardManager cm =
                        (android.content.ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                cm.setPrimaryClip(android.content.ClipData.newPlainText("smsgate-log",
                        "SmsGate 日志\n" + LogStore.recent(300)));
                toast("日志已复制，可粘贴发送");
            } catch (Exception e) {
                toast("复制失败");
            }
        });
        btnClearLog.setOnClickListener(v -> {
            LogStore.clear();
            refreshLog();
            toast("日志已清空");
        });

        requestNeededPermissions();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshStatus();
        refreshLog();
        ui.postDelayed(uiLoop, 1000);
        // 打开 App 立即补扫收件箱（找回广播被 ROM 拦截的短信）并上传积压队列
        InboxScanner.scanAndEnqueueAsync(this);
        if (cfg.enabled()) {
            DirectUploader.drainAsync(this, ok -> {
                if (ok) runOnUiThread(this::refreshStatus);
            });
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        ui.removeCallbacks(uiLoop);
    }

    private void refreshLog() {
        if (tvLog != null) tvLog.setText(LogStore.recent(40));
    }

    // ---------- 权限 ----------

    private void requestNeededPermissions() {
        boolean needSms = checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED;
        boolean needNoti = Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED;
        if (needSms || needNoti) {
            java.util.List<String> ps = new java.util.ArrayList<>();
            ps.add(Manifest.permission.RECEIVE_SMS);
            ps.add(Manifest.permission.READ_SMS);
            if (needNoti) ps.add(Manifest.permission.POST_NOTIFICATIONS);
            requestPermissions(ps.toArray(new String[0]), REQ_PERMS);
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        if (code == REQ_PERMS) {
            boolean smsOk = checkSelfPermission(Manifest.permission.RECEIVE_SMS) == PackageManager.PERMISSION_GRANTED;
            if (!smsOk) {
                toast("未授予短信权限，无法监听短信；可到 系统设置→应用→短信同步助手→权限 手动开启");
            } else if (cfg.enabled()) {
                UploadService.kick(this);
            }
            refreshStatus();
        }
    }

    // ---------- 操作 ----------

    private void saveInputs() {
        cfg.setServerUrl(etServer.getText().toString().trim());
        String name = etDevName.getText().toString().trim();
        if (name.isEmpty()) name = Device.model();
        cfg.setDeviceName(name);
        cfg.setKeepAwake(swKeep.isChecked());
    }

    private void startAll() {
        saveInputs();
        if (cfg.serverUrl().isEmpty()) {
            toast("请先填写服务器地址");
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED) {
            requestNeededPermissions();
            toast("请先授予短信权限");
            return;
        }
        cfg.setEnabled(true);
        swEnabled.setChecked(true);
        UploadService.kick(this);
        toast("已启动：短信将实时转发至服务器");
        refreshStatus();
    }

    private void stopAll() {
        cfg.setEnabled(false);
        swEnabled.setChecked(false);
        try { stopService(new Intent(this, UploadService.class)); } catch (Exception ignored) {}
        toast("已停止");
        refreshStatus();
    }

    /** 运行检测：后台线程逐项检查，弹窗展示报告 */
    private void runDiagnostics() {
        saveInputs();
        toast("正在检测…");
        new Thread(() -> {
            String rep;
            try {
                rep = Diagnostics.run(this);
            } catch (Exception e) {
                rep = "检测失败: " + e;
            }
            final String report = rep;
            ui.post(() -> new AlertDialog.Builder(MainActivity.this)
                    .setTitle("运行检测报告")
                    .setMessage(report)
                    .setPositiveButton("知道了", null)
                    .setNeutralButton("复制报告", (d, w) -> {
                        try {
                            android.content.ClipboardManager cm =
                                    (android.content.ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                            cm.setPrimaryClip(android.content.ClipData.newPlainText("diag", report));
                            toast("报告已复制");
                        } catch (Exception ignored) {
                        }
                    })
                    .show());
        }, "smsgate-diag").start();
    }

    private void testUpload() {
        saveInputs();
        QueueStore.add(this, QueueStore.makeRecord(
                cfg.deviceId(), cfg.deviceName(), "TEST", "测试短信 · " +
                        new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.CHINA)
                                .format(new java.util.Date()),
                System.currentTimeMillis()));
        UploadService.kick(this);
        toast("测试记录已入队，将立即尝试上传");
        refreshStatus();
    }

    private void requestBatteryWhite() {
        try {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm.isIgnoringBatteryOptimizations(getPackageName())) {
                toast("已在电池优化白名单中");
                return;
            }
            @SuppressWarnings("ImplicitSamUsage")
            Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + getPackageName()));
            startActivity(i);
        } catch (Exception e) {
            toast("无法打开电池优化设置，请手动到 设置→电池 中关闭本应用优化");
        }
    }

    private void showGuide() {
        new AlertDialog.Builder(this)
                .setTitle("后台保活指引")
                .setMessage("为保证后台也能即时收到短信（否则系统会拦截短信广播，需打开 App 才能补传），请务必完成：\n\n"
                        + "1. 自启动（最关键）：系统设置→应用管理→短信同步助手→自启动，设为允许\n"
                        + "   · ColorOS（一加/OPPO）：设置→应用→自启动，允许；并在「应用信息→耗电管理」选「允许完全后台」\n"
                        + "   · MIUI/澎湃：安全中心→应用管理→自启动\n"
                        + "   · EMUI/HarmonyOS：设置→应用→启动管理→手动管理（三项全开）\n"
                        + "2. 电池白名单：点本页「电池白名单」按钮\n"
                        + "3. 后台网络不受限：设置→移动网络→流量管理→应用网络管理→本应用，WLAN/数据/后台全允许\n"
                        + "4. 最近任务锁定：多任务界面下拉本应用卡片，点锁图标锁定\n"
                        + "5. 常驻通知：保持前台通知开启，这是系统的保活机制\n\n"
                        + "即使以上某项被系统关闭，App 也会通过精确定时补扫收件箱找回漏传的短信（约 10 分钟一轮）。\n\n"
                        + "本应用图标与通知常驻可见。")
                .setPositiveButton("知道了", null)
                .setNeutralButton("去开启自启动", (d, w) -> openStartupSettings())
                .show();
    }

    /** 直接跳转各 ROM 的「自启动管理」页；失败则回落到应用详情页 */
    private void openStartupSettings() {
        String[][] list = {
                {"com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity"},
                {"com.coloros.safecenter", "com.coloros.safecenter.startupapp.StartupAppListActivity"},
                {"com.oplus.safecenter", "com.oplus.safecenter.permission.startup.StartupAppListActivity"},
                {"com.oplus.safecenter", "com.oplus.safecenter.startupapp.StartupAppListActivity"},
                {"com.oppo.safe", "com.oppo.safe.permission.startup.StartupAppListActivity"},
                {"com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity"},
                {"com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"}
        };
        for (String[] item : list) {
            try {
                Intent i = new Intent();
                i.setComponent(new android.content.ComponentName(item[0], item[1]));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
                return;
            } catch (Exception ignored) {
            }
        }
        try {
            Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:" + getPackageName()));
            startActivity(i);
            toast("未找到自启动页，请在应用详情中找「自启动 / 耗电管理」");
        } catch (Exception e) {
            toast("请手动到 设置→应用→自启动 中允许本应用");
        }
    }

    private void refreshStatus() {
        int q = QueueStore.count(this);
        String state = cfg.enabled() ? "运行中" : "已停止";
        StringBuilder sb = new StringBuilder();
        sb.append("状态：").append(state)
          .append("\n队列待上传：").append(q).append(" 条")
          .append("\n累计已转发：").append(cfg.forwarded()).append(" 条");
        if (cfg.lastOk() > 0) {
            sb.append("\n上次上传成功：")
              .append(new java.text.SimpleDateFormat("MM-dd HH:mm:ss", java.util.Locale.CHINA)
                      .format(new java.util.Date(cfg.lastOk())));
        }
        if (cfg.deviceId() != null && !cfg.deviceId().isEmpty()) {
            sb.append("\n设备标识：").append(cfg.deviceId());
        }
        tvStatus.setText(sb.toString());
        swEnabled.setChecked(cfg.enabled());
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_SHORT).show();
    }
}
