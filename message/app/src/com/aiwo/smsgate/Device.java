package com.aiwo.smsgate;

import android.content.Context;
import android.os.Build;
import android.provider.Settings;

/** 设备标识：ANDROID_ID + 机型，首次生成后固定 */
public class Device {
    public static String deviceId(Context c) {
        String id = Settings.Secure.ANDROID_ID;
        if (id == null || id.isEmpty() || "9774d56d682e549c".equals(id)) {
            id = java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        }
        String model = Build.MODEL == null ? "unknown" : Build.MODEL.replaceAll("[^0-9A-Za-z_-]", "");
        return id + "_" + model;
    }

    public static String model() {
        return Build.MANUFACTURER + " " + Build.MODEL + " (Android " + Build.VERSION.RELEASE + ")";
    }
}
