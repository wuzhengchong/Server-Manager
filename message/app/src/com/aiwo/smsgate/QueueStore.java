package com.aiwo.smsgate;

import android.content.Context;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** 待上传队列：本地 JSON 文件持久化，上传失败不丢数据，开机重试 */
public class QueueStore {
    private static final String TAG = "SmsGate";
    private static final int MAX_QUEUE = 1000; // 队列上限，超出丢弃最旧的

    private static File file(Context c) {
        return new File(c.getFilesDir(), "queue.json");
    }

    private static synchronized JSONArray read(Context c) {
        try {
            FileInputStream fis = new FileInputStream(file(c));
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = fis.read(buf)) > 0) bos.write(buf, 0, n);
            fis.close();
            return new JSONArray(new String(bos.toByteArray(), StandardCharsets.UTF_8));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    private static synchronized void write(Context c, JSONArray arr) {
        try {
            File f = file(c);
            File tmp = new File(c.getFilesDir(), "queue.json.tmp");
            FileOutputStream fos = new FileOutputStream(tmp);
            fos.write(arr.toString().getBytes(StandardCharsets.UTF_8));
            fos.close();
            if (!tmp.renameTo(f)) {
                f.delete();
                tmp.renameTo(f);
            }
        } catch (Exception e) {
            Log.e(TAG, "queue write", e);
        }
    }

    public static synchronized void add(Context c, JSONObject rec) {
        try {
            JSONArray arr = read(c);
            arr.put(rec);
            while (arr.length() > MAX_QUEUE) arr.remove(0);
            write(c, arr);
        } catch (Exception e) {
            Log.e(TAG, "queue add", e);
        }
    }

    public static synchronized List<JSONObject> pending(Context c, int limit) {
        List<JSONObject> out = new ArrayList<>();
        try {
            JSONArray arr = read(c);
            for (int i = 0; i < arr.length() && out.size() < limit; i++) {
                out.add(arr.getJSONObject(i));
            }
        } catch (Exception ignored) {}
        return out;
    }

    public static synchronized void removeIds(Context c, List<String> ids) {
        try {
            JSONArray arr = read(c);
            JSONArray keep = new JSONArray();
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.getJSONObject(i);
                if (!ids.contains(o.optString("id"))) keep.put(o);
            }
            write(c, keep);
        } catch (Exception ignored) {}
    }

    public static synchronized int count(Context c) {
        return read(c).length();
    }

    public static synchronized void clear(Context c) {
        write(c, new JSONArray());
    }

    /** 生成一条记录 */
    public static JSONObject makeRecord(String deviceId, String deviceName,
                                        String sender, String body, long ts) {
        JSONObject rec = new JSONObject();
        try {
            rec.put("id", java.util.UUID.randomUUID().toString());
            rec.put("device_id", deviceId == null ? "" : deviceId);
            rec.put("device_name", deviceName == null ? "" : deviceName);
            rec.put("sender", sender == null ? "" : sender);
            rec.put("body", body == null ? "" : body);
            rec.put("ts", ts);
            rec.put("recv", System.currentTimeMillis());
        } catch (Exception ignored) {}
        return rec;
    }

    public static List<String> idsOf(List<JSONObject> list) {
        List<String> ids = new ArrayList<>();
        for (JSONObject o : list) ids.add(o.optString("id"));
        return ids;
    }

    public static List<String> singleton(String id) {
        return Collections.singletonList(id);
    }
}
