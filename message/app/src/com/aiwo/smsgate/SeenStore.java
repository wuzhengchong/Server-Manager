package com.aiwo.smsgate;

import android.content.Context;

import org.json.JSONArray;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/**
 * 已处理短信指纹表（持久化 seen.json）：
 * 广播路径与收件箱补扫路径都会入队，用指纹去重防止同一短信上传两次。
 * 指纹 = sender | body | 时间戳/1分钟（两条通道读到的是同一条短信的同一时间戳，
 * 1 分钟分桶足够对齐；窗口过大反而会误杀短时间内的同内容真实短信；
 * 上限 1000 条 FIFO 淘汰）。
 */
public class SeenStore {
    private static final int MAX = 1000;

    private static File file(Context c) {
        return new File(c.getFilesDir(), "seen.json");
    }

    private static synchronized JSONArray load(Context c) {
        try {
            FileInputStream fis = new FileInputStream(file(c));
            byte[] buf = new byte[(int) file(c).length()];
            int off = 0, n;
            while (off < buf.length && (n = fis.read(buf, off, buf.length - off)) > 0) off += n;
            fis.close();
            return new JSONArray(new String(buf, 0, off, StandardCharsets.UTF_8));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    private static synchronized void save(Context c, JSONArray arr) {
        try {
            File f = file(c);
            File tmp = new File(c.getFilesDir(), "seen.json.tmp");
            FileOutputStream fos = new FileOutputStream(tmp);
            fos.write(arr.toString().getBytes(StandardCharsets.UTF_8));
            fos.close();
            if (!tmp.renameTo(f)) {
                f.delete();
                tmp.renameTo(f);
            }
        } catch (Exception ignored) {
        }
    }

    public static String fingerprint(String sender, String body, long ts) {
        return (sender == null ? "" : sender) + "|"
                + (body == null ? "" : body) + "|" + (ts / 60000L);
    }

    /** @return true = 新消息（并记录指纹）；false = 已处理过，应跳过 */
    public static synchronized boolean markIfNew(Context c, String fp) {
        try {
            JSONArray arr = load(c);
            for (int i = 0; i < arr.length(); i++) {
                if (fp.equals(arr.optString(i))) return false;
            }
            arr.put(fp);
            while (arr.length() > MAX) arr.remove(0);
            save(c, arr);
            return true;
        } catch (Exception e) {
            return true;   // 去重失败宁可多发不可漏发
        }
    }
}
