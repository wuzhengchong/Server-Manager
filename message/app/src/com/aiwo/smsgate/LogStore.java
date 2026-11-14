package com.aiwo.smsgate;

import android.content.Context;
import android.util.Log;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.Date;
import java.util.Locale;

/** 运行日志：内存环形缓冲 + 文件持久化（files/smsgate.log），界面可查看/复制 */
public class LogStore {
    private static final ArrayDeque<String> MEM = new ArrayDeque<>();
    private static final int MEM_MAX = 300;
    private static final long FILE_MAX = 128 * 1024;
    private static final SimpleDateFormat FMT =
            new SimpleDateFormat("MM-dd HH:mm:ss", Locale.CHINA);
    private static Context appCtx;

    private LogStore() {
    }

    /** 在主界面 onCreate 时初始化（应用上下文） */
    public static void init(Context c) {
        if (appCtx == null) appCtx = c.getApplicationContext();
    }

    public static synchronized void add(String msg) {
        String line = FMT.format(new Date()) + " " + msg;
        MEM.addLast(line);
        while (MEM.size() > MEM_MAX) MEM.pollFirst();
        Log.d("SmsGate", msg);
        appendFile(line);
    }

    public static synchronized String recent(int n) {
        StringBuilder sb = new StringBuilder();
        int skip = Math.max(0, MEM.size() - n);
        int i = 0;
        for (String line : MEM) {
            if (i++ < skip) continue;
            if (sb.length() > 0) sb.append('\n');
            sb.append(line);
        }
        return sb.length() == 0 ? "（暂无日志）" : sb.toString();
    }

    public static synchronized void clear() {
        MEM.clear();
        try {
            if (appCtx != null) new File(appCtx.getFilesDir(), "smsgate.log").delete();
        } catch (Exception ignored) {
        }
    }

    private static void appendFile(String line) {
        try {
            if (appCtx == null) return;
            File f = new File(appCtx.getFilesDir(), "smsgate.log");
            // 超过 128KB 截断到后 64KB，防止无限增长
            if (f.exists() && f.length() > FILE_MAX) {
                byte[] old = readFile(f);
                if (old != null) {
                    int cut = Math.max(0, old.length - 64 * 1024);
                    while (cut < old.length && old[cut] != '\n') cut++;
                    FileOutputStream fos = new FileOutputStream(f);
                    fos.write(old, cut, old.length - cut);
                    fos.close();
                }
            }
            FileOutputStream fos = new FileOutputStream(f, true);
            fos.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            fos.close();
        } catch (Exception ignored) {
        }
    }

    private static byte[] readFile(File f) {
        try {
            FileInputStream fis = new FileInputStream(f);
            byte[] buf = new byte[(int) f.length()];
            int off = 0, n;
            while (off < buf.length && (n = fis.read(buf, off, buf.length - off)) > 0) off += n;
            fis.close();
            return buf;
        } catch (Exception e) {
            return null;
        }
    }
}
