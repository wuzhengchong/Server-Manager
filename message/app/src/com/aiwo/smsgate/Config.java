package com.aiwo.smsgate;

import android.content.Context;
import android.content.SharedPreferences;

/** 配置存取（SharedPreferences 封装） */
public class Config {
    private final SharedPreferences p;

    public Config(Context c) {
        p = c.getSharedPreferences("cfg", Context.MODE_PRIVATE);
    }

    public String serverUrl() { return p.getString("server", ""); }
    public void setServerUrl(String v) { p.edit().putString("server", v).apply(); }

    public String deviceName() { return p.getString("devname", ""); }
    public void setDeviceName(String v) { p.edit().putString("devname", v).apply(); }

    public String deviceId() { return p.getString("device_id", ""); }
    public void setDeviceId(String v) { p.edit().putString("device_id", v).apply(); }

    public boolean enabled() { return p.getBoolean("enabled", false); }
    public void setEnabled(boolean b) { p.edit().putBoolean("enabled", b).apply(); }

    /** 屏幕关闭时保持 CPU 唤醒（更费电，仅在恶劣 ROM 下开启） */
    public boolean keepAwake() { return p.getBoolean("keepawake", false); }
    public void setKeepAwake(boolean b) { p.edit().putBoolean("keepawake", b).apply(); }

    public long lastOk() { return p.getLong("lastok", 0); }
    public void setLastOk(long t) { p.edit().putLong("lastok", t).apply(); }

    public long forwarded() { return p.getLong("forwarded", 0); }
    public void addForwarded() { p.edit().putLong("forwarded", forwarded() + 1).apply(); }

    // ---------- 诊断事件时间戳（检测面板用） ----------

    public long lastBroadcast() { return p.getLong("ev_broadcast", 0); }
    public void setLastBroadcast(long t) { p.edit().putLong("ev_broadcast", t).apply(); }

    public long lastObserver() { return p.getLong("ev_observer", 0); }
    public void setLastObserver(long t) { p.edit().putLong("ev_observer", t).apply(); }

    public long lastScan() { return p.getLong("ev_scan", 0); }
    public void setLastScan(long t) { p.edit().putLong("ev_scan", t).apply(); }

    public long lastWatchdog() { return p.getLong("ev_watchdog", 0); }
    public void setLastWatchdog(long t) { p.edit().putLong("ev_watchdog", t).apply(); }

    /** 下一次看门狗闹钟的触发时间（elapsedRealtime 基准换算为墙钟） */
    public long watchdogAt() { return p.getLong("watchdog_at", 0); }
    public void setWatchdogAt(long t) { p.edit().putLong("watchdog_at", t).apply(); }

    public long lastPing() { return p.getLong("ev_ping", 0); }
    public void setLastPing(long t) { p.edit().putLong("ev_ping", t).apply(); }
}
