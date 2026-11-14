package com.aiwo.smsgate;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** HTTP 上传：POST JSON 到 服务器/__sms/upload（直连，无需令牌） */
public class Uploader {

    /** 归一化服务器地址（补尾斜杠），无效返回 null */
    private static String norm(String server) {
        if (server == null || server.trim().isEmpty()) return null;
        String b = server.trim();
        if (!b.endsWith("/")) b += "/";
        return b;
    }

    /** 服务器存活探测：能建立 HTTP 连接并拿到任意响应码即视为存活 */
    public static boolean ping(String server) {
        HttpURLConnection conn = null;
        try {
            String base = norm(server);
            if (base == null) return false;
            conn = (HttpURLConnection) new URL(base).openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);
            conn.setUseCaches(false);
            conn.setRequestProperty("Connection", "close");
            int code = conn.getResponseCode();
            return code > 0;   // 任何 HTTP 响应（200/30x/4xx）都说明服务器活着
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) try { conn.disconnect(); } catch (Exception ignored) {}
        }
    }

    /** @return true 表示服务器已确认收到（2xx） */
    public static boolean upload(String server, JSONObject rec) {
        return upload(server, rec, 0);
    }

    /**
     * 上传，支持切换连接方法（应对单一方法持续失败的场景）：
     * method 0 = 默认连接（15s 超时）
     * method 1 = 强制新连接（Connection: close，规避 keep-alive 僵死）
     * method 2 = 禁缓存 + 短超时（8s，快速失败快速重试）
     */
    public static boolean upload(String server, JSONObject rec, int method) {
        HttpURLConnection conn = null;
        try {
            String base = norm(server);
            if (base == null) return false;
            conn = (HttpURLConnection) new URL(base + "__sms/upload").openConnection();
            conn.setRequestMethod("POST");
            int timeout = method == 2 ? 8000 : 15000;
            conn.setConnectTimeout(timeout);
            conn.setReadTimeout(timeout);
            conn.setDoOutput(true);
            if (method == 1) conn.setRequestProperty("Connection", "close");
            if (method == 2) {
                conn.setUseCaches(false);
                conn.setRequestProperty("Cache-Control", "no-cache");
            }
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            byte[] body = rec.toString().getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(body.length);
            OutputStream os = conn.getOutputStream();
            os.write(body);
            os.close();
            int code = conn.getResponseCode();
            LogStore.add("上传 HTTP " + code + (code >= 200 && code < 300 ? "（成功）" : "（失败）"));
            return code >= 200 && code < 300;
        } catch (Exception e) {
            LogStore.add("上传异常: " + e.getClass().getSimpleName() + " " + e.getMessage());
            return false;
        } finally {
            if (conn != null) try { conn.disconnect(); } catch (Exception ignored) {}
        }
    }
}
