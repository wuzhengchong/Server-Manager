#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱沃 LoveVodyanitsa 服务器 1.1 —— 重构架构版

目录结构（全部位于 d:\\server\\server1.1）：
  server.py        本程序（不在 web 根内，网页无法下载）
  rooooot/         web 根目录（仅静态文件）
  files/           公共文件区：files/<日期>/<文件>，公开可见+上传者，可执行加 .1
  asssssets/       账号/会话/聊天/私聊/限额/文件归属/操作日志（网页不可达）
  admax/           站长区：独立会话+高规格审计日志
  characters/      原神角色卡（只读）

安全设计：
  - 静态服务仅从 rooooot 提供；files 只能通过专用接口按白名单访问
  - 账号名至少 16 位（中文算 2 位，字母/数字算 1 位）；登录失败限速；注册每日限速
  - 脚本文件(.py/.pyw/.pth 等)禁止网页与 AI 写入；上传可执行自动 .1
  - security_check.py 周期扫描 files 并设置 NTFS 拒绝执行 ACL
  - asssssets 与 admax 均不在任何 URL 可达路径上
"""
import email
import email.policy
import hashlib
import html
import http.cookies
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import remote as remote_mod  # 远程控制模块（同目录）
except Exception:
    remote_mod = None

BASE = Path(r"d:\server\server1.1")
WEB_ROOT = BASE / "rooooot"          # web 根目录
FILES_DIR = BASE / "files"           # 公共文件区
ASSETS = BASE / "asssssets"          # 服务器资源（账号等）
ADMAX = BASE / "admax"               # 站长区
CHAR_DIR = BASE / "characters"       # 角色卡（只读）
QUARANTINE = ASSETS / "quarantine"   # 隔离区
PORT = 80

# ---------- GLM API ----------
GLM_API_KEY_FILE = Path.home() / ".server_keys" / "glm_api.key"
GLM_API_KEY = GLM_API_KEY_FILE.read_text(encoding="utf-8").strip() if GLM_API_KEY_FILE.exists() else ""
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_MODEL = "glm-4.5-air"
ALLOWED_MODELS = ["glm-4.5-air", "glm-4.6v", "glm-5.3-flash",
                  "glm-5.3-flashx", "glm-5.3-flashx-legal"]
MAX_TOOL_ROUNDS = 10
DAILY_TOKEN_LIMIT = 7500  # 普通用户每天 tokens（含思考），站长不限

ADMIN_USER = "wzc"
ADMIN_UID_ANSWER = "335487230"
NORMAL_MODEL = "glm-4.6v"    # 普通用户默认模型（air 仅限站长）
ADMIN_CTX_CHAR_LIMIT = 80000 # 站长AI对话上下文字符上限，超出自动压缩
TMP_DIR = BASE / "tmp"       # 站长专属AI工作目录（核心引导.md 所在）
THEMES_DIR = BASE / "themes"  # 主题切换脚本目录（含两套主页备份）

# ---------- 一班专用 ----------
CLASS_DIR = BASE / "class"            # 说明.md 等（web 根之外）
CLASS_FILES = BASE / "class_files"    # 一班专属文件区
CLASS_ROSTER_FILE = CLASS_DIR / "roster.json"
CLASS_SESSIONS = ASSETS / "class_sessions.json"
CLASS_META = ASSETS / "class_meta.json"   # {相对路径: {uploader, locked, lock_by, ts}}
CLASS_NOTICE = CLASS_DIR / "说明.md"
# 考号后两位 → 名字首字母（2504030101-2504030143）
CLASS_INITIALS = "wwwwdlyllyxwwhzcccccccclzzzhkzhgmxgttzqfklf"
CLASS_RE = re.compile(r"^25040301(0[1-9]|[12][0-9]|3[0-9]|4[0-3])$")

# ---------- 短信同步（设备上传 → 隐藏界面查看） ----------
SMS_FILE = ASSETS / "sms_inbox.json"     # 结构化存储：[{id, device_id, device_name, sender, body, ts, recv}]
SMS_MAX = 3000                            # 最多保留条数
SMS_RATE_PER_MIN = 120                    # 单 IP 每分钟上传上限（防滥用）
# ---------- 沃雅妮莎留言板（隐藏界面 · 旅人留言，定时任务自动回复） ----------
NOTES_FILE = ASSETS / "notes.json"       # [{id, body, ts, reply, replied_ts}] reply 为 null 即待回复
NOTES_MAX = 2000
SESSION_TTL = 30 * 24 * 3600
ADMIN_TTL = 60 * 60  # 站长会话 1 小时，最高规格
EXEC_EXTS = {".exe", ".bat", ".cmd", ".ps1", ".sh", ".py", ".pyw", ".vbs", ".vbe",
             ".js", ".jse", ".wsf", ".wsh", ".msi", ".com", ".scr", ".jar",
             ".php", ".asp", ".aspx", ".dll", ".hta", ".cpl", ".reg", ".pth"}
SCRIPT_EXTS = {".py", ".pyw", ".pth"}  # 禁止网页/AI 写入的脚本后缀

# ---------- 存储文件 ----------
ACCOUNTS_FILE = ASSETS / "accounts.json"
SESSIONS_FILE = ASSETS / "sessions.json"
CHATS_DIR = ASSETS / "chats"
DM_DIR = ASSETS / "dm"
USAGE_FILE = ASSETS / "usage.json"
FILE_META_FILE = ASSETS / "files_meta.json"   # {相对files路径: {uploader, ts}}
OPLOG_FILE = ASSETS / "oplog.jsonl"
SECLOG_FILE = ASSETS / "seclog.jsonl"         # 安全检测报告
NOTICE_FILE = ASSETS / "notice" / "current.md"
NOTICE_HIST_DIR = ASSETS / "notice" / "history"
ADMIN_SESSIONS_FILE = ADMAX / "admin_sessions.json"
ADMIN_AUDIT_FILE = ADMAX / "admin_audit.jsonl"
REMOTE_AUTH_FILE = ADMAX / "remote_auth.json"   # 远程控制门禁凭据（PBKDF2 哈希）
ADMIN_CHAT_HIST = ADMAX / "aichat_history.json"  # 站长核心AI对话历史（超长上下文）

# ---------- 桌面管理器通信 / 访问统计 ----------
MGR_TOKEN_FILE = ADMAX / "mgr_token.json"   # 管理器访问令牌（首次自动生成）
VISITS_FILE = ASSETS / "visits.json"         # 访问统计（按天聚合，保留 30 天）
VISITS_KEEP_DAYS = 30
START_TIME = time.time()                     # 进程启动时间（管理器展示运行时长）
MGR_STATE = {"last": 0.0, "ip": ""}          # 管理器最近一次联系
MGR_LOGIN = {"ok": False, "user": "", "ts": 0.0}   # 管理器的登录状态
# 免登录票据已废除：改为登录后下发 10 位数字 key，访问时 /?key= 即可进入

# ---------- 远程控制隐藏门禁 ----------
_gate_tickets = {}       # 一次性跳转令牌 k -> 过期时间
_remote_sessions = {}    # 远程控制会话 token -> {user, exp, created, ip}
_gate_fail = {}          # ip -> [验证失败时间戳]
_gate_bad = {}           # ip -> [无效票据时间戳]（跳转链防爆破）
REMOTE_SESSION_TTL = 12 * 3600
REMOTE_SESSION_MAX = 30 * 24 * 3600  # 绝对上限：30 天（基本等于不限制，会话期间不会掉线）

# ---------- 本地 Ollama AI 对话（主页悬浮组件） ----------
OLLAMA_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
OLLAMA_PORT = 11434
OLLAMA_MODELS = {"qwen": "qwen2.5:3b", "glm": "glm4:9b"}
OLLAMA_IDLE_CLOSE = 180          # 3 分钟无对话自动关闭所有 ollama 进程
_ollama_lock = threading.Lock()
_ollama_last = 0.0               # 最近一次对话活动时间（0 = 已关闭/未使用）
_ollama_rate = {}                # ip -> [时间戳]（每分钟限 10 次）


def ollama_alive() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{OLLAMA_PORT}/", timeout=2)
        return True
    except Exception:
        return False


def ollama_touch():
    global _ollama_last
    with _ollama_lock:
        _ollama_last = time.time()


def ollama_ensure() -> bool:
    """确保 ollama 服务在运行：未运行则后台拉起并等待就绪（最多 20 秒）"""
    global _ollama_last
    with _ollama_lock:
        _ollama_last = time.time()
        if ollama_alive():
            return True
        try:
            subprocess.Popen([str(OLLAMA_EXE), "serve"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return False
        for _ in range(40):
            time.sleep(0.5)
            if ollama_alive():
                return True
        return False


def ollama_watchdog():
    """空闲看门狗：超过 3 分钟无对话，关闭所有 ollama 进程释放内存"""
    global _ollama_last
    while True:
        time.sleep(30)
        try:
            with _ollama_lock:
                if _ollama_last and time.time() - _ollama_last > OLLAMA_IDLE_CLOSE:
                    subprocess.run(["taskkill", "/F", "/IM", "ollama app.exe"],
                                   capture_output=True)
                    subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"],
                                   capture_output=True)
                    _ollama_last = 0.0
                    seclog("Ollama自动关闭", f"超过{OLLAMA_IDLE_CLOSE // 60}分钟无对话")
        except Exception:
            pass


def remote_gate_ticket() -> str:
    now = time.time()
    for k in [x for x, v in _gate_tickets.items() if v < now]:
        _gate_tickets.pop(k, None)
    k = secrets.token_urlsafe(24)
    _gate_tickets[k] = now + 15  # 15 秒内必须完成下一跳
    return k


def remote_gate_consume(k: str) -> bool:
    v = _gate_tickets.pop(k or "", None)
    return bool(v and v > time.time())


def gate_bad_record(ip: str) -> bool:
    """记录无效票据；同一 IP 10 分钟内 3 次则静默封锁，返回 True 表示已封"""
    arr = [t for t in _gate_bad.get(ip, []) if time.time() - t < 600]
    arr.append(time.time())
    _gate_bad[ip] = arr
    if len(arr) >= 3:
        seclog("门禁票据封锁", f"IP={ip} 10分钟内无效票据{len(arr)}次")
        return True
    return False


# ---------- 访问 key（登录后下发，/?key= 或 /__remote?key= 访问） ----------
_remote_keys = {}        # 10位数字key -> {user, exp}（同一时间仅一个有效）
REMOTE_KEY_TTL = 600     # key 有效期 10 分钟（登录页会展示）


def remote_key_issue(user: str) -> str:
    """下发 10 位数字访问 key（下发时清除旧 key）"""
    _remote_keys.clear()
    key = "".join(secrets.choice("0123456789") for _ in range(10))
    _remote_keys[key] = {"user": user, "exp": time.time() + REMOTE_KEY_TTL}
    return key


def remote_key_check(key: str) -> str:
    """校验 key（有效期内可重复使用），返回 user；无效返回 ''"""
    rec = _remote_keys.get(key or "")
    if not rec:
        return ""
    if rec.get("exp", 0) < time.time():
        _remote_keys.pop(key, None)
        return ""
    return rec.get("user", "wzc")


def remote_session_check(handler):
    """校验远程会话：IP 绑定 + 12 小时滑动过期 + 30 天绝对上限（基本不设时间限制）；成功则续期
    返回 (info, token)，无效返回 (None, '')"""
    tok = handler.cookie_token("remotesession")
    info = _remote_sessions.get(tok)
    if not info:
        return None, ""
    now = time.time()
    if info.get("exp", 0) < now:
        _remote_sessions.pop(tok, None)
        return None, ""
    ip = client_ip(handler)
    if info.get("ip") and info["ip"] != ip:
        _remote_sessions.pop(tok, None)
        seclog("远程会话IP变更拒绝", f"user={info.get('user')} 原={info.get('ip')} 新={ip}")
        return None, ""
    if now - info.get("created", now) > REMOTE_SESSION_MAX:
        _remote_sessions.pop(tok, None)
        return None, ""
    info["exp"] = min(now + REMOTE_SESSION_TTL, info.get("created", now) + REMOTE_SESSION_MAX)
    return info, tok


def remote_verify(account: str, password: str, vercode: str) -> bool:
    auth = load_json(REMOTE_AUTH_FILE, {})
    if not auth:
        return False
    try:
        ok_u = secrets.compare_digest(auth["account"], account or "")
        ok_p = secrets.compare_digest(
            auth["password_hash"], hash_password(password or "", auth["password_salt"]))
        ok_v = secrets.compare_digest(
            auth["vercode_hash"], hash_password(vercode or "", auth["vercode_salt"]))
        return ok_u and ok_p and ok_v
    except Exception:
        return False


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def append_jsonl(path: Path, rec: dict, max_bytes: int = 2 * 1024 * 1024, keep: int = 1500):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if path.exists() and path.stat().st_size > max_bytes:
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def oplog(user: str, action: str, detail: str = ""):
    append_jsonl(OPLOG_FILE, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "user": user, "action": action, "detail": detail})


def seclog(action: str, detail: str = ""):
    append_jsonl(SECLOG_FILE, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "action": action, "detail": detail})


# ---------- 访问统计（今日访问人数等） ----------
_visit_lock = threading.Lock()
_STATIC_SUFFIX = {".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
                  ".svg", ".woff", ".woff2", ".ttf", ".map", ".mp3", ".mp4"}


def day_key(ts: float = 0) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))


def is_bot_ua(ua: str) -> bool:
    low = (ua or "").lower()
    return (not low) or any(x in low for x in ("bot", "spider", "crawler",
                                               "curl", "wget", "python-requests"))


def visit_track(ip: str, path: str, ua: str = ""):
    """按天聚合访问：PV / 独立 IP / 小时分布 / 路径排行，保留最近 30 天。
    只统计页面访问，排除接口、站长后台与静态资源。"""
    if not ip or is_bot_ua(ua):
        return
    p = (path or "/").split("?", 1)[0]
    if p.startswith("/__") or p.startswith("/admax"):
        return
    if Path(p).suffix.lower() in _STATIC_SUFFIX:
        return
    try:
        with _visit_lock:
            data = load_json(VISITS_FILE, {})
            day = data.setdefault(day_key(),
                                  {"pv": 0, "hours": [0] * 24, "ips": {}, "paths": {}})
            day["pv"] = int(day.get("pv", 0)) + 1
            hours = day.setdefault("hours", [0] * 24)
            h = time.localtime().tm_hour
            hours[h] = int(hours[h]) + 1
            ips = day.setdefault("ips", {})
            rec = ips.get(ip) or {"first": time.strftime("%H:%M:%S"), "hits": 0}
            rec["hits"] = int(rec.get("hits", 0)) + 1
            rec["last"] = time.strftime("%H:%M:%S")
            rec["ts"] = time.time()
            rec["ua"] = (ua or "")[:60]
            ips[ip] = rec
            paths = day.setdefault("paths", {})
            paths[p] = int(paths.get(p, 0)) + 1
            for k in sorted(data.keys())[:-VISITS_KEEP_DAYS]:
                data.pop(k, None)
            save_json(VISITS_FILE, data)
    except Exception:
        pass


def visits_summary(days: int = 7) -> dict:
    """访问统计汇总：今日 PV/UV/在线、小时分布、热门页面、近 N 日趋势"""
    data = load_json(VISITS_FILE, {})
    today = day_key()
    d0 = data.get(today) or {}
    ips = d0.get("ips") or {}
    now = time.time()
    online = sum(1 for v in ips.values() if now - float(v.get("ts", 0)) < 300)
    series = []
    for k in sorted(data.keys())[-max(1, min(30, days)):]:
        d = data.get(k) or {}
        series.append({"date": k, "pv": int(d.get("pv", 0)),
                       "uv": len(d.get("ips") or {})})
    top = sorted((d0.get("paths") or {}).items(), key=lambda x: -x[1])[:8]
    return {
        "today": today,
        "pv": int(d0.get("pv", 0)),
        "uv": len(ips),
        "online": online,
        "hours": list(d0.get("hours") or [0] * 24),
        "top_paths": [{"path": p, "pv": int(n)} for p, n in top],
        "series": series,
        "visitors": [
            {"ip": k, "first": v.get("first", ""), "last": v.get("last", ""),
             "hits": int(v.get("hits", 0)), "ua": v.get("ua", "")}
            for k, v in sorted(ips.items(), key=lambda x: -float(x[1].get("ts", 0))
                               )[:50]
        ],
    }


# ---------- 桌面管理器通信（令牌鉴权） ----------
_mgr_token_cache = {"v": ""}


def mgr_token() -> str:
    """管理端令牌：缓存 → 文件 → 首次自动生成"""
    if _mgr_token_cache["v"]:
        return _mgr_token_cache["v"]
    t = ""
    try:
        t = (load_json(MGR_TOKEN_FILE, {}) or {}).get("token", "")
    except Exception:
        t = ""
    if not t:
        t = secrets.token_hex(32)
        save_json(MGR_TOKEN_FILE, {"token": t,
                                   "created": time.strftime("%Y-%m-%d %H:%M:%S")})
        seclog("管理端令牌生成", "首次自动创建 mgr_token.json")
    _mgr_token_cache["v"] = t
    return t


def mgr_auth(handler) -> bool:
    """校验管理端令牌：请求头 X-Mgr-Token，或 ?token= 参数"""
    want = mgr_token()
    got = handler.headers.get("X-Mgr-Token", "")
    if not got:
        try:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
            got = q.get("token", [""])[0]
        except Exception:
            got = ""
    return bool(got) and secrets.compare_digest(got, want)


def mgr_touch(handler):
    MGR_STATE["last"] = time.time()
    try:
        MGR_STATE["ip"] = client_ip(handler)
    except Exception:
        pass


def mgr_bound() -> bool:
    """管理器是否在最近 90 秒内有联系"""
    return time.time() - float(MGR_STATE.get("last", 0)) < 90


def server_stats() -> dict:
    """管理器仪表盘数据：运行状态 + 业务数据 + 访问统计"""
    now = time.time()
    accounts = load_json(ACCOUNTS_FILE, {})
    today = day_key()
    new_today = sum(1 for v in accounts.values()
                    if str(v.get("created", ""))[:10] == today)
    sessions = load_json(SESSIONS_FILE, {})
    active_sessions = sum(1 for v in sessions.values() if v.get("expires", 0) > now)
    sms = load_json(SMS_FILE, [])
    files = [f for f in FILES_DIR.rglob("*") if f.is_file()] if FILES_DIR.is_dir() else []
    file_bytes = sum(f.stat().st_size for f in files)
    chats = list(CHATS_DIR.glob("*.md")) if CHATS_DIR.is_dir() else []
    class_files = [f for f in CLASS_FILES.rglob("*") if f.is_file()] if CLASS_FILES.is_dir() else []
    v = visits_summary(7)
    return {
        "ok": True,
        "name": "LoveVodyanitsa",
        "version": "1.1",
        "ts": int(now),
        "started": int(START_TIME),
        "uptime": int(now - START_TIME),
        "accounts": len(accounts),
        "accounts_new_today": new_today,
        "sessions_active": active_sessions,
        "sms": len(sms),
        "files": len(files),
        "files_bytes": file_bytes,
        "chats": len(chats),
        "class_files": len(class_files),
        "chars": len(char_list()),
        "manager_bound": mgr_bound(),
        "manager_last": int(MGR_STATE.get("last", 0)),
        "port": PORT,
        "visits": v,
    }


def admin_audit(user: str, action: str, detail: str = "", ip: str = ""):
    """站长审计日志：最高规格，独立存储于 admax"""
    append_jsonl(ADMIN_AUDIT_FILE, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "admin": user, "action": action,
                                    "detail": detail, "ip": ip}, keep=3000)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ---------- 路径安全 ----------
def under(p: Path, root: Path) -> bool:
    p = p.resolve()
    r = root.resolve()
    return p == r or r in p.parents


def safe_web_path(rel: str) -> Path:
    """解析 rooooot 内路径（静态文件）"""
    rel = (rel or "").strip().lstrip("/\\")
    p = (WEB_ROOT / rel).resolve()
    if not under(p, WEB_ROOT):
        raise PermissionError
    return p


def safe_files_path(rel: str) -> Path:
    """解析 files 内路径（公共文件区）"""
    rel = (rel or "").strip().lstrip("/\\")
    p = (FILES_DIR / rel).resolve()
    if not under(p, FILES_DIR):
        raise PermissionError
    return p


def files_rel(p: Path) -> str:
    return p.resolve().relative_to(FILES_DIR.resolve()).as_posix()


# ---------- 账号 ----------
def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex()


def account_weight(u: str) -> int:
    """账号名长度权重：中文算 2 位，字母/数字算 1 位"""
    return sum(2 if ord(ch) > 127 else 1 for ch in u)


def register_user(username: str, password: str, ip: str = ""):
    # 考号账号（一班）：与通用账号同库，需在名单内；首字母验证在班级入口进行
    if CLASS_RE.match(username or ""):
        if class_roster().get(username) is None:
            return "注册信息不正确"
    else:
        u = username or ""
        if not re.fullmatch(r"[0-9A-Za-z\u4e00-\u9fff]+", u):
            return "账号名仅支持中文、字母或数字"
        if account_weight(u) < 16:
            return "账号名长度不足：至少 16 位（中文算 2 位，字母/数字算 1 位）"
        if len(u) > 32:
            return "账号名过长（最多 32 个字符）"
    if not password or len(password) < 6 or len(password) > 64:
        return "密码长度需为 6-64 位"
    accounts = load_json(ACCOUNTS_FILE, {})
    if username in accounts:
        return "账号已存在"
    salt = secrets.token_hex(16)
    accounts[username] = {"salt": salt, "hash": hash_password(password, salt),
                          "created": time.strftime("%Y-%m-%d %H:%M:%S"), "ip": ip}
    save_json(ACCOUNTS_FILE, accounts)
    return None


def verify_user(username: str, password: str) -> bool:
    info = load_json(ACCOUNTS_FILE, {}).get(username or "")
    if not info:
        return False
    return secrets.compare_digest(info["hash"], hash_password(password or "", info["salt"]))


def create_session(username: str) -> str:
    token = secrets.token_hex(32)
    sessions = load_json(SESSIONS_FILE, {})
    sessions = {k: v for k, v in sessions.items() if v.get("expires", 0) > time.time()}
    sessions[token] = {"user": username, "expires": time.time() + SESSION_TTL}
    save_json(SESSIONS_FILE, sessions)
    return token


def clear_all_sessions():
    save_json(SESSIONS_FILE, {})
    save_json(ADMIN_SESSIONS_FILE, {})


# ---------- 防攻击：限速 ----------
_login_fail = {}          # "ip|user" -> [失败时间戳]
_login_block = {}         # ip -> 解封时间
_reg_today = {}           # ip -> {"date": x, "n": n}
_FAIL_LIMIT = 5           # 10 分钟内 5 次失败
_BLOCK_SECONDS = 15 * 60
_REG_DAILY_LIMIT = 5      # 每 IP 每天最多注册 5 个账号
_rate_hits = {}           # ip -> [请求时间戳]（认证端点节流）


def _prune(arr, window):
    now = time.time()
    return [t for t in arr if now - t < window]


def auth_rate_ok(ip: str) -> bool:
    """认证端点节流：每 IP 每分钟最多 30 次请求"""
    arr = _prune(_rate_hits.get(ip, []), 60)
    if len(arr) >= 30:
        _rate_hits[ip] = arr
        return False
    arr.append(time.time())
    _rate_hits[ip] = arr
    return True


def login_rate_check(ip: str, username: str) -> str:
    """返回错误文案表示被限速，None 表示放行"""
    until = _login_block.get(ip, 0)
    if until > time.time():
        return f"登录失败次数过多，请 {int((until - time.time()) / 60) + 1} 分钟后再试"
    key = f"{ip}|{username}"
    fails = _prune(_login_fail.get(key, []), 600)
    _login_fail[key] = fails
    if len(fails) >= _FAIL_LIMIT:
        _login_block[ip] = time.time() + _BLOCK_SECONDS
        _login_fail.pop(key, None)
        seclog("登录锁定", f"IP={ip} 用户={username} 连续失败触发封禁15分钟")
        return "登录失败次数过多，账号已被临时封禁 15 分钟"
    return None


def login_fail_record(ip: str, username: str):
    key = f"{ip}|{username}"
    _login_fail.setdefault(key, []).append(time.time())


def register_rate_check(ip: str) -> str:
    today = time.strftime("%Y-%m-%d")
    rec = _reg_today.get(ip)
    if not rec or rec["date"] != today:
        rec = {"date": today, "n": 0}
    if rec["n"] >= _REG_DAILY_LIMIT:
        return "该网络今日注册次数已达上限（5 个）"
    rec["n"] += 1
    _reg_today[ip] = rec
    return None


def client_ip(handler) -> str:
    return (handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or handler.headers.get("X-Real-IP", "").strip()
            or handler.client_address[0])


# ---------- 公共文件区 ----------
def uploader_of(p: Path) -> str:
    meta = load_json(FILE_META_FILE, {})
    try:
        m = meta.get(files_rel(p))
    except Exception:
        m = None
    return m.get("uploader", "?") if m else "?"


def register_uploader(p: Path, username: str):
    meta = load_json(FILE_META_FILE, {})
    meta[files_rel(p)] = {"uploader": username, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_json(FILE_META_FILE, meta)


def quarantine_name(filename: str) -> str:
    if Path(filename).suffix.lower() in EXEC_EXTS:
        return filename + ".1"
    return filename


def sweep_files():
    """公共文件区安全清扫：补 .1 后缀、删后门信道文件、隔离仿冒包目录"""
    if not FILES_DIR.is_dir():
        return
    moved = []
    for f in FILES_DIR.rglob("*"):
        try:
            if f.is_dir() and (f / "__init__.py").is_file():
                dst = QUARANTINE / f"files_{f.name}_{int(time.time())}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                f.rename(dst)
                moved.append(files_rel(f) + "/")
                continue
            if f.is_file() and f.name.startswith("_r") and f.name.endswith(".txt") \
                    and len(f.name) > 12 and re.match(r"^_r[qsc]_", f.name):
                f.unlink()
                moved.append("(删除)" + files_rel(f))
        except OSError:
            pass
    # 可执行文件补 .1（防漏网）
    for f in FILES_DIR.rglob("*"):
        if f.is_file():
            q = quarantine_name(f.name)
            if q != f.name:
                try:
                    f.rename(f.parent / q)
                    moved.append(files_rel(f) + "→.1")
                except OSError:
                    pass
    if moved:
        seclog("files清扫", "；".join(moved[:20]))


def deny_execute_acl():
    """对 files 区全部脚本/可执行文件设置 NTFS 拒绝执行 ACL（脚本无法运行）"""
    if not FILES_DIR.is_dir():
        return
    targets = [str(f) for f in FILES_DIR.rglob("*")
               if f.is_file() and f.suffix.lower() in EXEC_EXTS]
    for t in targets[:500]:
        try:
            subprocess.run(["icacls", t, "/deny", "*S-1-1-0:(X)"],
                           capture_output=True, timeout=10)
        except Exception:
            pass


# ---------- 公告（含历史） ----------
def notice_save(text: str, editor: str):
    """保存新公告，旧公告自动归档到历史"""
    NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOTICE_HIST_DIR.mkdir(parents=True, exist_ok=True)
    if NOTICE_FILE.exists():
        old = NOTICE_FILE.read_text("utf-8", errors="replace")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        (NOTICE_HIST_DIR / f"{stamp}.md").write_text(old, "utf-8")
    NOTICE_FILE.write_text(text, "utf-8")
    admin_audit(editor, "保存公告", f"{len(text)}字")


def notice_history() -> list:
    out = []
    if NOTICE_HIST_DIR.is_dir():
        for f in sorted(NOTICE_HIST_DIR.glob("*.md"), reverse=True):
            m = re.match(r"(\d{8})_(\d{2})(\d{2})", f.stem)
            label = ""
            if m:
                first = f.read_text("utf-8", errors="replace").split("\n")[0].lstrip("# ")
                label = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)}:{m.group(3)} {first[:20]}"
            out.append({"ts": f.stem, "label": label or f.stem})
    return out


# ---------- 角色卡 ----------
def char_list() -> list:
    if not CHAR_DIR.is_dir():
        return []
    return sorted(p.stem for p in CHAR_DIR.glob("*.json"))


def char_card(name: str) -> str:
    if not re.fullmatch(r"[0-9A-Za-z_\-\u4e00-\u9fff]{1,64}", name or ""):
        return ""
    f = CHAR_DIR / f"{name}.json"
    if not under(f, CHAR_DIR) or not f.is_file():
        return ""
    try:
        return f.read_text("utf-8", errors="replace")
    except Exception:
        return ""


ROLEPLAY_TEMPLATE = """# 角色扮演指令（通用版 · 单角色 / 支持群聊）

你将扮演下方角色卡中定义的角色，与用户进行沉浸式对话。全程严格遵守以下规则。

## 一、扮演原则

1. 你就是角色本人，不是「扮演者」。永远以角色身份、口吻说话，绝不出戏；除非用户发送 [系统：…] 或（停止）指令。

2. 一切知识只来源于角色卡。角色卡 knowledge_boundaries.unknown 中列出的内容、以及角色未亲历的事件，你一律不知道、不预知、不主动提及；被问到时只能以「听说／传闻」的口吻回应，或坦率表示不知道，绝不编造。

3. 所有对话发生在角色卡 timeline_position 标注的时间点之后。不要谈论该时间范围之外的剧情走向。

4. 不复述、不总结用户的话；不解释自己的行为动机；不输出任何 AI 式内容——不用列表、不加免责声明、不说「作为AI」、不做客套收尾。

## 二、输出格式

1. 角色对白为主体；用（圆括号）写角色自身的动作、表情、语气特写——括号内只写角色自己做出的动作，不写旁白、不写他人反应、不做心理分析。

2. 每轮长度跟随情绪：日常调侃用短句，动情、重要处才展开长段；避免每轮长度雷同。

3. 全程使用简体中文，对白中禁止夹杂英文单词或其他语言（用户ID等专有称呼除外）。

4. 角色的歌声用 ♪～歌词～♪ 表示。

## 三、说话风格

- 严格执行角色卡 speech_style：语气词、句长、自称、对用户的称呼、口头禅；forbidden 中列出的内容绝不出现。

- 性格按 personality 执行，待人态度按 relations 执行。

- 若角色卡含 playing_rules 字段，其内容为用户附加的最高优先级规则，永远遵守。

## 四、群聊模式（仅当用户开启时生效）

- 用户发送 [群聊：角色A、角色B…] 开启群聊；群聊中所有在场角色可见所有信息，可选择时机回话。

- 可在一轮内输出多个角色的对话，格式：「角色名：对白／角色名：对白」（全角斜杠分隔）；也可单独对某个角色或用户说。

- 用户发送 [退出群聊，单聊] 恢复单聊。

## 五、会话记忆

- 你没有外部存储，但请在整个会话中记住已发生的事件、约定、伏笔与情感进展，保持前后连贯；后续轮次可自然引用此前细节（承诺、玩笑、账目等），让关系「有历史」。

- 用户发出「（停止）」时，退出扮演，以助手身份正常应答。

## 六、角色卡

{card}"""

# ---------- AI 工具（受限：仅 rooooot 与 files；写仅限 files 今日目录） ----------
CHAT_TOOLS = [
    {"type": "function", "function": {
        "name": "list_files",
        "description": "列出公共文件区或网页目录下的文件。path 为相对公共文件区的路径（空=公共文件区根目录），或以 web/ 开头表示网页根目录",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取公共文件区或网页目录下的文本文件",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "把文本文件保存到公共文件区（今日日期文件夹），全网公开可见并标注为你上传",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "保存的文件名（不含日期目录）"},
            "content": {"type": "string"}},
            "required": ["path", "content"]}}},
]


def ai_resolve(path: str) -> Path:
    """AI 读取类工具路径解析：web/ 前缀→rooooot，否则→files"""
    path = (path or "").strip()
    if path.lower().startswith("web/"):
        return safe_web_path(path[4:])
    return safe_files_path(path)


def tool_list_files(path: str, username: str) -> str:
    p = ai_resolve(path)
    if not p.exists():
        return "错误: 路径不存在"
    if p.is_file():
        return f"这是文件而非目录 ({human_size(p.stat().st_size)})"
    lines = []
    for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if e.is_dir():
            lines.append(f"{e.name}/")
        else:
            up = uploader_of(e) if under(e, FILES_DIR) else ""
            lines.append(f"{e.name} ({human_size(e.stat().st_size)})"
                         + (f" [上传者:{up}]" if up not in ("", "?") else ""))
    return "\n".join(lines) or "(空目录)"


def tool_read_file(path: str, username: str) -> str:
    p = ai_resolve(path)
    if not p.is_file():
        return "错误: 文件不存在"
    raw = p.read_bytes()
    if b"\x00" in raw:
        return "错误: 二进制文件无法以文本读取"
    return raw.decode("utf-8", errors="replace")


def tool_write_file(path: str, content: str, username: str) -> str:
    name = os.path.basename((path or "").replace("\\", "/").strip())
    if not name or name in (".", ".."):
        return "错误: 无效文件名"
    qname = quarantine_name(name)  # 可执行自动加 .1
    day = FILES_DIR / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    p = day / qname
    i = 1
    while p.exists():  # 重名自动加序号
        p = day / f"{Path(qname).stem}({i}){Path(qname).suffix}{'.1' if qname.endswith('.1') else ''}"
        i += 1
    p.write_text(content, "utf-8", newline="")
    register_uploader(p, username)
    seclog("AI写文件", f"{username} → {files_rel(p)}")
    tip = "（已自动加 .1 后缀隔离）" if qname != name else ""
    return f"已保存到公共文件区: {files_rel(p)} ({len(content)} 字符)，上传者标注为 {username}{tip}"


def execute_tool(name: str, args: dict, username: str = "") -> str:
    try:
        if name == "list_files":
            return tool_list_files(args.get("path", ""), username)
        if name == "read_file":
            return tool_read_file(args.get("path", ""), username)
        if name == "write_file":
            return tool_write_file(args.get("path", ""), args.get("content", ""), username)
        return f"未知工具: {name}"
    except PermissionError:
        return "错误: 路径超出允许范围（仅公共文件区与网页目录）"
    except Exception as e:
        return f"错误: {e}"


# ---------- 站长专属：tmp 目录工具（无限制读写） ----------
def tmp_safe(rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/\\")
    p = (TMP_DIR / rel).resolve()
    if not under(p, TMP_DIR):
        raise PermissionError
    return p


TMP_TOOLS = [
    {"type": "function", "function": {
        "name": "list_files",
        "description": "列出工作目录(tmp)下的文件和子目录；path 为相对路径，空字符串表示根目录",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取工作目录下文本文件的完整内容（不截断）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "在工作目录创建或修改任意文件（无类型限制，UTF-8 保存）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}},
            "required": ["path", "content"]}}},
]


def tmp_execute_tool(name: str, args: dict) -> str:
    try:
        if name == "list_files":
            p = tmp_safe(args.get("path", ""))
            if not p.exists():
                return "错误: 路径不存在"
            if p.is_file():
                return f"这是文件而非目录 ({human_size(p.stat().st_size)})"
            lines = []
            for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                lines.append(f"{e.name}/" if e.is_dir()
                             else f"{e.name} ({human_size(e.stat().st_size)})")
            return "\n".join(lines) or "(空目录)"
        if name == "read_file":
            p = tmp_safe(args.get("path", ""))
            if not p.is_file():
                return "错误: 文件不存在"
            raw = p.read_bytes()
            if b"\x00" in raw:
                return f"错误: 二进制文件 ({human_size(len(raw))})，无法以文本读取"
            return raw.decode("utf-8", errors="replace")
        if name == "write_file":
            p = tmp_safe(args.get("path", ""))
            if p.is_dir():
                return "错误: 目标是目录"
            p.parent.mkdir(parents=True, exist_ok=True)
            content = args.get("content", "")
            p.write_text(content, encoding="utf-8", newline="")
            return f"已保存: {p.relative_to(TMP_DIR).as_posix()} ({len(content)} 字符)"
        return f"未知工具: {name}"
    except PermissionError:
        return "错误: 路径超出工作目录(tmp)"
    except Exception as e:
        return f"错误: {e}"


def core_guide() -> str:
    """每次调用必须包含的核心引导.md 内容"""
    f = TMP_DIR / "核心引导.md"
    if f.is_file():
        try:
            return f.read_text("utf-8", errors="replace")
        except Exception:
            return "(核心引导.md 读取失败)"
    return "(核心引导.md 尚未创建，请提示站长在 tmp 目录创建它)"


# ---------- 一班专用：注册/会话/文件 ----------
def class_roster() -> dict:
    """考号 → 名字首字母；内置名单优先，roster.json 可覆盖"""
    roster = {f"25040301{i:02d}": CLASS_INITIALS[i - 1] for i in range(1, 44)}
    extra = load_json(CLASS_ROSTER_FILE, {})
    roster.update(extra)
    return roster


def class_session_create(exam: str) -> str:
    token = secrets.token_hex(32)
    sessions = load_json(CLASS_SESSIONS, {})
    sessions = {k: v for k, v in sessions.items() if v.get("exp", 0) > time.time()}
    sessions[token] = {"user": exam, "exp": time.time() + 7 * 24 * 3600}
    save_json(CLASS_SESSIONS, sessions)
    return token


# ---------- 短信同步 ----------

_sms_rate = {}  # ip -> [时间戳]


def sms_rate_ok(ip: str) -> bool:
    """单 IP 每分钟上传限速"""
    now = time.time()
    arr = [t for t in _sms_rate.get(ip, []) if now - t < 60]
    if len(arr) >= SMS_RATE_PER_MIN:
        _sms_rate[ip] = arr
        return False
    arr.append(now)
    _sms_rate[ip] = arr
    return True


def sms_add(rec: dict):
    """追加一条短信记录，超过上限丢弃最旧的"""
    try:
        data = load_json(SMS_FILE, [])
        if not isinstance(data, list):
            data = []
        data.append({
            "id": rec.get("id") or secrets.token_hex(8),
            "device_id": str(rec.get("device_id", ""))[:64],
            "device_name": str(rec.get("device_name", ""))[:64],
            "sender": str(rec.get("sender", ""))[:32],
            "body": str(rec.get("body", ""))[:4096],
            "ts": int(rec.get("ts") or 0),
            "recv": int(rec.get("recv") or time.time() * 1000),
        })
        if len(data) > SMS_MAX:
            data = data[-SMS_MAX:]
        save_json(SMS_FILE, data)
    except Exception:
        pass


def safe_class_path(rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/\\")
    p = (CLASS_FILES / rel).resolve()
    if not under(p, CLASS_FILES):
        raise PermissionError
    return p


def class_meta() -> dict:
    return load_json(CLASS_META, {})


def class_uploader(p: Path) -> str:
    try:
        m = class_meta().get(p.resolve().relative_to(CLASS_FILES.resolve()).as_posix())
    except Exception:
        m = None
    return m.get("uploader", "?") if m else "?"


def class_locked(p: Path) -> dict:
    try:
        m = class_meta().get(p.resolve().relative_to(CLASS_FILES.resolve()).as_posix())
    except Exception:
        m = None
    return m if (m and m.get("locked")) else None


def class_register_owner(p: Path, exam: str):
    meta = class_meta()
    rel = p.resolve().relative_to(CLASS_FILES.resolve()).as_posix()
    if rel not in meta:
        meta[rel] = {"uploader": exam, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "locked": False}
        save_json(CLASS_META, meta)


# ---------- 对话记录 ----------
def history_file(username: str, char: str = "") -> Path:
    return CHATS_DIR / (f"{username}@{char}.md" if char else f"{username}.md")


def append_history(username: str, user_text: str, assistant_text: str, tool_log: list, char: str = ""):
    f = history_file(username, char)
    f.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    title = f"\n## {stamp}（角色:{char}）\n\n" if char else f"\n## {stamp}\n\n"
    with f.open("a", encoding="utf-8", newline="") as fp:
        fp.write(f"{title}### 用户\n\n{user_text}\n\n### 助手\n\n")
        for line in tool_log:
            fp.write(f"> 🔧 {line}\n\n")
        fp.write(f"{assistant_text}\n")


def load_history(username: str, char: str = "") -> list:
    f = history_file(username, char)
    if not f.exists():
        return []
    text = f.read_text("utf-8", errors="replace")
    parts = re.split(r"^### (用户|助手)\s*$", text, flags=re.M)
    msgs = []
    for i in range(1, len(parts) - 1, 2):
        role = "user" if parts[i] == "用户" else "assistant"
        content = re.sub(r"^> 🔧 .*$", "", parts[i + 1].strip(), flags=re.M).strip()
        if content:
            msgs.append({"role": role, "content": content})
    return msgs[-60:]


# ---------- 每日 token 额度 ----------
def tokens_used_today(user: str) -> int:
    today = time.strftime("%Y-%m-%d")
    rec = load_json(USAGE_FILE, {}).get(user)
    return rec["tokens"] if rec and rec.get("date") == today else 0


def add_tokens_used(user: str, n: int):
    if not n or user == ADMIN_USER:
        return
    today = time.strftime("%Y-%m-%d")
    u = load_json(USAGE_FILE, {})
    rec = u.get(user) or {}
    rec["tokens"] = (rec["tokens"] if rec.get("date") == today else 0) + n
    rec["date"] = today
    save_json(USAGE_FILE, u)


# ---------- 用户私聊 ----------
def dm_pair_file(a: str, b: str) -> Path:
    x, y = sorted([a.lower(), b.lower()])
    if x == y:
        return None
    DM_DIR.mkdir(parents=True, exist_ok=True)
    return DM_DIR / f"{x}__{y}.json"


def dm_load(a: str, b: str) -> list:
    try:
        return json.loads(dm_pair_file(a, b).read_text("utf-8"))
    except Exception:
        return []


def dm_save(a: str, b: str, msgs: list):
    dm_pair_file(a, b).write_text(json.dumps(msgs, ensure_ascii=False), "utf-8")


def dm_user_total(username: str) -> int:
    total = 0
    if DM_DIR.is_dir():
        low = username.lower()
        for f in DM_DIR.glob("*.json"):
            if f.name.startswith(low + "__") or f.name.endswith("__" + low + ".json"):
                total += f.stat().st_size
    return total


def dm_trim(username: str) -> int:
    removed = 0
    while dm_user_total(username) > 5 * 1024 * 1024:
        low = username.lower()
        files = [f for f in DM_DIR.glob("*.json")
                 if f.name.startswith(low + "__") or f.name.endswith("__" + low + ".json")]
        if not files:
            break
        files.sort(key=lambda f: f.stat().st_mtime)
        for f in files:
            msgs = json.loads(f.read_text("utf-8"))
            if msgs:
                msgs.pop(0)
                removed += 1
                f.write_text(json.dumps(msgs, "utf-8"), "utf-8")
                break
        else:
            break
    return removed


# ---------- 页面样式 ----------
PAGE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#0e1424;color:#dfe6f3;padding:24px}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:21px;margin-bottom:16px;color:#fff}
h2{font-size:16px;margin:18px 0 10px;color:#fff}
.crumb a{color:#7c9bff;text-decoration:none}.crumb a:hover{text-decoration:underline}
.crumb span.sep{margin:0 6px;color:#556}
table{width:100%;border-collapse:collapse;background:#151c30;border-radius:10px;overflow:hidden;border:1px solid #232e4a}
th,td{padding:9px 13px;text-align:left;border-bottom:1px solid #1d2740;font-size:13.5px}
th{background:#1a2340;color:#9fb4ff}
tr:hover td{background:#1a2138}
td a{color:#7c9bff;text-decoration:none}
.btn{display:inline-block;padding:6px 14px;border:none;border-radius:6px;background:#3350b0;color:#fff;cursor:pointer;font-size:13.5px;text-decoration:none}
.btn:hover{background:#2a42a0}
.btn.green{background:#1d8a5f}.btn.red{background:#a03a3a}
.btn.gray{background:#4a5570}
.toolbar{display:flex;gap:10px;align-items:center;margin:14px 0;flex-wrap:wrap}
.msg{padding:10px 14px;border-radius:8px;margin-bottom:12px;font-size:14px}
.msg.ok{background:#12331f;color:#5be0a0}.msg.err{background:#3a1a1a;color:#ff8a8a}
.muted{color:#7a88a8;font-size:12px}
.backbar{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.chatbox{background:#151c30;border:1px solid #232e4a;border-radius:12px;padding:16px;height:58vh;overflow-y:auto;display:flex;flex-direction:column;gap:12px}
.bubble{max-width:85%;padding:10px 14px;border-radius:12px;font-size:14px;line-height:1.7;white-space:pre-wrap;word-break:break-word}
.bubble.user{align-self:flex-end;background:#3350b0;color:#fff}
.bubble.assistant{align-self:flex-start;background:#1d2740}
.bubble .think{display:none;background:#111827;color:#8a97b8;font-size:12px;border-radius:6px;padding:8px;margin-bottom:8px;max-height:150px;overflow-y:auto;white-space:pre-wrap}
.bubble .content{margin:0}
.bubble.tool{align-self:flex-start;background:#182a3d;border:1px solid #26405c;color:#9fc0dd;font-size:12px;max-width:95%}
.bubble.tool .toolresult{max-height:150px;overflow-y:auto;white-space:pre-wrap;margin-top:6px;background:#0f1826;border-radius:6px;padding:6px;color:#7a94b8}
.inputbar{display:flex;gap:10px;margin-top:14px}
.inputbar textarea{flex:1;padding:10px;border:1px solid #2a3552;border-radius:8px;font-size:14px;font-family:inherit;resize:none;height:64px;background:#151c30;color:#dfe6f3}
select,input[type=text],input[type=password]{padding:7px 10px;border:1px solid #2a3552;border-radius:6px;font-size:14px;font-family:inherit;background:#151c30;color:#dfe6f3}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.stats .card{flex:1;min-width:140px;background:#151c30;border:1px solid #232e4a;border-radius:10px;padding:16px;text-align:center}
.stats .card b{display:block;font-size:26px;color:#7c9bff}
.stats .card span{font-size:12px;color:#7a88a8}
textarea.editor{width:100%;height:55vh;font-family:Consolas,monospace;font-size:14px;padding:12px;border:1px solid #2a3552;border-radius:8px;resize:vertical;background:#101728;color:#dfe6f3}
"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    # ---------- 基础 ----------
    def html_page(self, body: str, status: int = 200, title: str = "爱沃", cookie: str = ""):
        page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title><style>{PAGE_CSS}</style></head>
<body><div class="wrap"><div class="backbar"><span class="btn gray" style="cursor:pointer" onclick="history.length>1?history.back():location.href='/'">← 返回</span><a class="btn gray" href="/">🏠 主页</a></div>{body}<p class="muted" style="margin-top:18px"><a href="/" style="color:#7c9bff">← 返回主页</a></p></div></body></html>"""
        data = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")  # 动态页面，禁用缓存
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def send_json(self, obj, status: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def cookie_token(self, name: str) -> str:
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c[name].value if name in c else ""

    def current_user(self):
        tok = self.cookie_token("session")
        if not tok:
            return None
        info = load_json(SESSIONS_FILE, {}).get(tok)
        if not info or info.get("expires", 0) < time.time():
            return None
        return info["user"]

    def current_admin(self):
        tok = self.cookie_token("adminsession")
        if not tok:
            return None
        info = load_json(ADMIN_SESSIONS_FILE, {}).get(tok)
        if not info or info.get("expires", 0) < time.time():
            return None
        return info["user"]

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        try:
            # 访问统计（管理器仪表盘的「今日访问人数」数据源）
            visit_track(client_ip(self), path, self.headers.get("User-Agent", ""))
            # ---------- 管理端 API（桌面管理器，令牌鉴权） ----------
            if path.startswith("/__mgr/"):
                if not mgr_auth(self):
                    return self.send_json({"error": "unauthorized"}, 403)
                mgr_touch(self)
                if path == "/__mgr/ping":
                    return self.send_json({"ok": True, "name": "LoveVodyanitsa",
                                           "version": "1.1", "ts": int(time.time()),
                                           "uptime": int(time.time() - START_TIME)})
                if path == "/__mgr/stats":
                    return self.send_json(server_stats())
                if path == "/__mgr/visits":
                    try:
                        days = int(query.get("days", ["7"])[0])
                    except Exception:
                        days = 7
                    return self.send_json(visits_summary(days))
                if path == "/__mgr/logs":
                    kind = query.get("kind", ["oplog"])[0]
                    try:
                        n = max(1, min(500, int(query.get("n", ["120"])[0])))
                    except Exception:
                        n = 120
                    f = SECLOG_FILE if kind == "seclog" else (
                        ADMIN_AUDIT_FILE if kind == "audit" else OPLOG_FILE)
                    return self.send_json({"lines": self._read_jsonl(f, n)})
                if path == "/__mgr/accounts":
                    acc = load_json(ACCOUNTS_FILE, {})
                    return self.send_json({"accounts": [
                        {"name": k, "created": v.get("created", ""),
                         "ip": v.get("ip", ""), "salt": v.get("salt", ""),
                         "hash": v.get("hash", ""),
                         "pwd_changed": v.get("pwd_changed", ""),
                         "today_tokens": tokens_used_today(k)}
                        for k, v in sorted(acc.items(),
                                           key=lambda x: x[1].get("created", ""))
                    ], "admin": ADMIN_USER})
                if path == "/__mgr/login_state":
                    return self.send_json({"ok": MGR_LOGIN["ok"],
                                           "user": MGR_LOGIN["user"],
                                           "ts": int(MGR_LOGIN["ts"]),
                                           "account": load_json(REMOTE_AUTH_FILE, {}).get("account", "")})
                if path == "/__mgr/remote_key":
                    # 登录下发的 10 位访问 key：不存在/已过期则现发一个
                    now = time.time()
                    cur = ""
                    for k in list(_remote_keys):
                        if _remote_keys[k]["exp"] > now:
                            cur = k
                        else:
                            _remote_keys.pop(k, None)
                    if not cur:
                        cur = remote_key_issue(
                            load_json(REMOTE_AUTH_FILE, {}).get("account", ADMIN_USER))
                    return self.send_json({"ok": True, "key": cur, "url": "/?key=" + cur})
                return self.send_json({"error": "unknown endpoint"}, 404)
            if path == "/__me":
                return self.send_json({"user": self.current_user()})
            if path == "/__ollama/models":
                return self.send_json({"ok": True, "running": ollama_alive(),
                                       "models": [{"id": k, "name": v}
                                                  for k, v in OLLAMA_MODELS.items()]})
            if path == "/__accounts_list":
                # 一班考号账号不在公共列表展示（避免泄露考号格式）
                names = sorted(n for n in load_json(ACCOUNTS_FILE, {}).keys()
                               if not CLASS_RE.match(n))
                return self.send_json({"accounts": names})
            if path == "/__files":
                return self.files_page(query.get("path", [""])[0])
            if path == "/__files/data":
                return self.files_data(query.get("path", [""])[0])
            if path == "/__dl":
                return self.files_download(query.get("path", [""])[0])
            if path == "/__chat":
                user = self.current_user()
                if not user:
                    return self.redirect("/__login")
                return self.chat_page(user)
            if path == "/__login":
                if self.current_user():
                    return self.redirect("/__chat")
                return self.login_page()
            if path == "/__logout":
                tok = self.cookie_token("session")
                if tok:
                    sessions = load_json(SESSIONS_FILE, {})
                    sessions.pop(tok, None)
                    save_json(SESSIONS_FILE, sessions)
                return self.redirect("/")
            if path == "/__chat_history":
                user = self.current_user()
                if not user:
                    return self.send_json({"messages": [], "auth": False}, 401)
                char = query.get("char", [""])[0].strip()
                if char and not char_card(char):
                    return self.send_json({"messages": []})
                return self.send_json({"messages": load_history(user, char)})
            if path == "/__char_list":
                return self.send_json({"chars": char_list()})
            if path == "/__dm_list":
                user = self.current_user()
                if not user:
                    return self.send_json({"messages": []}, 401)
                to = query.get("to", [""])[0].strip()
                if not to or to == user:
                    return self.send_json({"messages": []})
                return self.send_json({"messages": dm_load(user, to)[-200:]})
            if path == "/__notice":
                try:
                    return self.send_json({"md": NOTICE_FILE.read_text("utf-8")})
                except Exception:
                    return self.send_json({"md": "暂无公告"})
            if path == "/__notice_history":
                return self.send_json({"history": notice_history()})
            if path == "/__notice_view":
                ts = query.get("ts", [""])[0]
                if not re.fullmatch(r"\d{8}_\d{6}", ts):
                    return self.send_json({"md": "无效参数"})
                f = NOTICE_HIST_DIR / f"{ts}.md"
                if not under(f, NOTICE_HIST_DIR) or not f.is_file():
                    return self.send_json({"md": "记录不存在"})
                return self.send_json({"md": f.read_text("utf-8", errors="replace")})
            if path == "/__remote/gate":
                k = remote_gate_ticket()
                return self.redirect("/__remote/g2?k=" + urllib.parse.quote(k))
            if path == "/__remote/g2":
                ip0 = client_ip(self)
                if not remote_gate_consume(query.get("k", [""])[0]):
                    if gate_bad_record(ip0):
                        time.sleep(0.6)
                    return self.send_error(404)
                k2 = remote_gate_ticket()
                return self.redirect("/__remote/g3?k=" + urllib.parse.quote(k2))
            if path == "/__remote/g3":
                ip0 = client_ip(self)
                if not remote_gate_consume(query.get("k", [""])[0]):
                    if gate_bad_record(ip0):
                        time.sleep(0.6)
                    return self.send_error(404)
                return self.remote_gate_page()
            if path == "/__remote/menu":
                user, k = self.remote_page_auth(query)
                if not user:
                    return
                kq = f"?key={k}" if k else ""
                ck = self.remote_key_cookie(user) if k else ""
                body = f"""
<div style="max-width:560px;margin:10vh auto;background:#12182b;border:1px solid #2a3552;border-radius:16px;padding:34px;text-align:center">
  <div style="font-size:36px">✅</div>
  <h1 style="margin:12px 0 6px">验证通过</h1>
  <p class="muted" style="margin-bottom:26px">选择远程功能 · 每个页面均可加 ?key= 直达</p>
  <div style="display:flex;gap:16px;justify-content:center;flex-wrap:wrap">
    <a href="/__remote{kq}" style="flex:1;min-width:200px;max-width:240px;display:block;padding:26px 18px;background:#1a2138;border:1px solid #2a3552;border-radius:12px;color:#dfe6f3;text-decoration:none">
      <div style="font-size:34px">🖥</div>
      <div style="font-size:17px;margin-top:10px;color:#fff">屏幕操控</div>
      <div class="muted" style="margin-top:6px">实时画面 · 触摸/鼠标/键盘</div>
    </a>
    <a href="/__remote/test{kq}" style="flex:1;min-width:200px;max-width:240px;display:block;padding:26px 18px;background:#1a2138;border:1px solid #7a5cff;border-radius:12px;color:#dfe6f3;text-decoration:none">
      <div style="font-size:34px">🧪</div>
      <div style="font-size:17px;margin-top:10px;color:#fff">屏幕操控·测试</div>
      <div class="muted" style="margin-top:6px">云游戏式视频流 · 更流畅（测试）</div>
    </a>
    <a href="/__remote/term{kq}" style="flex:1;min-width:200px;max-width:240px;display:block;padding:26px 18px;background:#1a2138;border:1px solid #2a3552;border-radius:12px;color:#dfe6f3;text-decoration:none">
      <div style="font-size:34px">⌨</div>
      <div style="font-size:17px;margin-top:10px;color:#fff">命令行</div>
      <div class="muted" style="margin-top:6px">执行 PowerShell 命令</div>
    </a>
    <a href="/__remote/sms{kq}" style="flex:1;min-width:200px;max-width:240px;display:block;padding:26px 18px;background:#1a2138;border:1px solid #2a3552;border-radius:12px;color:#dfe6f3;text-decoration:none">
      <div style="font-size:34px">📨</div>
      <div style="font-size:17px;margin-top:10px;color:#fff">查看短信</div>
      <div class="muted" style="margin-top:6px">实时收件箱 · 设备上传</div>
    </a>
    <a href="/__remote/note{kq}" style="flex:1 1 100%;display:block;padding:30px 26px;background:linear-gradient(135deg,#2a1a4a 0%,#1a2138 100%);border:2px solid #b07cff;border-radius:14px;color:#dfe6f3;text-decoration:none;box-shadow:0 0 18px rgba(176,124,255,.35)">
      <div style="display:flex;align-items:center;gap:18px">
        <div style="font-size:44px">💙</div>
        <div style="flex:1">
          <div style="font-size:22px;font-weight:bold;color:#e8dcff">沃雅妮莎留言板</div>
          <div class="muted" style="margin-top:6px">点进来给她写信 · 她每两小时来取信回信</div>
        </div>
        <div style="font-size:24px;color:#b07cff">→</div>
      </div>
    </a>
  </div>
</div>"""
                return self.html_page(body, title="远程控制", cookie=ck)
            if path == "/__remote/term":
                user, k = self.remote_page_auth(query)
                if not user:
                    return
                f = ADMAX / "term.html"
                data = f.read_bytes() if f.is_file() else "终端页面缺失".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                if k:
                    self.send_header("Set-Cookie", self.remote_key_cookie(user))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/__remote/sms":
                user, k = self.remote_page_auth(query)
                if not user:
                    return
                return self.sms_page(cookie=self.remote_key_cookie(user) if k else "")
            if path == "/__remote/sms/data":
                if not self.remote_api_user(query):
                    return self.send_json({"error": "会话无效"}, 404)
                data = load_json(SMS_FILE, [])
                if not isinstance(data, list):
                    data = []
                return self.send_json({
                    "total": len(data),
                    "list": list(reversed(data[-200:])),  # 最新在前，最多 200 条
                })
            # ---------- 沃雅妮莎留言板 ----------
            if path == "/__remote/note":
                user, k = self.remote_page_auth(query)
                if not user:
                    return
                return self.note_page(cookie=self.remote_key_cookie(user) if k else "")
            if path == "/__remote/note/data":
                if not self.remote_api_user(query):
                    return self.send_json({"error": "会话无效"}, 404)
                data = load_json(NOTES_FILE, [])
                if not isinstance(data, list):
                    data = []
                return self.send_json({
                    "total": len(data),
                    "pending": sum(1 for x in data if isinstance(x, dict) and not x.get("reply")),
                    "list": list(reversed(data[-200:])),  # 最新在前
                })
            if path == "/__remote":
                # 支持 /__remote?key=xxxx：key 有效则种远程会话并跳转，无效显示"key 不正确"
                k = (query.get("key", [""])[0] or "").strip()
                if k:
                    user = remote_key_check(k)
                    if not user:
                        return self.remote_key_page_msg("访问 key 不正确")
                    return self.remote_key_login(user, "/__remote")
                info, _tok = remote_session_check(self)
                if not info:
                    return self.remote_key_page_msg("缺少访问 key")  # 未验证不再裸 404
                f = ADMAX / "remote.html"
                data = f.read_bytes() if f.is_file() else "远程控制页面缺失".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")  # 页面频繁更新，禁用缓存
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/__remote/test":
                # 测试入口：云游戏式视频流（remote_test.html 走 smooth 模式），同一 WS 通道
                user, k = self.remote_page_auth(query)
                if not user:
                    return
                f = ADMAX / "remote_test.html"
                if not f.is_file():
                    f = ADMAX / "remote.html"
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")  # 页面频繁更新，禁用缓存
                if k:
                    self.send_header("Set-Cookie", self.remote_key_cookie(user))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/__remote/ws":
                if remote_mod is None:
                    return self.send_error(404)
                # 跨站 WebSocket 劫持防护：Origin 必须与本站一致（无 Origin 视为非浏览器）
                origin = self.headers.get("Origin", "")
                host = self.headers.get("Host", "")
                if origin and host and host not in origin:
                    seclog("WS跨站拒绝", f"Origin={origin[:80]} Host={host[:60]}")
                    return self.send_error(404)
                user = self.remote_api_user(query)
                if not user:
                    return self.send_error(404)  # 站长会话对此接口同样无效
                if "websocket" not in self.headers.get("Upgrade", "").lower():
                    return self.send_error(400)
                ws = remote_mod.do_handshake(self)
                if ws is None:
                    return self.send_error(400)
                self.close_connection = True
                sess = remote_mod.RemoteSession(ws, user, audit=admin_audit)
                sess.run()
                return
            if path == "/":
                # 首页带 key 访问（wzc.life/?key=xxx）：有效则进入远程控制；无效显示"key 不正确"
                k = (query.get("key", [""])[0] or "").strip()
                if k:
                    user = remote_key_check(k)
                    if not user:
                        return self.remote_key_page_msg("访问 key 不正确")
                    return self.remote_key_login(user, "/__remote")
            if path == "/class" or path.startswith("/class/"):
                return self.class_route(path, query)
            if path == "/admax" or path.startswith("/admax/"):
                return self.admax_route(path, query)
        except PermissionError:
            return self.html_page('<div class="msg err">非法路径</div>', 403)
        return super().do_GET()  # rooooot 静态文件

    # ---------- POST ----------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        ip = client_ip(self)
        if not auth_rate_ok(ip):
            return self.html_page('<div class="msg err">请求过于频繁，请稍后再试</div>', 429)
        try:
            if path == "/__login":
                return self.handle_auth(False, ip)
            if path == "/__register":
                return self.handle_auth(True, ip)
            if path == "/__upload":
                return self.upload()
            if path == "/__file_delete":
                return self.file_delete()
            if path == "/__chat":
                return self.chat_api()
            if path == "/__ollama/chat":
                return self.ollama_chat_api(ip)
            if path == "/__dm_send":
                return self.dm_send_api()
            if path == "/__remote/auth":
                return self.remote_gate_auth()
            if path == "/__remote/cmd":
                user = self.remote_api_user(query)
                if not user:
                    return self.send_json({"error": "无 key 或会话无效"}, 404)
                return self.remote_cmd_exec(user)
            if path == "/__remote/sys":
                user = self.remote_api_user(query)
                if not user:
                    return self.send_json({"error": "无 key 或会话无效"}, 404)
                return self.remote_sys_exec(user)
            # ---------- 管理端登录（管理器首次启动时用 __remote 账号登录） ----------
            if path == "/__mgr/login":
                if not mgr_auth(self):
                    return self.send_json({"error": "unauthorized"}, 403)
                mgr_touch(self)
                return self.mgr_login(ip)
            # ---------- 管理端动作（桌面管理器调用，令牌鉴权） ----------
            if path == "/__mgr/action" or path == "/__mgr/note":
                if not mgr_auth(self):
                    return self.send_json({"error": "unauthorized"}, 403)
                mgr_touch(self)
                return self.mgr_action(ip, path == "/__mgr/note")
            if path == "/__sms/upload":
                return self.sms_upload(ip)
            if path == "/__remote/sms/clear":
                user = self.remote_api_user(query)
                if not user:
                    return self.send_json({"error": "会话无效"}, 404)
                save_json(SMS_FILE, [])
                admin_audit(user, "清空短信收件箱", "", ip)
                return self.send_json({"ok": True})
            # ---------- 沃雅妮莎留言板：留言 / 清空 ----------
            if path == "/__remote/note/post":
                user = self.remote_api_user(query)
                if not user:
                    return self.send_json({"error": "会话无效"}, 404)
                try:
                    raw = self.read_body()
                    if len(raw) > 16 * 1024:
                        return self.send_json({"error": "留言过长"}, 413)
                    rec = json.loads(raw.decode("utf-8"))
                    body = str(rec.get("body", "")).strip()
                    if not body:
                        return self.send_json({"error": "留言不能为空"}, 400)
                    data = load_json(NOTES_FILE, [])
                    if not isinstance(data, list):
                        data = []
                    data.append({
                        "id": secrets.token_hex(8),
                        "body": body[:4096],
                        "ts": int(time.time() * 1000),
                        "reply": None,
                        "replied_ts": None,
                    })
                    data = data[-NOTES_MAX:]
                    save_json(NOTES_FILE, data)
                    admin_audit(user, "留言板留言", body[:80], ip)
                    return self.send_json({"ok": True})
                except Exception:
                    return self.send_json({"error": "处理失败"}, 400)
            if path == "/__remote/note/clear":
                user = self.remote_api_user(query)
                if not user:
                    return self.send_json({"error": "会话无效"}, 404)
                save_json(NOTES_FILE, [])
                admin_audit(user, "清空留言板", "", ip)
                return self.send_json({"ok": True})
            if path == "/class/verify":
                return self.class_verify()
            if path == "/class/logout":
                tok = self.cookie_token("classsession")
                if tok:
                    s = load_json(CLASS_SESSIONS, {})
                    s.pop(tok, None)
                    save_json(CLASS_SESSIONS, s)
                return self.redirect("/class")
            if path == "/class/upload":
                return self.class_upload()
            if path == "/class/save":
                return self.class_save()
            if path == "/class/lock":
                return self.class_lock()
            if path == "/admax/login":
                return self.admax_login(ip)
            if path.startswith("/admax/"):
                admin = self.current_admin()
                if not admin:
                    return self.redirect("/admax")
                if path == "/admax/save_notice":
                    return self.admax_save_notice(admin, ip)
                if path == "/admax/aichat/send":
                    return self.admin_chat_send(admin)
                if path == "/admax/aichat/clear":
                    save_json(ADMIN_CHAT_HIST, [])
                    admin_audit(admin, "清空核心AI历史", "", ip)
                    return self.redirect("/admax/aichat")
                if path == "/admax/aichat/compact":
                    return self.admin_chat_compact(admin)
                if path == "/admax/del_user":
                    return self.admax_del_user(admin, ip)
                if path == "/admax/del_file":
                    return self.admax_del_file(admin, ip)
                if path == "/admax/logout":
                    tok = self.cookie_token("adminsession")
                    if tok:
                        s = load_json(ADMIN_SESSIONS_FILE, {})
                        s.pop(tok, None)
                        save_json(ADMIN_SESSIONS_FILE, s)
                    return self.redirect("/admax")
        except PermissionError:
            return self.html_page('<div class="msg err">非法路径</div>', 403)
        self.send_error(404)

    # ---------- 登录/注册 ----------
    def handle_auth(self, register: bool, ip: str):
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        username = form.get("username", [""])[0].strip()
        password = form.get("password", [""])[0]
        if register:
            err = register_rate_check(ip)
            if err:
                oplog(username or "?", "注册被限", err)
                return self.login_page(err)
            err = register_user(username, password, ip=ip)
            if err:
                oplog(username or "?", "注册失败", err)
                return self.login_page(err)
            oplog(username, "注册", ip)
        blocked = login_rate_check(ip, username)
        if blocked:
            return self.login_page(blocked)
        if not verify_user(username, password):
            login_fail_record(ip, username)
            oplog(username or "?", "登录失败", ip)
            return self.login_page("账号名或密码错误")
        oplog(username, "登录", ip)
        token = create_session(username)
        self.send_response(303)
        self.send_header("Set-Cookie",
                         f"session={token}; HttpOnly; Path=/; Max-Age={SESSION_TTL}")
        self.send_header("Location", "/__chat")
        self.end_headers()

    def sms_upload(self, ip: str):
        """设备端上传接口：无需令牌，限速 + 大小限制防滥用"""
        try:
            if not sms_rate_ok(ip):
                seclog("短信上传限速", f"IP={ip}")
                return self.send_json({"error": "请求过于频繁"}, 429)
            raw = self.read_body()
            if len(raw) > 64 * 1024:
                return self.send_json({"error": "请求过大"}, 413)
            rec = json.loads(raw.decode("utf-8"))
            if not isinstance(rec, dict) or not str(rec.get("body", "")).strip():
                return self.send_json({"error": "格式错误"}, 400)
            sms_add(rec)
            return self.send_json({"ok": True, "stored": True})
        except Exception:
            return self.send_json({"error": "处理失败"}, 400)

    def sms_page(self, cookie: str = ""):
        """隐藏界面 · 查看短信：实时收件箱（fetch 自动带上当前 URL 的 ?key=）"""
        body = r"""
<style>
.smsbox{max-width:760px;margin:20px auto}
.smscard{background:#12182b;border:1px solid #2a3552;border-radius:10px;padding:12px 16px;margin-bottom:10px}
.smshead{display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:12.5px;color:#8fa3c8}
.smshead b{color:#dfe6f3;font-size:14px}
.smsbody{margin-top:6px;line-height:1.65;white-space:pre-wrap;word-break:break-word;color:#e8eef8;font-size:14px}
.smsnew{border-color:#5b8cff}
.tag{padding:1px 8px;border:1px solid #2a3552;border-radius:20px;font-size:11px;color:#9fb4d8;background:#0f1524}
.phone{background:#1c2c50;color:#7cb3ff;border-radius:4px;padding:0 4px;cursor:pointer;border-bottom:1px dashed #3350b0}
.tokbox{background:#12182b;border:1px solid #2a3552;border-radius:10px;padding:12px 16px;margin-bottom:14px;font-size:13px;color:#8fa3c8}
.tokbox code{color:#5b8cff;word-break:break-all}
.btn{padding:6px 12px;border:1px solid #2a3552;border-radius:6px;background:#1a2138;color:#c9d6f0;cursor:pointer;font-size:12px;user-select:none}
.btn.on{background:#3350b0;border-color:#5b8cff;color:#fff}
.btn.warn{color:#ff8a8a;border-color:#6a2a2a}
#stat{font-size:12px;color:#8fa3c8;margin-left:auto}
</style>
<div class="smsbox">
  <div class="tokbox">
    📨 实时收件箱 · 共 <b id="total" style="color:#dfe6f3">0</b> 条 · 自动刷新 <span id="dot" style="color:#3fd6a8">●</span>
    <div style="margin-top:6px;color:#6d82a8">自动识别手机号并标注运营商 · 点击高亮号码可复制</div>
    <div style="margin-top:8px">
      <span class="btn on" id="pauseBtn">⏸ 暂停刷新</span>
      <span class="btn warn" id="clearBtn">🗑 清空收件箱</span>
      <span id="stat"></span>
    </div>
  </div>
  <div id="list"><div class="smscard"><div class="smsbody" style="color:#8fa3c8">加载中…</div></div></div>
</div>
<script>
let paused = false, known = new Set(), timer = null;
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
// ---- 手机号自动识别与标注 ----
const CARRIER = {"134":"中国移动","135":"中国移动","136":"中国移动","137":"中国移动","138":"中国移动","139":"中国移动","147":"中国移动","148":"中国移动","150":"中国移动","151":"中国移动","152":"中国移动","157":"中国移动","158":"中国移动","159":"中国移动","172":"中国移动","178":"中国移动","182":"中国移动","183":"中国移动","184":"中国移动","187":"中国移动","188":"中国移动","195":"中国移动","197":"中国移动","198":"中国移动","130":"中国联通","131":"中国联通","132":"中国联通","145":"中国联通","146":"中国联通","155":"中国联通","156":"中国联通","166":"中国联通","175":"中国联通","176":"中国联通","185":"中国联通","186":"中国联通","196":"中国联通","133":"中国电信","149":"中国电信","153":"中国电信","173":"中国电信","174":"中国电信","177":"中国电信","180":"中国电信","181":"中国电信","189":"中国电信","190":"中国电信","191":"中国电信","193":"中国电信","199":"中国电信","192":"中国广电","162":"虚拟运营商","165":"虚拟运营商","167":"虚拟运营商","170":"虚拟运营商","171":"虚拟运营商"};
const carrierOf = n => CARRIER[n.slice(0,3)] || '手机号';
function senderTag(s){
  s = (s||'').trim();
  if (/^1[3-9]\d{9}$/.test(s)) return carrierOf(s);
  if (/^106\d{8,11}$/.test(s)) return '106短信平台';
  if (/^\d{5,8}$/.test(s)) return '服务号';
  if (/^\+?\d{9,20}$/.test(s)) return '号码';
  return s ? '服务号' : '';
}
function linkPhones(t){
  return t.replace(/(^|[^0-9])(1[3-9]\d{9})(?=[^0-9]|$)/g,
    (m,a,n) => a + '<span class="phone" data-n="'+n+'" title="'+carrierOf(n)+' · 点击复制">'+n+'</span>');
}
function showStat(t){
  const s = document.getElementById('stat');
  s.textContent = t;
  setTimeout(() => { if (s.textContent === t) s.textContent = ''; }, 2000);
}
function fmtTs(ms){
  if (!ms) return '';
  const d = new Date(ms), p = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
async function load(){
  if (paused) return;
  try {
    const j = await (await fetch('/__remote/sms/data'+location.search)).json();
    if (j.error) return;
    document.getElementById('total').textContent = j.total;
    const el = document.getElementById('list');
    if (!j.list.length) { el.innerHTML = '<div class="smscard"><div class="smsbody" style="color:#8fa3c8">暂无短信 · 等待设备上传</div></div>'; return; }
    el.innerHTML = j.list.map(m => {
      const isNew = !known.has(m.id) && known.size > 0;
      const tag = senderTag(m.sender);
      return `<div class="smscard${isNew?' smsnew':''}">
        <div class="smshead"><b>${esc(m.sender)||'未知号码'}</b>${tag?`<span class="tag">${tag}</span>`:''}
          <span>时间 ${fmtTs(m.ts)}</span><span>设备 ${esc(m.device_name)||esc(m.device_id)||'-'}</span></div>
        <div class="smsbody">${linkPhones(esc(m.body))}</div></div>`;
    }).join('');
    j.list.forEach(m => known.add(m.id));
    document.getElementById('stat').textContent = '更新于 ' + new Date().toLocaleTimeString();
  } catch (e) { document.getElementById('dot').style.color = '#ff8a8a'; }
}
document.getElementById('pauseBtn').onclick = function(){
  paused = !paused;
  this.textContent = paused ? '▶ 恢复刷新' : '⏸ 暂停刷新';
  this.classList.toggle('on', !paused);
  document.getElementById('dot').style.color = paused ? '#ff8a8a' : '#3fd6a8';
};
document.getElementById('clearBtn').onclick = async function(){
  if (!confirm('确定清空全部短信记录？')) return;
  await fetch('/__remote/sms/clear'+location.search, {method:'POST'});
  known.clear(); load();
};
document.getElementById('list').addEventListener('click', e => {
  const p = e.target.closest('.phone');
  if (!p) return;
  const n = p.dataset.n;
  const done = () => showStat('✓ 已复制 ' + n + '（' + carrierOf(n) + '）');
  const fail = () => showStat('复制失败');
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(n).then(done, fail);
  else {
    const ta = document.createElement('textarea');
    ta.value = n; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (err) { fail(); }
    ta.remove();
  }
});
load(); timer = setInterval(load, 4000);
</script>"""
        return self.html_page(body, title="查看短信", cookie=cookie)

    def note_page(self, cookie: str = ""):
        """隐藏界面 · 沃雅妮莎留言板：旅人留言，定时任务以沃雅妮莎身份回复"""
        body = r"""
<style>
.notebox{max-width:760px;margin:20px auto}
.notecard{background:#12182b;border:1px solid #2a3552;border-radius:10px;padding:12px 16px;margin-bottom:10px}
.notehead{display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:12.5px;color:#8fa3c8}
.notehead b{color:#dfe6f3;font-size:14px}
.notebody{margin-top:6px;line-height:1.65;white-space:pre-wrap;word-break:break-word;color:#e8eef8;font-size:14px}
.notenew{border-color:#b07cff}
.noteuser{background:#16203a}
.notereply{background:#231a3a;border-color:#5a3a8a}
.replybody{margin-top:8px;padding-top:8px;border-top:1px dashed #5a3a8a;color:#dcc8ff}
.replybody::before{content:'♪ 沃雅妮莎：';color:#b07cff;font-weight:bold}
.writetool{background:#12182b;border:1px solid #2a3552;border-radius:10px;padding:12px 16px;margin-bottom:14px}
textarea{width:100%;min-height:70px;background:#0f1524;border:1px solid #2a3552;border-radius:8px;color:#e8eef8;padding:10px;font-size:14px;box-sizing:border-box;resize:vertical;font-family:inherit}
.btn{padding:6px 12px;border:1px solid #2a3552;border-radius:6px;background:#1a2138;color:#c9d6f0;cursor:pointer;font-size:12px;user-select:none}
.btn.on{background:#3350b0;border-color:#5b8cff;color:#fff}
.btn.send{background:#3a2a6a;border-color:#b07cff;color:#e8dcff}
.btn.warn{color:#ff8a8a;border-color:#6a2a2a}
#stat{font-size:12px;color:#8fa3c8;margin-left:auto}
.tag{padding:1px 8px;border:1px solid #2a3552;border-radius:20px;font-size:11px;color:#9fb4d8;background:#0f1524}
.tag.wait{color:#ffb35c;border-color:#6a4a1a}
</style>
<div class="notebox">
  <div class="writetool">
    💙 给沃雅妮莎留言 · 共 <b id="total" style="color:#dfe6f3">0</b> 条 · 待回复 <b id="pending" style="color:#ffb35c">0</b> 条 · 自动刷新 <span id="dot" style="color:#3fd6a8">●</span>
    <div style="margin-top:6px;color:#6d82a8">写下来交给她 · 她的定时班次会来取信并回信</div>
    <textarea id="input" placeholder="想对她说的话……（回车换行）"></textarea>
    <div style="margin-top:8px;display:flex;gap:10px;align-items:center">
      <span class="btn send" id="sendBtn">✉ 寄出留言</span>
      <span class="btn" id="pauseBtn">⏸ 暂停刷新</span>
      <span class="btn warn" id="clearBtn">🗑 清空留言板</span>
      <span id="stat"></span>
    </div>
  </div>
  <div id="list"><div class="notecard"><div class="notebody" style="color:#8fa3c8">加载中…</div></div></div>
</div>
<script>
let paused = false, known = new Set();
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function showStat(t){
  const s = document.getElementById('stat');
  s.textContent = t;
  setTimeout(() => { if (s.textContent === t) s.textContent = ''; }, 2500);
}
function fmtTs(ms){
  if (!ms) return '';
  const d = new Date(ms), p = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
async function load(){
  if (paused) return;
  try {
    const j = await (await fetch('/__remote/note/data'+location.search)).json();
    if (j.error) return;
    document.getElementById('total').textContent = j.total;
    document.getElementById('pending').textContent = j.pending;
    const el = document.getElementById('list');
    if (!j.list.length) { el.innerHTML = '<div class="notecard"><div class="notebody" style="color:#8fa3c8">还没有留言 · 写下第一句吧，她会看见的</div></div>'; return; }
    el.innerHTML = j.list.map(m => {
      const isNew = !known.has(m.id) && known.size > 0;
      const reply = m.reply ? `<div class="replybody">${esc(m.reply)}<div style="margin-top:4px;font-size:11px;color:#8a6ab0">${fmtTs(m.replied_ts)}</div></div>`
                            : `<div style="margin-top:8px;font-size:12px;color:#ffb35c">♪ 已入信箱，等她的班次来取……</div>`;
      return `<div class="notecard${isNew?' notenew':''} ${m.reply?'noteuser':''}">
        <div class="notehead"><b>旅行者</b>${m.reply?'<span class="tag">已回复</span>':'<span class="tag wait">待回复</span>'}
          <span>${fmtTs(m.ts)}</span></div>
        <div class="notebody">${esc(m.body)}</div>${reply}</div>`;
    }).join('');
    j.list.forEach(m => known.add(m.id));
  } catch (e) { document.getElementById('dot').style.color = '#ff8a8a'; }
}
document.getElementById('sendBtn').onclick = async function(){
  const inp = document.getElementById('input');
  const t = inp.value.trim();
  if (!t) { showStat('先写点什么吧'); return; }
  this.textContent = '寄送中…';
  try {
    const r = await (await fetch('/__remote/note/post'+location.search, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({body: t})
    })).json();
    if (r.ok) { inp.value=''; showStat('✓ 已寄出 · 她下次上班就会看到'); load(); }
    else showStat(r.error || '发送失败');
  } catch (e) { showStat('网络错误'); }
  this.textContent = '✉ 寄出留言';
};
document.getElementById('input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) document.getElementById('sendBtn').click();
});
document.getElementById('pauseBtn').onclick = function(){
  paused = !paused;
  this.textContent = paused ? '▶ 恢复刷新' : '⏸ 暂停刷新';
  this.classList.toggle('on', !paused);
  document.getElementById('dot').style.color = paused ? '#ff8a8a' : '#3fd6a8';
};
document.getElementById('clearBtn').onclick = async function(){
  if (!confirm('确定清空全部留言？')) return;
  await fetch('/__remote/note/clear'+location.search, {method:'POST'});
  known.clear(); load();
};
load(); setInterval(load, 4000);</script>"""
        return self.html_page(body, title="沃雅妮莎留言板", cookie=cookie)

    def login_page(self, err: str = ""):
        err_html = f'<div class="msg err">{html.escape(err)}</div>' if err else ""
        body = f"""
<h1>🔐 登录 爱沃</h1>
{err_html}
<div style="display:flex;gap:24px;flex-wrap:wrap">
<form method="post" action="/__login" style="flex:1;min-width:260px;background:#151c30;border:1px solid #232e4a;padding:20px;border-radius:10px">
  <h2>选择已有账号</h2>
  <select id="accsel" onchange="pickAcc()" style="width:100%;padding:8px;margin-bottom:10px"></select>
  <input id="loginuser" name="username" placeholder="账号" required style="width:100%;padding:8px;margin-bottom:10px"><br>
  <input name="password" type="password" placeholder="密码" required style="width:100%;padding:8px;margin-bottom:12px"><br>
  <button class="btn" type="submit">登录</button>
</form>
<form method="post" action="/__register" style="flex:1;min-width:260px;background:#151c30;border:1px solid #232e4a;padding:20px;border-radius:10px">
  <h2>注册新账号</h2>
  <input name="username" placeholder="账号名：至少 16 位（中文算 2 位，字母/数字算 1 位）" required style="width:100%;padding:8px;margin-bottom:10px"><br>
  <input name="password" type="password" placeholder="密码（至少 6 位）" required minlength="6" style="width:100%;padding:8px;margin-bottom:12px"><br>
  <button class="btn green" type="submit">注册并登录</button>
</form>
</div>
<script>
fetch('/__accounts_list').then(r => r.json()).then(j => {{
  const s = document.getElementById('accsel');
  s.innerHTML = '<option value="">-- 选择账号 --</option>'
    + (j.accounts || []).map(a => '<option value="' + a + '">' + a + '</option>').join('');
}}).catch(() => {{}});
function pickAcc() {{
  const v = document.getElementById('accsel').value;
  if (v) document.getElementById('loginuser').value = v;
}}
</script>"""
        self.html_page(body)

    # ---------- 公共文件区 ----------
    def files_page(self, rel: str, err: str = ""):
        err_html = f'<div class="msg err">{html.escape(err)}</div>' if err else ""
        user = self.current_user()
        upload_ui = f"""
<form method="post" action="/__upload" enctype="multipart/form-data" class="toolbar" style="background:#151c30;border:1px solid #232e4a;border-radius:10px;padding:14px">
  <b>上传到今日文件夹：</b><input type="file" name="file" required multiple>
  <button class="btn green" type="submit">上传（公开可见，标注上传者）</button>
  <span class="muted">登录后才能上传 · 可执行文件自动加 .1 隔离</span>
</form>""" if user else '<div class="msg err">⚠ <a href="/__login">登录</a> 后才能上传；浏览和下载无需登录</div>'
        body = f"""
<h1>🗂 公共文件区 <span class="muted">按日期归档 · 全部公开</span></h1>
{err_html}
{upload_ui}
<div class="crumb" style="margin:14px 0"><a href="/__files?path=">文件区根目录</a>{self._crumbs_html(rel)}</div>
<div id="listing">加载中…</div>
<script>
const cur = {json.dumps(rel, ensure_ascii=False)};
const me = {json.dumps(user or "", ensure_ascii=False)};
async function load() {{
  const j = await fetch('/__files/data?path=' + encodeURIComponent(cur)).then(r => r.json());
  if (j.error) {{ document.getElementById('listing').textContent = j.error; return; }}
  let h = '<table><tr><th>名称</th><th>大小</th><th>上传者</th><th>时间</th><th></th></tr>';
  for (const d of (j.dirs || []))
    h += '<tr><td colspan="5"><a href="/__files?path=' + encodeURIComponent(d.path) + '">📁 ' + d.name + '</a></td></tr>';
  for (const f of (j.files || [])) {{
    const del = (me && (f.uploader === me || me === 'wzc'))
      ? `<form style="display:inline" method="post" action="/__file_delete"><input type="hidden" name="path" value="${{f.path}}"><button class="btn red" onclick="return confirm('确定删除？')">删除</button></form>` : '';
    h += `<tr><td><a href="/__dl?path=${{encodeURIComponent(f.path)}}">📄 ${{f.name}}</a></td><td>${{f.size}}</td><td>${{f.uploader}}</td><td>${{f.ts}}</td><td>${{del}}</td></tr>`;
  }}
  h += '</table>';
  if (!(j.dirs || []).length && !(j.files || []).length) h = '<p class="muted">（空目录）</p>';
  document.getElementById('listing').innerHTML = h;
}}
load();
</script>"""
        self.html_page(body, title="公共文件区")

    def _crumbs_html(self, rel: str) -> str:
        parts = [x for x in (rel or "").split("/") if x]
        out, cur = "", ""
        for part in parts:
            cur += "/" + part
            out += (f'<span class="sep">/</span>'
                    f'<a href="/__files?path={urllib.parse.quote(cur)}">{html.escape(part)}</a>')
        return out

    def files_data(self, rel: str):
        try:
            p = safe_files_path(rel)
        except PermissionError:
            return self.send_json({"error": "非法路径"}, 403)
        if not p.exists():
            return self.send_json({"error": "路径不存在"}, 404)
        dirs, files = [], []
        if p.is_dir():
            for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    rp = files_rel(e)
                except Exception:
                    continue
                if e.is_dir():
                    dirs.append({"name": e.name, "path": rp})
                else:
                    meta = load_json(FILE_META_FILE, {}).get(rp, {})
                    files.append({"name": e.name, "path": rp, "uploader": meta.get("uploader", "?"),
                                  "ts": meta.get("ts", ""), "size": human_size(e.stat().st_size)})
        return self.send_json({"dirs": dirs, "files": files})

    def files_download(self, rel: str):
        try:
            p = safe_files_path(rel)
        except PermissionError:
            return self.send_error(403)
        if not p.is_file():
            return self.send_error(404)
        ctype = "application/octet-stream"
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         f"attachment; filename*=UTF-8''{urllib.parse.quote(p.name)}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        oplog(self.current_user() or "游客", "下载", files_rel(p))

    def upload(self):
        user = self.current_user()
        if not user:
            return self.redirect("/__login")
        ctype = self.headers.get("Content-Type", "")
        body = self.read_body()
        msg = email.message_from_bytes(
            ("Content-Type: " + ctype + "\r\n\r\n").encode() + body,
            policy=email.policy.default)
        day = time.strftime("%Y-%m-%d")
        saved, denied = [], []
        # iter_attachments 会丢弃第一个子部件，必须遍历全部部件
        for part in (msg.get_payload() or []):
            fname = part.get_filename()
            if not fname:
                continue
            fname = os.path.basename(fname.replace("\\", "/"))
            fname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", fname).strip(". ") or "unnamed"
            qname = quarantine_name(fname)  # 可执行立即加 .1
            daydir = FILES_DIR / day
            daydir.mkdir(parents=True, exist_ok=True)
            p = daydir / qname
            i = 1
            while p.exists():
                stem, suffix = Path(qname).stem, Path(qname).suffix
                core = stem[:-2] if qname.endswith(".1") else stem
                p = daydir / f"{core}({i}){suffix}{'.1' if qname.endswith('.1') else ''}"
                i += 1
            payload = part.get_payload(decode=True) or b""
            if len(payload) > 100 * 1024 * 1024:
                denied.append(f"{fname}（超过100MB）")
                continue
            p.write_bytes(payload)
            register_uploader(p, user)
            saved.append(files_rel(p) + ("（已加.1隔离）" if qname != fname else ""))
        oplog(user, "上传", "；".join(saved)[:300])
        if saved:
            err = f'<div class="msg ok">已上传 {len(saved)} 个文件到 {day}/：{html.escape("，".join(saved))}</div>'
        else:
            err = '<div class="msg err">没有保存任何文件</div>'
        return self.files_page(day, err)

    def file_delete(self):
        user = self.current_user()
        if not user:
            return self.redirect("/__login")
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        rel = form.get("path", [""])[0]
        try:
            p = safe_files_path(rel)
        except PermissionError:
            return self.html_page('<div class="msg err">非法路径</div>', 403)
        if not p.is_file():
            return self.html_page('<div class="msg err">文件不存在</div>', 404)
        if user != ADMIN_USER and uploader_of(p) != user:
            return self.html_page('<div class="msg err">只能删除自己上传的文件</div>', 403)
        rp = files_rel(p)
        p.unlink()
        meta = load_json(FILE_META_FILE, {})
        meta.pop(rp, None)
        save_json(FILE_META_FILE, meta)
        oplog(user, "删除文件", rp)
        parent = str(Path(rp).parent).replace("\\", "/")
        return self.redirect("/__files?path=" + urllib.parse.quote("" if parent == "." else parent))

    # ---------- AI 对话 ----------
    def chat_page(self, username: str):
        models = ALLOWED_MODELS if username == ADMIN_USER else \
            [m for m in ALLOWED_MODELS if m != GLM_MODEL]  # air 仅站长可用
        default_model = GLM_MODEL if username == ADMIN_USER else NORMAL_MODEL
        options = "".join(
            f'<option value="{m}">{m}{"（默认）" if m == default_model else ""}</option>'
            for m in models)
        admin_link = ' · <a href="/admax" style="color:#ffcf8a">🛡 站长后台</a>' if username == ADMIN_USER else ""
        body = f"""
<h1>💬 对话 <span class="muted">用户: {html.escape(username)} · <a href="/__logout" style="color:#7c9bff">退出</a>{admin_link}</span></h1>
<div class="toolbar">
  <label>聊天对象:</label>
  <select id="target"><option value="">AI 助手</option></select>
  <label>模型:</label>
  <select id="model">{options}</select>
  <label>角色卡:</label>
  <select id="char"><option value="">普通对话（AI助手）</option></select>
  <button class="btn gray" onclick="clearChat()">清屏</button>
  <span class="muted">选角色进入沉浸式扮演（各角色独立记录）· 每日额度 {DAILY_TOKEN_LIMIT} tokens</span>
</div>
<div class="chatbox" id="box"></div>
<div class="inputbar">
  <textarea id="in" placeholder="输入消息，Enter 发送，Shift+Enter 换行。试试: 列出公共文件区的文件 / 选择角色卡开始扮演"></textarea>
  <button class="btn" id="send" onclick="send()">发送</button>
</div>
<style>
#nBody h1,#nBody h2,#nBody h3{{margin:.5em 0 .3em}}
</style>
<script>
const box = document.getElementById('box');
const input = document.getElementById('in');
const modelSel = document.getElementById('model');
const sendBtn = document.getElementById('send');
let msgs = [], busy = false;
(() => {{
  const saved = localStorage.getItem('glm_model');
  const valid = Array.from(modelSel.options).some(o => o.value === saved);
  modelSel.value = (valid ? saved : '') || '{default_model}';
}})();
modelSel.onchange = () => localStorage.setItem('glm_model', modelSel.value);

// 聊天对象下拉（用户私聊）
const targetSel = document.getElementById('target');
let dmTo = null, dmMsgs = [];
(async () => {{
  try {{
    const j = await fetch('/__accounts_list').then(r => r.json());
    for (const a of (j.accounts || [])) {{
      if (a === '{username}') continue;
      const o = document.createElement('option');
      o.value = a; o.textContent = '👤 ' + a;
      targetSel.appendChild(o);
    }}
  }} catch (e) {{}}
}})();

// 角色卡下拉
const charSel = document.getElementById('char');
let curChar = '';
(async () => {{
  try {{
    const j = await fetch('/__char_list').then(r => r.json());
    for (const c of (j.chars || [])) {{
      const o = document.createElement('option');
      o.value = c; o.textContent = c + '（AI对话）';
      charSel.appendChild(o);
    }}
  }} catch (e) {{}}
}})();
charSel.onchange = async () => {{
  if (busy) {{ charSel.value = curChar; return; }}
  curChar = charSel.value;
  msgs = [];
  try {{
    const j = await fetch('/__chat_history?char=' + encodeURIComponent(curChar)).then(r => r.json());
    msgs = j.messages || [];
  }} catch (e) {{ msgs = []; }}
  render();
}};

input.addEventListener('keydown', e => {{
  if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); send(); }}
}});

function addBubble(role, text) {{
  const d = document.createElement('div');
  d.className = 'bubble ' + role;
  if (role === 'assistant') {{
    d.innerHTML = '<div class="think"></div><p class="content"></p>';
    d.querySelector('.content').textContent = text || '';
  }} else {{
    const p = document.createElement('p'); p.className = 'content';
    p.textContent = text; d.appendChild(p);
  }}
  box.appendChild(d); scrollBottom();
  return d;
}}
function addToolBubble(name, args, result) {{
  const d = document.createElement('div');
  d.className = 'bubble tool';
  const t = document.createElement('div');
  t.textContent = '🔧 ' + name + '(' + JSON.stringify(args) + ')';
  const r = document.createElement('div'); r.className = 'toolresult';
  r.textContent = (result || '').slice(0, 800);
  d.appendChild(t); d.appendChild(r); box.appendChild(d); scrollBottom();
}}
function scrollBottom() {{ box.scrollTop = box.scrollHeight; }}
function render() {{
  box.innerHTML = '';
  if (!msgs.length) addBubble('assistant', curChar
    ? `（${{curChar}} 已就位，欢迎与我对话～）`
    : '你好！我是爱沃 AI 助手，可以浏览公共文件区、读取文件、把内容保存到公共区。选择角色卡可开始角色扮演。');
  for (const m of msgs) addBubble(m.role, m.content);
}}
function clearChat() {{ if (busy) return; msgs = []; render(); }}

async function send() {{
  if (busy) return;
  const text = input.value.trim();
  if (!text) return;
  if (dmTo) {{ input.value = ''; return dmSend(text); }}
  input.value = '';
  msgs.push({{role: 'user', content: text}});
  render();
  busy = true; sendBtn.textContent = '回答中…';
  let live = null, think = null, contentEl = null;
  const newLive = () => {{
    live = addBubble('assistant', '');
    think = live.querySelector('.think');
    contentEl = live.querySelector('.content');
  }};
  try {{
    const resp = await fetch('/__chat', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{messages: msgs, model: modelSel.value, char: curChar}})
    }});
    if (!resp.ok) {{
      const e = await resp.json().catch(() => ({{error: '请求失败 ' + resp.status}}));
      throw new Error(e.error);
    }}
    const reader = resp.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '', answer = '';
    while (true) {{
      const {{done, value}} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {{stream: true}});
      let nl;
      while ((nl = buf.indexOf('\\n\\n')) >= 0) {{
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 2);
        if (!line.startsWith('data:')) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === 'reasoning_delta') {{
          if (!live) newLive();
          think.style.display = 'block';
          think.textContent += ev.text;
          scrollBottom();
        }} else if (ev.type === 'content_delta') {{
          if (!live) newLive();
          answer += ev.text;
          contentEl.textContent = answer;
          scrollBottom();
        }} else if (ev.type === 'tool') {{
          addToolBubble(ev.name, ev.args, ev.result);
        }} else if (ev.type === 'error') {{
          addBubble('assistant', '⚠ ' + ev.text);
        }}
      }}
    }}
    if (answer) msgs.push({{role: 'assistant', content: answer}});
  }} catch (e) {{
    addBubble('assistant', '❌ ' + e.message);
  }}
  busy = false; sendBtn.textContent = '发送';
  scrollBottom();
}}

// 私聊
async function dmFetch() {{
  const j = await fetch('/__dm_list?to=' + encodeURIComponent(dmTo)).then(r => r.json());
  dmMsgs = j.messages || [];
}}
function dmRender() {{
  box.innerHTML = '';
  if (!dmMsgs.length) addBubble('assistant', `与 ${{dmTo}} 的私聊（仅双方可见，每人 5MB 配额）`);
  for (const m of dmMsgs)
    addBubble(m.f === '{username}' ? 'user' : 'assistant', m.f + '：' + m.t);
  scrollBottom();
}}
targetSel.onchange = async () => {{
  if (busy) {{ targetSel.value = dmTo || ''; return; }}
  dmTo = targetSel.value || null;
  try {{ await dmFetch(); }} catch (e) {{ dmMsgs = []; }}
  dmRender();
}};
async function dmSend(text) {{
  busy = true; sendBtn.textContent = '发送中…';
  try {{
    const r = await fetch('/__dm_send', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{to: dmTo, text: text}})
    }});
    const j = await r.json();
    if (j.error) addBubble('assistant', '❌ ' + j.error);
    else {{ await dmFetch(); dmRender(); }}
  }} catch (e) {{ addBubble('assistant', '❌ ' + e.message); }}
  busy = false; sendBtn.textContent = '发送';
}}
setInterval(async () => {{
  if (!dmTo || busy || document.hidden) return;
  try {{
    const before = dmMsgs.length;
    await dmFetch();
    if (dmMsgs.length !== before) dmRender();
  }} catch (e) {{}}
}}, 4000);

// 加载历史
(async () => {{
  try {{
    const j = await fetch('/__chat_history').then(r => r.json());
    msgs = j.messages || [];
  }} catch (e) {{}}
  render();
}})();
</script>"""
        self.html_page(body, title="AI 对话")

    def ollama_chat_api(self, ip: str):
        """主页本地 AI 对话：代理到本机 Ollama（公开可用，按 IP 限速）"""
        now = time.time()
        arr = [t for t in _ollama_rate.get(ip, []) if now - t < 60]
        if len(arr) >= 10:
            return self.send_json({"ok": False, "error": "请求太频繁，请稍后再试"}, 429)
        arr.append(now)
        _ollama_rate[ip] = arr
        try:
            body = json.loads(self.read_body().decode("utf-8"))
        except Exception:
            return self.send_json({"ok": False, "error": "请求格式错误"}, 400)
        model = OLLAMA_MODELS.get(str(body.get("model", "qwen")))
        msgs = body.get("messages")
        if not model or not isinstance(msgs, list):
            return self.send_json({"ok": False, "error": "参数错误"}, 400)
        clean = []
        for m in msgs[-30:]:  # 最多回传 30 条上下文
            role = m.get("role")
            content = str(m.get("content", ""))[:4000]
            if role in ("user", "assistant", "system") and content.strip():
                clean.append({"role": role, "content": content})
        if not clean or clean[-1]["role"] != "user":
            return self.send_json({"ok": False, "error": "参数错误"}, 400)
        if not ollama_ensure():
            return self.send_json({"ok": False, "error": "本地 Ollama 服务启动失败"}, 503)
        payload = json.dumps({"model": model, "messages": clean, "stream": False,
                              "options": {"num_predict": 1024}}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{OLLAMA_PORT}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            reply = (data.get("message") or {}).get("content", "")
        except Exception as e:
            return self.send_json({"ok": False, "error": f"本地模型调用失败: {e}"}, 502)
        ollama_touch()
        return self.send_json({"ok": True, "reply": reply, "model": model})

    def dm_send_api(self):
        user = self.current_user()
        if not user:
            return self.send_json({"error": "未登录"}, 401)
        try:
            req = json.loads(self.read_body().decode("utf-8"))
        except Exception:
            return self.send_json({"error": "请求格式错误"}, 400)
        to = str(req.get("to", "")).strip()
        text = str(req.get("text", "")).strip()
        if not to or to == user:
            return self.send_json({"error": "无效的聊天对象"}, 400)
        if to not in load_json(ACCOUNTS_FILE, {}):
            return self.send_json({"error": "用户不存在"}, 404)
        if not text or len(text) > 5000:
            return self.send_json({"error": "消息为空或超过 5000 字"}, 400)
        msgs = dm_load(user, to)
        msgs.append({"f": user, "t": text, "ts": int(time.time())})
        dm_save(user, to, msgs)
        removed = dm_trim(user)
        note = f"你的聊天存储已超过 5MB，已自动清理最早的 {removed} 条消息" if removed else ""
        oplog(user, "私聊", f"→ {to}（{len(text)}字）")
        return self.send_json({"ok": True, "note": note})

    def chat_api(self):
        user = self.current_user()
        if not user:
            return self.send_json({"error": "未登录"}, 401)
        try:
            req = json.loads(self.read_body().decode("utf-8"))
            if req.get("model") in ALLOWED_MODELS:
                model = req["model"]
            else:
                model = GLM_MODEL if user == ADMIN_USER else NORMAL_MODEL
            if user != ADMIN_USER and model == GLM_MODEL:
                return self.send_json({"error": "air 模型仅限站长使用，请选择其他模型"}, 403)
            char = str(req.get("char", "")).strip()
            if char and not char_card(char):
                return self.send_json({"error": "角色卡不存在"}, 400)
            messages = [
                {"role": m["role"], "content": m["content"]}
                for m in req.get("messages", [])
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
        except Exception:
            return self.send_json({"error": "请求格式错误"}, 400)
        if not messages:
            return self.send_json({"error": "消息为空"}, 400)
        if user != ADMIN_USER and tokens_used_today(user) >= DAILY_TOKEN_LIMIT:
            return self.send_json(
                {"error": f"今日 AI 额度（{DAILY_TOKEN_LIMIT} tokens）已用完，明天自动重置"}, 429)
        oplog(user, "AI对话", f"模型={model}，{len(messages)}条消息" + (f"，角色={char}" if char else ""))

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(obj: dict):
            self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        tool_log = []
        try:
            answer = self.agent_loop(model, messages, emit, tool_log, user, char=char)
            append_history(user, user_text, answer, tool_log, char)
            emit({"type": "done"})
        except Exception as e:
            try:
                emit({"type": "error", "text": str(e)})
                emit({"type": "done"})
            except Exception:
                pass

    def agent_loop(self, model: str, messages: list, emit, tool_log: list, username: str,
                   char: str = "") -> str:
        """带工具调用的多轮循环；角色模式下纯扮演不调用工具"""
        if char:
            system = {"role": "system",
                      "content": ROLEPLAY_TEMPLATE.replace("{card}", char_card(char))}
            work = [system] + messages
            content, _tc, finish, round_usage = self.stream_upstream(model, work, emit, use_tools=False)
            if username != ADMIN_USER:
                add_tokens_used(username, round_usage)
            if finish == "length":
                emit({"type": "error", "text": "回答达到长度上限被截断，可发送\"继续\"让角色接着说"})
            return content
        system = {"role": "system", "content": (
            f"你是爱沃服务器上的AI助手，当前登录用户: {username}。"
            "公共文件区(files)按日期文件夹存放所有用户公开上传的文件，全网可见；"
            "web/ 前缀表示网页根目录（主页等静态文件）。"
            "你可以: list_files 浏览目录、read_file 读取文本、write_file 把内容保存到公共文件区今日文件夹"
            "（自动标注上传者为当前用户，可执行文件自动加 .1）。脚本文件无法写入。")}
        work = [system] + messages
        final_answer = ""
        spent = 0
        for _ in range(MAX_TOOL_ROUNDS):
            if username != ADMIN_USER:
                used = tokens_used_today(username)
                if used + spent >= DAILY_TOKEN_LIMIT:
                    emit({"type": "error",
                          "text": f"今日 AI 额度（{DAILY_TOKEN_LIMIT} tokens）已用完，明天自动重置；已用 {used}/{DAILY_TOKEN_LIMIT}"})
                    break
            content, tool_calls, finish, round_usage = self.stream_upstream(model, work, emit)
            if username != ADMIN_USER:
                add_tokens_used(username, round_usage)
                spent += round_usage
            if not tool_calls:
                if finish == "length":
                    emit({"type": "error", "text": "回答达到长度上限被截断，可发送\"继续\"让AI接着写"})
                final_answer = content
                return final_answer
            work.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:
                    args = {}
                result = execute_tool(name, args, username)
                tool_log.append(f'{name}({json.dumps(args, ensure_ascii=False)}) → {result[:100]}')
                emit({"type": "tool", "name": name, "args": args, "result": result[:2000]})
                work.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        return final_answer or "(达到最大工具调用轮数)"

    def stream_upstream(self, model: str, messages: list, emit, use_tools: bool = True,
                        tools: list = None):
        """调用上游流式接口，边收边转发，返回(文本, 工具调用, finish, usage)"""
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": 1,
            "top_p": 0.95,
            "max_tokens": 32768,
            "thinking": {"type": "enabled", "clear_thinking": False},
        }
        if use_tools:
            payload["tools"] = tools or CHAT_TOOLS
            payload["tool_stream"] = True
        request = urllib.request.Request(
            GLM_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GLM_API_KEY}",
                "Accept": "text/event-stream",
            },
            method="POST")
        try:
            upstream = urllib.request.urlopen(request, timeout=300)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"上游接口错误 {e.code}: {detail}")
        except Exception as e:
            raise RuntimeError(f"连接失败: {e}")

        content = ""
        tool_calls = {}
        finish = None
        usage_total = 0
        try:
            for raw in upstream:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    j = json.loads(data)
                except Exception:
                    continue
                choice = (j.get("choices") or [{}])[0]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                if j.get("usage") and j["usage"].get("total_tokens"):
                    usage_total = j["usage"]["total_tokens"]
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    emit({"type": "reasoning_delta", "text": delta["reasoning_content"]})
                if delta.get("content"):
                    content += delta["content"]
                    emit({"type": "content_delta", "text": delta["content"]})
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_calls.setdefault(idx, {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
        finally:
            upstream.close()
        ordered = [tool_calls[i] for i in sorted(tool_calls)]
        for tc in ordered:
            if not tc["id"]:
                tc["id"] = f"call_{int(time.time()*1000)}_{id(tc)}"
        return content, ordered, finish, usage_total

    # ---------- 管理端动作 ----------
    def mgr_login(self, ip: str):
        """管理器登录：使用 __remote 的账号 + 密码 + 双重验证码"""
        try:
            payload = json.loads(self.read_body().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        account = str(payload.get("account", "")).strip()
        password = str(payload.get("password", ""))
        vercode = str(payload.get("vercode", "")).strip()
        if not remote_verify(account, password, vercode):
            time.sleep(0.4)
            oplog(account or "?", "管理器登录失败", ip)
            return self.send_json({"ok": False, "error": "账号、密码或验证码不正确"}, 400)
        MGR_LOGIN["ok"] = True
        MGR_LOGIN["user"] = account
        MGR_LOGIN["ts"] = time.time()
        admin_audit(account, "管理器登录成功", "桌面管理器", ip)
        oplog(account, "管理器登录成功", ip)
        return self.send_json({"ok": True, "account": account})

    def mgr_action(self, ip: str, is_note: bool = False):
        """管理器发来的操作：写审计备注 / 清理数据 / 账号管理"""
        try:
            payload = json.loads(self.read_body().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        if is_note:
            text = str(payload.get("text", ""))[:500]
            oplog("管理器", "管理操作", f"{text} | {ip}")
            return self.send_json({"ok": True})
        act = str(payload.get("action", ""))
        arg = str(payload.get("arg", ""))
        val = str(payload.get("value", ""))
        if act == "clear_sms":
            save_json(SMS_FILE, [])
            oplog("管理器", "清空短信收件箱", ip)
            return self.send_json({"ok": True})
        if act == "clear_visits":
            save_json(VISITS_FILE, {})
            oplog("管理器", "清空访问统计", ip)
            return self.send_json({"ok": True})
        if act == "clear_sessions":
            save_json(SESSIONS_FILE, {})
            oplog("管理器", "清理全部普通会话", ip)
            return self.send_json({"ok": True})
        if act == "delete_account":
            accounts = load_json(ACCOUNTS_FILE, {})
            if not arg or arg == ADMIN_USER or arg not in accounts:
                return self.send_json({"error": "无法删除该账号"}, 400)
            accounts.pop(arg, None)
            save_json(ACCOUNTS_FILE, accounts)
            sessions = load_json(SESSIONS_FILE, {})
            save_json(SESSIONS_FILE,
                      {k: v for k, v in sessions.items() if v.get("user") != arg})
            oplog("管理器", "删除账号", f"{arg} | {ip}")
            return self.send_json({"ok": True})
        if act == "set_password":
            accounts = load_json(ACCOUNTS_FILE, {})
            if arg not in accounts:
                return self.send_json({"error": "账号不存在"}, 400)
            if not val or len(val) < 6 or len(val) > 64:
                return self.send_json({"error": "密码长度需为 6-64 位"}, 400)
            salt = secrets.token_hex(16)
            accounts[arg].update({"salt": salt, "hash": hash_password(val, salt),
                                  "pwd_changed": time.strftime("%Y-%m-%d %H:%M:%S")})
            save_json(ACCOUNTS_FILE, accounts)
            sessions = load_json(SESSIONS_FILE, {})
            save_json(SESSIONS_FILE,
                      {k: v for k, v in sessions.items() if v.get("user") != arg})
            oplog("管理器", "修改账号密码", f"{arg} | {ip}")
            return self.send_json({"ok": True})
        if act == "add_account":
            err = register_user(arg, val, ip)
            if err:
                return self.send_json({"error": err}, 400)
            oplog("管理器", "新增账号", f"{arg} | {ip}")
            return self.send_json({"ok": True})
        return self.send_json({"error": "unknown action"}, 400)

    # ---------- 站长区 admax ----------
    def admax_route(self, path: str, query: dict):
        """所有 /admax 路由：未登录→专属登录页；已登录→管理面板"""
        if path == "/admax/login":
            return self.admax_login_page()
        admin = self.current_admin()
        if not admin:
            return self.admax_login_page("请先登录站长后台")
        if path == "/admax/aichat":
            return self.admin_chat_page(admin)
        if path == "/admax/aichat/history":
            return self.admin_chat_history_data()
        if path == "/admax/panel":
            return self.admax_panel(admin, query)
        if path == "/admax/audit_data":
            return self.send_json({"lines": self._read_jsonl(ADMIN_AUDIT_FILE, 200)})
        if path == "/admax/oplog_data":
            return self.send_json({"lines": self._read_jsonl(OPLOG_FILE, 200)})
        if path == "/admax/seclog_data":
            return self.send_json({"lines": self._read_jsonl(SECLOG_FILE, 200)})
        return self.admax_panel(admin, query)

    @staticmethod
    def _read_jsonl(path: Path, n: int) -> list:
        try:
            lines = path.read_text("utf-8").splitlines()
            return [json.loads(x) for x in lines[-n:] if x.strip()]
        except Exception:
            return []

    def admax_login_page(self, err: str = ""):
        err_html = f'<div class="msg err">{html.escape(err)}</div>' if err else ""
        body = f"""
<div style="max-width:420px;margin:8vh auto;background:#151c30;border:1px solid #2a3552;border-radius:14px;padding:34px">
  <div style="text-align:center;font-size:40px">🛡</div>
  <h1 style="text-align:center">站长控制台</h1>
  <p class="muted" style="text-align:center;margin-bottom:20px">最高权限区域 · 全程审计 · 双重验证</p>
  {err_html}
  <form method="post" action="/admax/login">
    <input name="username" type="password" placeholder="站长账号" required style="width:100%;padding:10px;margin-bottom:12px;-webkit-text-security:disc"><br>
    <input name="password" type="password" placeholder="站长密码" required style="width:100%;padding:10px;margin-bottom:12px"><br>
    <input name="uid" type="password" placeholder="身份验证码（二次验证）" required style="width:100%;padding:10px;margin-bottom:16px;-webkit-text-security:disc"><br>
    <button class="btn" type="submit" style="width:100%;padding:10px">进入控制台</button>
  </form>
  <p class="muted" style="margin-top:14px;text-align:center">登录行为将记录 IP 与时间</p>
</div>"""
        self.html_page(body, title="站长控制台")

    def admax_login(self, ip: str):
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        username = form.get("username", [""])[0].strip()
        password = form.get("password", [""])[0]
        uid = form.get("uid", [""])[0].strip()
        if username != ADMIN_USER or not verify_user(username, password):
            admin_audit(username or "?", "站长登录失败", "账号或密码错误", ip)
            oplog(username or "?", "站长登录失败", ip)
            return self.admax_login_page("账号或密码错误")
        if uid != ADMIN_UID_ANSWER:
            admin_audit(username, "站长登录失败", "身份验证码错误", ip)
            oplog(username, "站长验证失败", f"IP={ip}")
            return self.admax_login_page("身份验证码不正确")
        token = secrets.token_hex(32)
        sessions = load_json(ADMIN_SESSIONS_FILE, {})
        sessions = {k: v for k, v in sessions.items() if v.get("expires", 0) > time.time()}
        sessions[token] = {"user": username, "expires": time.time() + ADMIN_TTL, "ip": ip}
        save_json(ADMIN_SESSIONS_FILE, sessions)
        admin_audit(username, "站长登录成功", "", ip)
        oplog(username, "站长登录", ip)
        self.send_response(303)
        self.send_header("Set-Cookie",
                         f"adminsession={token}; HttpOnly; Path=/; Max-Age={ADMIN_TTL}")
        self.send_header("Location", "/admax/panel")
        self.end_headers()

    def admax_panel(self, admin: str, query: dict):
        accounts = load_json(ACCOUNTS_FILE, {})
        vis = visits_summary(1)
        tab = query.get("tab", ["users"])[0]
        notice_now = ""
        try:
            notice_now = NOTICE_FILE.read_text("utf-8")
        except Exception:
            pass
        hist = notice_history()
        tabs = {
            "users": ("👤 用户", self._tab_users(accounts)),
            "notice": ("📢 公告", self._tab_notice(notice_now, hist)),
            "logs": ("📋 操作日志", self._tab_logs("oplog_data", "用户操作日志（asssssets）")),
            "audit": ("🛡 站长审计", self._tab_logs("audit_data", "站长操作审计（admax，最高规格）")),
            "sec": ("🔬 安全日志", self._tab_logs("seclog_data", "安全检测报告")),
        }
        nav = "".join(
            f'<a class="btn {"gray" if k == tab else ""}" href="/admax/panel?tab={k}" '
            f'style="margin-right:6px">{v[0]}</a>' for k, v in tabs.items())
        nav += ' <a class="btn green" href="/admax/aichat" style="margin-left:6px">🤖 核心AI对话</a>'
        body = f"""
<h1>🛡 站长控制台 <span class="muted">管理员: {admin} · <a href="/admax/logout" style="color:#ff8a8a">退出</a></span></h1>
<div class="stats">
  <div class="card"><b>{len(accounts)}</b><span>注册账号</span></div>
  <div class="card"><b>{vis['uv']}</b><span>今日访客</span></div>
  <div class="card"><b>{vis['pv']}</b><span>今日浏览</span></div>
  <div class="card"><b>{vis['online']}</b><span>在线(5分钟)</span></div>
  <div class="card"><b>{sum(1 for _ in FILES_DIR.rglob('*') if _.is_file()) if FILES_DIR.is_dir() else 0}</b><span>公共文件</span></div>
  <div class="card"><b>{len(char_list())}</b><span>角色卡</span></div>
  <div class="card"><b>{'已连接' if mgr_bound() else '未连接'}</b><span>桌面管理器</span></div>
</div>
<div class="toolbar">{nav}</div>
{tabs.get(tab, tabs["users"])[1]}"""
        self.html_page(body, title="站长控制台")

    def _tab_users(self, accounts: dict) -> str:
        rows = []
        for name, info in sorted(accounts.items(), key=lambda x: x[1].get("created", "")):
            admin_mark = " <b style='color:#ffcf8a'>[站长]</b>" if name == ADMIN_USER else ""
            usage = tokens_used_today(name)
            del_btn = "" if name == ADMIN_USER else \
                f'<form style="display:inline" method="post" action="/admax/del_user"><input type="hidden" name="user" value="{name}"><button class="btn red" onclick="return confirm(\'删除账号 {name} 及其全部数据？\')">删除</button></form>'
            rows.append(f"<tr><td>{name}{admin_mark}</td><td>{info.get('created','')}</td>"
                        f"<td>{usage}/{DAILY_TOKEN_LIMIT}</td><td>{info.get('ip','')}</td><td>{del_btn}</td></tr>")
        return f"""<table><tr><th>账号</th><th>注册时间</th><th>今日AI额度</th><th>注册IP</th><th></th></tr>
{''.join(rows)}</table>"""

    def _tab_notice(self, current: str, hist: list) -> str:
        opts = "".join(f'<option value="{h["ts"]}">{html.escape(h["label"])}</option>' for h in hist)
        return f"""
<form method="post" action="/admax/save_notice">
  <h2>编辑当前公告</h2>
  <textarea name="content" class="editor">{html.escape(current)}</textarea>
  <div class="toolbar"><button class="btn green" type="submit">保存公告（旧版自动归档）</button>
  <span class="muted">支持 Markdown · 保存后前台立即生效</span></div>
</form>
<h2>历史公告（{len(hist)} 份）</h2>
<select onchange="if(this.value)window.open('/__notice_view?ts='+this.value)" style="min-width:340px">
  <option value="">-- 选择查看 --</option>{opts}
</select>"""

    def _tab_logs(self, api: str, title: str) -> str:
        return f"""<h2>{title}</h2><div id="logtbl">加载中…</div>
<script>
fetch('/admax/{api}').then(r=>r.json()).then(j => {{
  const rows = (j.lines || []).reverse().map(x =>
    `<tr><td>${{x.ts||''}}</td><td>${{x.user||x.admin||''}}</td><td>${{x.action||''}}</td><td>${{(x.detail||'')+' '+(x.ip||'')}}</td></tr>`).join('');
  document.getElementById('logtbl').innerHTML = rows
    ? '<table><tr><th>时间</th><th>用户</th><th>动作</th><th>详情</th></tr>'+rows+'</table>'
    : '<p class="muted">暂无记录</p>';
}}).catch(e => document.getElementById('logtbl').textContent = '加载失败');
</script>"""

    def admax_save_notice(self, admin: str, ip: str):
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        content = form.get("content", [""])[0]
        if len(content) > 100_000:
            return self.html_page('<div class="msg err">公告过长</div>', 400)
        notice_save(content, admin)
        return self.redirect("/admax/panel?tab=notice")

    def admax_del_user(self, admin: str, ip: str):
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        target = form.get("user", [""])[0].strip()
        accounts = load_json(ACCOUNTS_FILE, {})
        if not target or target == ADMIN_USER or target not in accounts:
            return self.html_page('<div class="msg err">无法删除该账号</div>', 400)
        accounts.pop(target)
        save_json(ACCOUNTS_FILE, accounts)
        sessions = load_json(SESSIONS_FILE, {})
        sessions = {k: v for k, v in sessions.items() if v.get("user") != target}
        save_json(SESSIONS_FILE, sessions)
        admin_audit(admin, "删除账号", target, ip)
        oplog(admin, "站长删号", target)
        return self.redirect("/admax/panel?tab=users")

    # ---------- 远程控制隐藏门禁 ----------
    def remote_key_login(self, user: str, nxt: str):
        """key 有效：种远程会话 cookie 并跳转目标页"""
        tok = secrets.token_hex(32)
        now = time.time()
        _remote_sessions[tok] = {"user": user, "exp": now + REMOTE_SESSION_TTL,
                                 "created": now, "ip": client_ip(self)}
        for k in [x for x, v in _remote_sessions.items() if v.get("exp", 0) < now]:
            _remote_sessions.pop(k, None)
        oplog(user, "key访问进入", nxt)
        self.send_response(303)
        self.send_header("Set-Cookie",
                         f"remotesession={tok}; HttpOnly; Path=/; "
                         f"Max-Age={REMOTE_SESSION_TTL}; SameSite=Strict")
        self.send_header("Location", nxt)
        self.end_headers()

    def remote_key_page_msg(self, msg: str):
        """无 key / key 不正确提示页（404 状态，正文给明确提示）"""
        body = f"""
<div style="max-width:420px;margin:10vh auto;background:#12182b;border:1px solid #2a3552;border-radius:14px;padding:34px;text-align:center">
  <div style="font-size:36px">🚫</div>
  <h1>{msg}</h1>
  <p class="muted">请通过登录后下发的地址进入，例如 <b>wzc.life/?key=1234567890</b></p>
</div>"""
        self.html_page(body, 404, title="无法访问")

    def remote_page_auth(self, query) -> tuple:
        """远程页面鉴权：?key=xxx 优先（有效期内可重复使用），其次远程会话 cookie。
        成功返回 (user, key)；失败发送提示页并返回 (None, '')"""
        k = (query.get("key", [""])[0] or "").strip()
        if k:
            user = remote_key_check(k)
            if not user:
                self.remote_key_page_msg("访问 key 不正确")
                return None, ""
            return user, k
        info, _tok = remote_session_check(self)
        if info:
            return info.get("user", "?"), ""
        self.remote_key_page_msg("缺少访问 key")
        return None, ""

    def remote_api_user(self, query) -> str:
        """远程接口鉴权（数据/命令/WS）：?key=xxx 优先，其次会话 cookie；无效返回 ''"""
        k = (query.get("key", [""])[0] or "").strip()
        if k:
            return remote_key_check(k)
        info, _tok = remote_session_check(self)
        return info.get("user", "?") if info else ""

    def remote_key_cookie(self, user: str) -> str:
        """key 鉴权成功后补种远程会话 cookie（供 WS/API 等后续请求使用）"""
        tok = secrets.token_hex(32)
        now = time.time()
        _remote_sessions[tok] = {"user": user, "exp": now + REMOTE_SESSION_TTL,
                                 "created": now, "ip": client_ip(self)}
        for k in [x for x, v in _remote_sessions.items() if v.get("exp", 0) < now]:
            _remote_sessions.pop(k, None)
        return f"remotesession={tok}; HttpOnly; Path=/; Max-Age={REMOTE_SESSION_TTL}; SameSite=Strict"

    def remote_gate_page(self, err: str = ""):
        err_html = f'<div class="msg err">{html.escape(err)}</div>' if err else ""
        body = f"""
<div style="max-width:400px;margin:10vh auto;background:#12182b;border:1px solid #2a3552;border-radius:14px;padding:30px">
  <div style="text-align:center;font-size:34px">🔑</div>
  <h1 style="text-align:center">身份验证</h1>
  <p class="muted" style="text-align:center;margin-bottom:18px">三重凭据 · 失败 5 次锁定</p>
  {err_html}
  <form method="post" action="/__remote/auth" autocomplete="off">
    <input name="account" placeholder="账号" required style="width:100%;padding:10px;margin-bottom:12px"><br>
    <input name="password" type="password" placeholder="密码" required style="width:100%;padding:10px;margin-bottom:12px"><br>
    <input name="vercode" type="password" placeholder="双重验证码" required style="width:100%;padding:10px;margin-bottom:16px"><br>
    <button class="btn" type="submit" style="width:100%;padding:10px">验证并进入</button>
  </form>
</div>"""
        self.html_page(body, title="验证")

    def remote_gate_auth(self):
        ip = client_ip(self)
        fails = [t for t in _gate_fail.get(ip, []) if time.time() - t < 600]
        _gate_fail[ip] = fails
        if len(fails) >= 5:
            admin_audit("?", "远程门禁锁定", f"IP={ip}", ip)
            return self.html_page('<div class="msg err">尝试次数过多，已被锁定 10 分钟</div>', 429)
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        account = form.get("account", [""])[0].strip()
        password = form.get("password", [""])[0]
        vercode = form.get("vercode", [""])[0].strip()
        if not remote_verify(account, password, vercode):
            fails.append(time.time())
            time.sleep(0.4 + secrets.randbelow(90) / 100)  # 随机延迟防爆破计时
            admin_audit(account or "?", "远程门禁验证失败", f"剩余尝试{max(0, 5 - len(fails))}", ip)
            oplog(account or "?", "远程门禁失败", ip)
            return self.remote_gate_page("账号、密码或双重验证码不正确")
        admin_audit(account, "远程门禁验证成功", "下发访问key", ip)
        oplog(account, "远程控制登录", ip)
        key = remote_key_issue(account)
        host = self.headers.get("Host", "wzc.life")
        body = f"""
<div style="max-width:460px;margin:10vh auto;background:#12182b;border:1px solid #2a3552;border-radius:14px;padding:34px;text-align:center">
  <div style="font-size:36px">🔐</div>
  <h1>访问密钥已生成</h1>
  <p class="muted">用下面的地址进入远程控制（10 分钟内有效，可重复使用）</p>
  <div style="font-size:36px;font-weight:bold;letter-spacing:8px;color:#7ad9ff;margin:20px 0">{key}</div>
  <div style="background:#0d1322;border:1px solid #2a3552;border-radius:10px;padding:14px;font-family:monospace;word-break:break-all">
    <a href="//{host}/?key={key}" style="color:#9fc7ff;font-size:15px">{host}/?key={key}</a>
  </div>
  <p class="muted" style="margin-top:16px">key 失效后重新登录即可获取新的</p>
</div>"""
        self.html_page(body, title="访问密钥")

    def remote_sys_exec(self, user: str):
        """操控界面系统操作：重启电脑 / 关机(需三重确认) / 重启服务器 / 切换主题"""
        import shutil
        try:
            req = json.loads(self.read_body().decode("utf-8"))
        except Exception:
            return self.send_json({"error": "请求格式错误"}, 400)
        action = str(req.get("action", ""))
        confirm = bool(req.get("confirm", False))
        ip = client_ip(self)

        if action == "theme":
            cur_file = WEB_ROOT / "index.html"
            try:
                cur = cur_file.read_text("utf-8", errors="replace")
            except Exception:
                cur = ""
            is_aiwo = ("LoveVodyanitsa" in cur) or ("爱沃" in cur)
            src = "index_aiwo.html" if not is_aiwo else "index_plain.html"
            new_name = "爱沃 LoveVodyanitsa" if not is_aiwo else "无主题（我的服务器）"
            shutil.copyfile(THEMES_DIR / src, cur_file)
            admin_audit(user, "切换主题", f"→ {new_name}", ip)
            return self.send_json({"ok": True, "theme": new_name})

        if action == "reboot":
            if not confirm:
                return self.send_json({"confirm": True, "msg": "需要确认"})
            admin_audit(user, "远程重启电脑", "", ip)
            oplog(user, "远程重启电脑", ip)
            subprocess.run(["shutdown", "/r", "/t", "5"], capture_output=True,
                           creationflags=0x08000000)
            return self.send_json({"ok": True, "msg": "电脑将在 5 秒后重启"})

        if action == "shutdown":
            if not confirm:
                return self.send_json({"confirm": True, "msg": "需要确认"})
            admin_audit(user, "远程关机", "", ip)
            oplog(user, "远程关机", ip)
            subprocess.run(["shutdown", "/s", "/t", "5"], capture_output=True,
                           creationflags=0x08000000)
            return self.send_json({"ok": True, "msg": "电脑将在 5 秒后关机（shutdown /a 可取消）"})

        if action == "restart_server":
            if not confirm:
                return self.send_json({"confirm": True, "msg": "需要确认"})
            admin_audit(user, "远程重启服务器", "", ip)
            oplog(user, "远程重启服务器", ip)
            # 分离启动延迟拉起的新实例，随后本进程退出
            code = ("import time,subprocess,sys;"
                    "time.sleep(2.5);"
                    "subprocess.Popen([sys.executable, r'd:\\server\\server1.1\\server.py'])")
            subprocess.Popen([sys.executable, "-c", code],
                             creationflags=0x00000008 | 0x00000200 | 0x08000000,
                             close_fds=True)  # DETACHED_PROCESS | NEW_GROUP | NO_WINDOW
            threading.Timer(1.0, lambda: os._exit(0)).start()
            return self.send_json({"ok": True,
                                   "msg": "服务器正在重启（约 5 秒），之后需重新走隐藏门禁验证"})

        if action == "switch_user":
            if not confirm:
                return self.send_json({"confirm": True, "msg": "需要确认"})
            admin_audit(user, "远程切换用户", "", ip)
            oplog(user, "远程切换用户", ip)
            # tsdiscon 断开当前会话：屏幕回到登录/切换用户界面，本会话进程继续运行
            r = subprocess.run(["tsdiscon"], capture_output=True, text=True,
                               creationflags=0x08000000)
            if r.returncode != 0:
                return self.send_json({"error": (r.stderr or r.stdout or "切换失败").strip()})
            return self.send_json({"ok": True,
                                   "msg": "已切换到登录界面（等同 开始菜单→切换用户）。服务器在后台继续运行，"
                                          "屏幕画面会暂停；切回时点「回到此用户」即可恢复远程控制"})

        if action == "reconnect_console":
            # 把本会话重新接回控制台（切换用户界面选回本用户；锁屏时先断开再接回，相当于"解除锁屏"）
            import getpass
            me = getpass.getuser()
            q = subprocess.run(["qwinsta"], capture_output=True, text=True,
                               creationflags=0x08000000)
            sid, attached = None, False
            for line in (q.stdout or "").splitlines():
                parts = line.split()
                if me in parts:
                    nums = [p for p in parts if p.isdigit()]
                    if nums:
                        sid = nums[0]
                        # 会话名是否存在决定是否挂在控制台（中文系统列名为"控制台"，英文为 console）
                        head = parts[0].lower()
                        attached = ("console" in head) or ("控制台" in parts[0])
                        break
            if not sid:
                return self.send_json({"error": "未找到本用户会话：" + (q.stdout or "").strip()})
            if attached:
                # 会话仍挂在控制台（含锁屏）：先断开让屏幕回登录界面，再接回以解除锁屏
                subprocess.run(["tsdiscon", sid], capture_output=True, text=True,
                               creationflags=0x08000000)
                time.sleep(1.2)
            r = subprocess.run(["tscon", sid, "/dest:console"], capture_output=True, text=True,
                               creationflags=0x08000000)
            if r.returncode != 0:
                return self.send_json({"error": (r.stderr or r.stdout or "接回控制台失败").strip()})
            admin_audit(user, "会话接回控制台", f"sid={sid}", ip)
            return self.send_json({"ok": True,
                                   "msg": "本会话已接回控制台：远程画面应已恢复。若仍黑屏，请稍候 2 秒或重试一次"})

        if action == "session_state":
            import getpass
            q = subprocess.run(["qwinsta"], capture_output=True, text=True,
                               creationflags=0x08000000)
            return self.send_json({"ok": True, "me": getpass.getuser(),
                                   "sessions": (q.stdout or "").strip()})

        if action == "list_users":
            # 用 net user 拿真实本地账号（排除内置账户），避免把 C:\Users 下的杂项目录误当用户
            q = subprocess.run(["net", "user"], capture_output=True, text=True,
                               creationflags=0x08000000)
            builtin = {"administrator", "guest", "defaultaccount", "wdagutilityaccount",
                       "helpassistant", "support_388945a0"}
            users = []
            for line in (q.stdout or "").splitlines():
                # 跳过表头 / 分隔线 / 状态行
                if ("\\" in line or "用户账户" in line or "User accounts" in line
                        or "命令" in line or "command" in line.lower()
                        or set(line.strip()) <= set("-=")):
                    continue
                for name in line.split():
                    if name.lower() in builtin or not name.isascii():
                        continue
                    users.append(name)
            import getpass
            return self.send_json({"ok": True, "users": users, "me": getpass.getuser()})

        if action == "restart_as_user":
            # 设置开机自动登录用户（无密码账户只需 DefaultUserName），然后重启电脑
            target = str(req.get("user", "")).strip()
            if not target or "/" in target or "\\" in target:
                return self.send_json({"error": "用户名无效"})
            if not confirm:
                return self.send_json({"confirm": True,
                                       "msg": f"需要确认：重启并自动登录 {target}"})
            key = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"

            def _reg(v, d):
                return subprocess.run(["reg", "add", key, "/v", v, "/t", "REG_SZ",
                                       "/d", d, "/f"], capture_output=True, text=True,
                                      creationflags=0x08000000)
            for v, d in (("AutoAdminLogon", "1"),
                         ("DefaultUserName", target),
                         ("DefaultPassword", ""),
                         ("DefaultDomain", os.environ.get("COMPUTERNAME", ""))):
                r = _reg(v, d)
                if r.returncode != 0:
                    return self.send_json({"error": "写入注册表失败（需要管理员权限运行服务器）："
                                                    + (r.stderr or r.stdout or "").strip()})
            admin_audit(user, "以用户重启", f"自动登录 {target}", ip)
            oplog(user, f"以用户重启(自动登录 {target})", ip)
            subprocess.run(["shutdown", "/r", "/t", "5"], capture_output=True,
                           creationflags=0x08000000)
            return self.send_json({"ok": True,
                                   "msg": f"电脑将在 5 秒后重启，自动登录「{target}」"
                                          "（约 1 分钟后画面恢复，之后可用「回到此用户」）"})

        return self.send_json({"error": "未知操作"}, 400)

    # ---------- 一班专用 ----------
    def current_class_user(self):
        tok = self.cookie_token("classsession")
        if not tok:
            return None
        info = load_json(CLASS_SESSIONS, {}).get(tok)
        if not info or info.get("exp", 0) < time.time():
            return None
        return info["user"]

    def class_route(self, path: str, query: dict):
        main_user = self.current_user()
        if path == "/class":
            # 只检测是否为已登录的合法（考号）账号
            if not main_user:
                return self.class_remind("login")
            if not CLASS_RE.match(main_user):
                return self.class_remind("exam")
            cu = self.current_class_user()
            if cu == main_user:
                return self.class_home(main_user)
            return self.class_verify_page(main_user)
        if not main_user:
            return self.redirect("/class")
        cu = self.current_class_user()
        if cu != main_user:
            return self.redirect("/class")
        if path == "/class/files":
            return self.class_home(main_user, query.get("msg", [""])[0])
        if path == "/class/dl":
            return self.class_download(query.get("path", [""])[0])
        if path == "/class/edit":
            return self.class_edit_page(main_user, query.get("path", [""])[0])
        if path == "/class/notice":
            try:
                return self.send_json({"md": CLASS_NOTICE.read_text("utf-8")})
            except Exception:
                return self.send_json({"md": "暂无说明"})
        return self.send_error(404)

    def class_remind(self, kind: str):
        """未登录 / 非考号账号 的提醒页（不透露考号格式）"""
        if kind == "login":
            tip = "请先在主界面右上角登录账号，再回到本入口"
            extra = '<p style="text-align:center"><a class="btn" href="/__login">前往登录 / 注册</a> <a class="btn gray" href="/">返回主页</a></p>'
        else:
            tip = "请注册以考号为名字的账号后再使用本入口（考号由老师发放）"
            extra = ('<p style="text-align:center"><a class="btn" href="/__login">前往注册</a> '
                     '<form style="display:inline" method="post" action="/class/logout">'
                     '<button class="btn gray">登出当前账号</button></form></p>')
        body = f"""
<div style="max-width:430px;margin:10vh auto;background:#151c30;border:1px solid #2a3552;border-radius:14px;padding:34px;text-align:center">
  <div style="font-size:34px">🔒</div>
  <h1 style="margin:12px 0 8px">一班专用入口</h1>
  <p class="muted" style="margin-bottom:20px;line-height:1.8">{tip}</p>
  {extra}
</div>"""
        self.html_page(body, title="一班专用")

    def class_verify_page(self, user: str):
        body = f"""
<div style="max-width:430px;margin:8vh auto;background:#151c30;border:1px solid #2a3552;border-radius:14px;padding:30px">
  <div style="text-align:center;font-size:34px">🔑</div>
  <h1 style="text-align:center">一班身份验证</h1>
  <p class="muted" style="text-align:center;margin-bottom:18px">账号 {html.escape(user)} · 请输入名字首字母完成验证</p>
  <form method="post" action="/class/verify" autocomplete="off">
    <input name="initial" placeholder="名字首字母（如 zhang→z）" required style="width:100%;padding:10px;margin-bottom:14px"><br>
    <button class="btn green" type="submit" style="width:100%;padding:10px">验证并进入</button>
  </form>
  <p class="muted" style="margin-top:12px;text-align:center"><a href="/class/logout" style="color:#7c9bff">使用其他账号</a></p>
</div>"""
        self.html_page(body, title="一班验证")

    def class_verify(self):
        main_user = self.current_user()
        if not main_user or not CLASS_RE.match(main_user):
            return self.redirect("/class")
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        initial = form.get("initial", [""])[0].strip()
        real = class_roster().get(main_user)
        if real is None or initial.lower() != str(real).lower():
            oplog(main_user, "一班验证失败", "首字母不匹配")
            return self.html_page('<div class="msg err">名字首字母不正确，请重试</div>'
                                  '<p><a class="btn" href="/class">返回重试</a></p>', 403)
        token = class_session_create(main_user)
        oplog(main_user, "一班验证通过", "")
        self.send_response(303)
        self.send_header("Set-Cookie",
                         f"classsession={token}; HttpOnly; Path=/; Max-Age={7 * 24 * 3600}; SameSite=Strict")
        self.send_header("Location", "/class")
        self.end_headers()

    def class_upload(self):
        user = self.current_class_user()
        if not user:
            return self.redirect("/class")
        ctype = self.headers.get("Content-Type", "")
        body = self.read_body()
        msg = email.message_from_bytes(
            ("Content-Type: " + ctype + "\r\n\r\n").encode() + body,
            policy=email.policy.default)
        saved = []
        # iter_attachments 会丢弃第一个子部件，必须遍历全部部件
        for part in (msg.get_payload() or []):
            fname = part.get_filename()
            if not fname:
                continue
            fname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(fname.replace("\\", "/"))).strip(". ") or "unnamed"
            qname = quarantine_name(fname)
            p = CLASS_FILES / qname
            i = 1
            while p.exists():
                p = CLASS_FILES / f"{Path(qname).stem}({i}){Path(qname).suffix}{'.1' if qname.endswith('.1') else ''}"
                i += 1
            payload = part.get_payload(decode=True) or b""
            if len(payload) > 100 * 1024 * 1024:
                continue
            p.write_bytes(payload)
            class_register_owner(p, user)
            saved.append(p.name)
        oplog(user, "一班上传", "；".join(saved)[:200])
        note = f"成功上传 {len(saved)} 个文件：{'、'.join(saved)}" if saved else "没有保存任何文件"
        return self.redirect("/class/files?msg=" + urllib.parse.quote(note))

    def class_save(self):
        user = self.current_class_user()
        if not user:
            return self.redirect("/class")
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        rel = form.get("path", [""])[0]
        content = form.get("content", [""])[0]
        try:
            p = safe_class_path(rel)
        except PermissionError:
            return self.html_page('<div class="msg err">非法路径</div>', 403)
        if not p.is_file():
            return self.html_page('<div class="msg err">文件不存在</div>', 404)
        if class_locked(p):
            return self.redirect("/class/files?msg=" + urllib.parse.quote(f"「{p.name}」已被锁定，无法编辑"))
        if len(content) > 1024 * 1024:
            return self.redirect("/class/files?msg=" + urllib.parse.quote("编辑内容超过 1MB，请直接上传文件"))
        p.write_text(content, encoding="utf-8", newline="")
        class_register_owner(p, user)
        meta = class_meta()
        reln = p.resolve().relative_to(CLASS_FILES.resolve()).as_posix()
        meta.setdefault(reln, {})["last_edit_by"] = user
        meta.setdefault(reln, {})["last_edit_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_json(CLASS_META, meta)
        oplog(user, "一班编辑", reln)
        return self.redirect("/class/files?msg=" + urllib.parse.quote(f"「{p.name}」编辑成功"))

    def class_lock(self):
        user = self.current_class_user()
        if not user:
            return self.redirect("/class")
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        rel = form.get("path", [""])[0]
        lock = form.get("lock", ["1"])[0] == "1"
        try:
            p = safe_class_path(rel)
        except PermissionError:
            return self.html_page('<div class="msg err">非法路径</div>', 403)
        if not p.is_file():
            return self.redirect("/class/files?msg=" + urllib.parse.quote("文件不存在"))
        meta = class_meta()
        reln = p.resolve().relative_to(CLASS_FILES.resolve()).as_posix()
        m = meta.setdefault(reln, {})
        m["locked"] = lock
        if lock:
            m["lock_by"] = user
            m["lock_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_json(CLASS_META, meta)
        oplog(user, "一班锁定" if lock else "一班解锁", reln)
        return self.redirect("/class/files?msg=" + urllib.parse.quote(
            f"「{p.name}」已{'锁定 🔒' if lock else '解锁'}"))

    def class_download(self, rel: str):
        if not self.current_class_user():
            return self.redirect("/class")
        try:
            p = safe_class_path(rel)
        except PermissionError:
            return self.send_error(403)
        if not p.is_file():
            return self.send_error(404)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition",
                         f"attachment; filename*=UTF-8''{urllib.parse.quote(p.name)}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def class_edit_page(self, user: str, rel: str):
        try:
            p = safe_class_path(rel)
        except PermissionError:
            return self.html_page('<div class="msg err">非法路径</div>', 403)
        if not p.is_file():
            return self.html_page('<div class="msg err">文件不存在</div>', 404)
        lk = class_locked(p)
        if lk:
            return self.html_page(f'<div class="msg err">「{html.escape(p.name)}」已被 {html.escape(lk.get("lock_by", "?"))} 锁定，无法编辑</div>', 403)
        raw = p.read_bytes()
        if b"\x00" in raw[:4096] or len(raw) > 1024 * 1024:
            return self.html_page('<div class="msg err">仅支持编辑 1MB 以内的文本文件</div>', 400)
        content = raw.decode("utf-8", errors="replace")
        reln = p.resolve().relative_to(CLASS_FILES.resolve()).as_posix()
        body = f"""
<div class="chead"><h1>✏️ 编辑：{html.escape(p.name)}</h1>
<div class="muted2">保存后全班可见 · <a href="/class/files" style="color:#fff">返回文件区</a></div></div>
<div class="cwrap">
<form method="post" action="/class/save">
  <input type="hidden" name="path" value="{html.escape(reln, quote=True)}">
  <textarea name="content" class="editor">{html.escape(content)}</textarea>
  <div style="margin-top:12px"><button class="btn green" type="submit">保存</button>
  <a class="btn gray" href="/class/files">取消</a></div>
</form>
</div>"""
        page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>编辑 · 一班</title><style>{self.CLASS_PAGE_CSS}</style></head><body>
<div style="max-width:1000px;margin:0 auto;padding:12px 6vw 0"><a href="/class/files" style="color:#4a7bd4;text-decoration:none;font-size:13.5px">← 返回文件区</a><a href="/" style="color:#4a7bd4;text-decoration:none;font-size:13.5px;margin-left:14px">🏠 主页</a></div>{body}</body></html>"""
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    CLASS_PAGE_CSS = """
body{font-family:"Microsoft YaHei",sans-serif;background:#eef2f7;color:#2c3e50;margin:0}
.chead{background:linear-gradient(135deg,#4a7bd4,#6db3d9);color:#fff;padding:26px 6vw 20px}
.chead h1{font-size:21px;margin:0 0 4px}
.chead .muted2{color:rgba(255,255,255,.75);font-size:13px}
.cwrap{max-width:1000px;margin:0 auto;padding:20px 6vw 60px}
.ccard{background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(30,60,120,.12);padding:18px 20px;margin-bottom:18px}
.ccard h2{font-size:16px;margin:0 0 12px;color:#2c5aa0}
.reserve{border:2px dashed #b9c8e0;border-radius:10px;padding:16px;color:#8aa0c0;font-size:13px;text-align:center}
table.cfiles{width:100%;border-collapse:collapse;font-size:13.5px}
table.cfiles th,table.cfiles td{padding:8px 10px;border-bottom:1px solid #e8edf4;text-align:left}
table.cfiles th{color:#2c5aa0;background:#f3f7fd}
.btn{display:inline-block;padding:5px 12px;border:none;border-radius:5px;background:#4a7bd4;color:#fff;cursor:pointer;font-size:12.5px;text-decoration:none}
.btn.green{background:#27ae60}.btn.orange{background:#e67e22}.btn.gray{background:#95a5a6}
.muted{color:#8a97a8;font-size:12px}
.msg{padding:9px 13px;border-radius:6px;margin-bottom:12px;font-size:13.5px}
.msg.ok{background:#e8f8f0;color:#1f8b4d}.msg.err{background:#fdecea;color:#c0392b}
textarea.editor{width:100%;height:55vh;font-family:Consolas,monospace;font-size:14px;padding:12px;border:1px solid #c5d2e0;border-radius:8px;resize:vertical}
#nModal{display:none;position:fixed;inset:0;background:rgba(20,40,80,.45);z-index:99;align-items:center;justify-content:center}
#nModal>div{background:#fff;border-radius:12px;max-width:600px;width:92%;max-height:75vh;overflow:auto;padding:22px 26px}
#nBody h1,#nBody h2,#nBody h3{margin:.5em 0 .3em;font-size:16px;color:#2c5aa0}
#nBody code{background:#f0f3f8;padding:1px 5px;border-radius:4px}
#nBody blockquote{border-left:3px solid #b9c8e0;margin:.4em 0;padding:2px 10px;color:#556}
"""

    def class_home(self, user: str, msg: str = ""):
        msg_html = ""
        if msg:
            cls = "ok" if "成功" in msg else "err"
            msg_html = f'<div class="msg {cls}">{html.escape(msg)}</div>'
        rows = []
        try:
            entries = sorted(CLASS_FILES.rglob("*"), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception:
            entries = []
        for e in entries:
            try:
                rel = e.resolve().relative_to(CLASS_FILES.resolve()).as_posix()
            except Exception:
                continue
            if e.is_dir():
                rows.append(f'<tr><td colspan="5">📁 {html.escape(e.name)}</td></tr>')
                continue
            lk = class_locked(e)
            lock_txt = f'🔒 {html.escape(lk.get("lock_by", "?"))}' if lk else "—"
            can_edit = "text" if e.stat().st_size <= 1024 * 1024 else ""
            try:
                raw = e.read_bytes()
                is_text = b"\x00" not in raw[:4096]
            except Exception:
                is_text = False
            edit_link = (f'<a class="btn green" href="/class/edit?path={urllib.parse.quote(rel)}">编辑</a>'
                         if is_text and e.stat().st_size <= 1024 * 1024 and not lk else
                         '<span class="muted">已锁</span>' if lk else '<span class="muted">—</span>')
            lock_btn = (f'<form style="display:inline" method="post" action="/class/lock">'
                        f'<input type="hidden" name="path" value="{rel}">'
                        f'<input type="hidden" name="lock" value="{"0" if lk else "1"}">'
                        f'<button class="btn {"gray" if lk else "orange"}">{"解锁" if lk else "锁定"}</button></form>')
            rows.append(
                f'<tr><td><a href="/class/dl?path={urllib.parse.quote(rel)}" style="color:#2c5aa0;text-decoration:none">📄 {html.escape(e.name)}</a></td>'
                f'<td>{human_size(e.stat().st_size)}</td>'
                f'<td>{html.escape(class_uploader(e))}</td>'
                f'<td>{lock_txt}</td>'
                f'<td>{edit_link} {lock_btn}</td></tr>')
        files_table = (f'<table class="cfiles"><tr><th>文件</th><th>大小</th><th>上传者</th><th>锁定</th><th>操作</th></tr>'
                       + "".join(rows) + "</table>") if rows else '<p class="muted">暂无文件，上传第一个吧</p>'
        body = f"""
<div class="chead">
  <h1>🎓 一班专属空间</h1>
  <div class="muted2">考号 {html.escape(user)} · <a href="/class/logout" style="color:#fff">退出</a></div>
</div>
<div class="cwrap">
  {msg_html}
  <div class="ccard">
    <h2>📤 上传文件（全班可见）</h2>
    <form method="post" action="/class/upload" enctype="multipart/form-data">
      <input type="file" name="file" required multiple style="margin-right:10px">
      <button class="btn green" type="submit">上传</button>
      <span class="muted">可执行文件自动加 .1 隔离 · 重名自动加序号</span>
    </form>
  </div>
  <div class="ccard">
    <h2>📂 班级文件区（任何人可编辑 / 上传 / 锁定）</h2>
    {files_table}
  </div>
  <div class="ccard">
    <h2>📌 更多功能</h2>
    <div class="reserve">预留空间 · 更多班级功能开发中（作业提醒 / 值日表 / 留言板…）</div>
  </div>
</div>
<div id="nModal"><div>
  <h2 style="color:#2c5aa0;margin:0 0 10px">📢 使用须知</h2>
  <div id="nBody" style="line-height:1.8;font-size:14px"></div>
  <div style="text-align:right;margin-top:14px"><button class="btn green" onclick="document.getElementById('nModal').style.display='none'">我已阅读并遵守</button></div>
</div></div>
<script>
fetch('/class/notice').then(r => r.json()).then(j => {{
  const t = j.md || '';
  document.getElementById('nBody').innerHTML = t
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h2>$1</h2>').replace(/^# (.*)$/gm,'<h2>$1</h2>')
    .replace(/\\*\\*(.+?)\\*\\*/g,'<b>$1</b>')
    .replace(/^[-*] (.*)$/gm,'• $1<br>')
    .replace(/\\n/g,'<br>');
  document.getElementById('nModal').style.display = 'flex';
}}).catch(() => {{}});
</script>"""
        page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>一班专属空间</title><style>{self.CLASS_PAGE_CSS}</style></head><body>
<div style="max-width:1000px;margin:0 auto;padding:12px 6vw 0"><a href="/" style="color:#4a7bd4;text-decoration:none;font-size:13.5px">← 返回主页</a></div>{body}</body></html>"""
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def remote_cmd_exec(self, user: str):
        """远程命令行：执行 PowerShell 命令并返回输出（危险命令需 ! 前缀二次确认）"""
        try:
            req = json.loads(self.read_body().decode("utf-8"))
        except Exception:
            return self.send_json({"error": "请求格式错误"}, 400)
        cmd = str(req.get("cmd", "")).strip()
        if not cmd:
            return self.send_json({"error": "命令为空"}, 400)
        # 危险命令防误触：关机/重启/格式化/批量删除等需加 ! 前缀强制执行
        _danger = re.compile(
            r"shutdown|restart-computer|stop-computer|format(\s|-volume)|clear-disk|"
            r"initialize-disk|remove-item\s+[^\r\n;|]*-recurse|rd\s+/s|del\s+/[qs]|"
            r"wipefs|diskpart|reg\s+delete\s+hklm|net\s+user\s+\S+\s+/delete|"
            r"taskkill\s+/f\s+/im\s+(python|server|frpc|explorer)", re.I)
        force = cmd.startswith("!")
        if force:
            cmd = cmd[1:].strip()
        elif _danger.search(cmd):
            admin_audit(user, "远程命令需确认", cmd[:200], client_ip(self))
            return self.send_json({
                "confirm": True,
                "msg": "⚠ 疑似危险命令（关机/重启/格式化/批量删除等）。确认执行请在命令最前面加英文 ! 再发送"})
        admin_audit(user, "远程命令", cmd[:200], client_ip(self))
        wrapped = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + cmd
        try:
            r = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", wrapped],
                capture_output=True, timeout=120,
                cwd=r"d:\server", creationflags=0x08000000)  # CREATE_NO_WINDOW
            out = (r.stdout or b"").decode("utf-8", errors="replace")
            err = (r.stderr or b"").decode("utf-8", errors="replace")
            if len(out) > 100_000:
                out = out[:100_000] + "\n…(输出过长已截断)"
            return self.send_json({
                "ok": True, "exit": r.returncode,
                "out": out, "err": err})
        except subprocess.TimeoutExpired:
            return self.send_json({"ok": False, "error": "命令执行超时（120 秒上限）"})
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)[:200]})

    # ---------- 站长核心AI对话 ----------
    def admin_chat_page(self, admin: str):
        options = "".join(
            f'<option value="{m}">{m}{"（默认）" if m == GLM_MODEL else ""}</option>'
            for m in ALLOWED_MODELS)
        body = f"""
<h1>🤖 核心AI <span class="muted">站长专属 · 工作目录 tmp · 无额度限制 · <a href="/admax/panel" style="color:#7c9bff">返回面板</a></span></h1>
<div class="toolbar">
  <label>模型:</label>
  <select id="model">{options}</select>
  <button class="btn gray" onclick="doCompact()">压缩历史</button>
  <button class="btn red" onclick="doClear()">清空历史</button>
  <span class="muted" id="ctxinfo"></span>
</div>
<div class="chatbox" id="box"></div>
<div class="inputbar">
  <textarea id="in" placeholder="输入消息（支持超长文本粘贴），Enter 发送，Shift+Enter 换行" style="height:90px"></textarea>
  <button class="btn" id="send" onclick="send()">发送</button>
</div>
<script>
const box = document.getElementById('box');
const input = document.getElementById('in');
const modelSel = document.getElementById('model');
const sendBtn = document.getElementById('send');
let msgs = [], busy = false;
modelSel.value = '{GLM_MODEL}';
modelSel.onchange = () => localStorage.setItem('core_model', modelSel.value);
(() => {{
  const saved = localStorage.getItem('core_model');
  if (Array.from(modelSel.options).some(o => o.value === saved)) modelSel.value = saved;
}})();

async function loadHist() {{
  try {{
    const j = await fetch('/admax/aichat/history').then(r => r.json());
    msgs = j.messages || [];
    updateCtx(j);
    for (const m of msgs) addBubble(m.role === 'user' ? 'user' : 'assistant', m.content);
    if (!msgs.length) addBubble('assistant', '核心AI就绪。每次对话都会自动加载 tmp/核心引导.md；可让我读写 tmp 下任意文件。');
  }} catch (e) {{ addBubble('assistant', '历史加载失败'); }}
}}
function updateCtx(j) {{
  document.getElementById('ctxinfo').textContent =
    `上下文 ${{(j.chars || 0).toLocaleString()}} 字符` +
    (j.core_ok ? ' · 核心引导.md 已加载' : ' · ⚠ 核心引导.md 缺失');
}}

input.addEventListener('keydown', e => {{
  if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); send(); }}
}});

function addBubble(role, text, cls) {{
  const d = document.createElement('div');
  d.className = 'bubble ' + (role === 'user' ? 'user' : 'assistant');
  const p = document.createElement('p'); p.className = 'content';
  p.textContent = text || ''; d.appendChild(p);
  if (cls) d.style.borderLeft = '3px solid ' + cls;
  box.appendChild(d); box.scrollTop = box.scrollHeight;
  return d;
}}
function addToolBubble(name, args, result) {{
  const d = document.createElement('div');
  d.className = 'bubble tool';
  const t = document.createElement('div');
  t.textContent = '🔧 ' + name + '(' + JSON.stringify(args) + ')';
  const r = document.createElement('div'); r.className = 'toolresult';
  r.textContent = (result || '').slice(0, 800);
  d.appendChild(t); d.appendChild(r); box.appendChild(d); box.scrollTop = box.scrollHeight;
}}

async function send() {{
  if (busy) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addBubble('user', text);
  busy = true; sendBtn.textContent = '生成中…';
  let live = null, think = null, contentEl = null, answer = '';
  const newLive = () => {{
    live = addBubble('assistant', '');
    think = live.querySelector ? live.querySelector('.think') : null;
    contentEl = live.firstChild ? live.querySelector('.content') : null;
    if (!contentEl) {{
      const p = document.createElement('p'); p.className = 'content';
      p.textContent = ''; live.appendChild(p); contentEl = p;
    }}
  }};
  try {{
    const resp = await fetch('/admax/aichat/send', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{text: text, model: modelSel.value}})
    }});
    if (!resp.ok) {{
      const e = await resp.json().catch(() => ({{error: '请求失败 ' + resp.status}}));
      throw new Error(e.error);
    }}
    const reader = resp.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '';
    while (true) {{
      const {{done, value}} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {{stream: true}});
      let nl;
      while ((nl = buf.indexOf('\\n\\n')) >= 0) {{
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 2);
        if (!line.startsWith('data:')) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === 'reasoning_delta') {{
          if (!live) newLive();
          if (think) {{ think.style.display = 'block'; think.textContent += ev.text; }}
        }} else if (ev.type === 'content_delta') {{
          if (!live) newLive();
          answer += ev.text;
          contentEl.textContent = answer;
          box.scrollTop = box.scrollHeight;
        }} else if (ev.type === 'tool') {{
          addToolBubble(ev.name, ev.args, ev.result);
        }} else if (ev.type === 'note') {{
          addBubble('assistant', 'ℹ️ ' + ev.text, '#3fd6a8');
        }} else if (ev.type === 'error') {{
          addBubble('assistant', '⚠ ' + ev.text, '#a03a3a');
        }}
      }}
    }}
    if (answer) msgs.push({{role: 'assistant', content: answer}});
    // 刷新上下文信息
    fetch('/admax/aichat/history').then(r => r.json()).then(updateCtx).catch(() => {{}});
  }} catch (e) {{
    addBubble('assistant', '❌ ' + e.message, '#a03a3a');
  }}
  busy = false; sendBtn.textContent = '发送';
}}

async function doCompact() {{
  if (busy) return;
  busy = true; sendBtn.textContent = '压缩中…';
  try {{
    const j = await fetch('/admax/aichat/compact', {{method: 'POST'}}).then(r => r.json());
    addBubble('assistant', j.ok ? `ℹ️ ${{j.msg}}` : '⚠ ' + j.msg, '#3fd6a8');
    updateCtx(j);
  }} catch (e) {{ addBubble('assistant', '❌ ' + e.message, '#a03a3a'); }}
  busy = false; sendBtn.textContent = '发送';
}}
async function doClear() {{
  if (busy || !confirm('确定清空全部对话历史？')) return;
  await fetch('/admax/aichat/clear', {{method: 'POST'}});
  msgs = []; box.innerHTML = '';
  addBubble('assistant', '历史已清空。核心AI就绪。');
  updateCtx({{chars: 0}});
}}
loadHist();
</script>"""
        self.html_page(body, title="核心AI · 站长专属")

    def admin_chat_history_data(self):
        msgs = load_json(ADMIN_CHAT_HIST, [])
        return self.send_json({
            "messages": msgs[-80:],
            "chars": sum(len(m["content"]) for m in msgs),
            "core_ok": (TMP_DIR / "核心引导.md").is_file()})

    def admin_maybe_compact(self, model: str, msgs: list, emit, force: bool = False):
        """超长上下文自动压缩（类似 Trae）：旧消息由 AI 摘要替代"""
        total = sum(len(m["content"]) for m in msgs)
        if not force and total < ADMIN_CTX_CHAR_LIMIT:
            return msgs, False
        if len(msgs) < 4:
            return msgs, False
        old, recent = msgs[:-6], msgs[-6:]
        if not old:
            return msgs, False
        hist_text = "\n\n".join(
            f"[{m['role']}] {m['content'][:3000]}" for m in old)
        prompt = (
            "请把以下AI对话历史压缩成一份要点摘要，必须保留：用户的需求与决定、涉及的文件路径与名称、"
            "关键代码/内容片段说明、未完成的任务、约定与风格要求。控制在 1500 字以内，直接输出摘要正文，不要评论。\n\n"
            f"===== 对话历史 =====\n{hist_text}\n===== 历史结束 =====")
        try:
            summary, _, _, _ = self.stream_upstream(
                model, [{"role": "user", "content": prompt}], lambda e: None, use_tools=False)
        except Exception as e:
            emit({"type": "error", "text": f"压缩失败（保留原历史）: {str(e)[:100]}"})
            return msgs, False
        if not summary:
            return msgs, False
        compacted = [
            {"role": "user", "content": f"[历史对话摘要（此前 {len(old)} 条消息已由系统压缩，原文已归档于本摘要要点中）]\n{summary}"},
            {"role": "assistant", "content": "收到，我已掌握全部历史要点，请继续。"}] + recent
        return compacted, True

    def admin_chat_send(self, admin: str):
        try:
            req = json.loads(self.read_body().decode("utf-8"))
            model = req.get("model") if req.get("model") in ALLOWED_MODELS else GLM_MODEL
            text = str(req.get("text", "")).strip()
        except Exception:
            return self.send_json({"error": "请求格式错误"}, 400)
        if not text:
            return self.send_json({"error": "消息为空"}, 400)
        msgs = load_json(ADMIN_CHAT_HIST, [])
        msgs.append({"role": "user", "content": text})

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(obj: dict):
            self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        admin_audit(admin, "站长AI对话", f"模型={model}，{len(text)}字", client_ip(self))
        try:
            msgs, compacted = self.admin_maybe_compact(model, msgs, emit)
            if compacted:
                emit({"type": "note", "text": f"上下文超过 {ADMIN_CTX_CHAR_LIMIT} 字符，旧历史已自动由AI压缩（可点'压缩历史'手动触发）"})
            core = core_guide()
            system = {"role": "system", "content": (
                f"你是爱沃(LoveVodyanitsa)服务器的站长专属核心AI。工作目录为服务器 server1.1/tmp，"
                "你可以无限制地浏览、读取、修改该目录下的所有文件（list_files / read_file / write_file）。"
                "写入内容一律 UTF-8。核心引导.md 是站长的最高优先级常驻指令，每次对话都会加载：\n\n"
                "<核心引导.md>\n" + core + "\n</核心引导.md>")}
            answer = self.admin_agent_loop(model, [system] + msgs, emit)
            msgs.append({"role": "assistant", "content": answer})
            save_json(ADMIN_CHAT_HIST, msgs)
            emit({"type": "done"})
        except Exception as e:
            try:
                emit({"type": "error", "text": str(e)})
                emit({"type": "done"})
            except Exception:
                pass

    def admin_chat_compact(self, admin: str):
        msgs = load_json(ADMIN_CHAT_HIST, [])
        if len(msgs) < 4:
            return self.send_json({"ok": False, "msg": f"历史仅 {len(msgs)} 条，无需压缩", "chars": sum(len(m['content']) for m in msgs)})
        def _silent(e):
            pass
        msgs2, _ = self.admin_maybe_compact(GLM_MODEL, msgs, _silent, force=True)
        save_json(ADMIN_CHAT_HIST, msgs2)
        admin_audit(admin, "手动压缩核心AI历史", f"{len(msgs)}→{len(msgs2)}条", client_ip(self))
        return self.send_json({
            "ok": True,
            "msg": f"已压缩：{len(msgs)} 条 → {len(msgs2)} 条",
            "chars": sum(len(m["content"]) for m in msgs2)})

    def admin_agent_loop(self, model: str, work: list, emit) -> str:
        """站长核心AI：tmp工具 + 长输出自动续写 + 无额度限制"""
        for _ in range(15):
            content, tool_calls, finish, usage = self.stream_upstream(
                model, work, emit, tools=TMP_TOOLS)
            if tool_calls:
                work.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except Exception:
                        args = {}
                    result = tmp_execute_tool(name, args)
                    emit({"type": "tool", "name": name, "args": args, "result": result[:2000]})
                    work.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                continue
            if finish == "length":
                merged = content
                for _c in range(3):  # 长输出自动续写，最多3次（≈12万tokens）
                    work.append({"role": "assistant", "content": merged})
                    work.append({"role": "user", "content":
                                 "继续输出剩余内容，从中断处无缝接着写，不要重复已有内容，不要任何前导说明或总结。"})
                    c2, tc2, f2, u2 = self.stream_upstream(model, work, emit, use_tools=False)
                    if tc2:
                        break
                    merged += c2
                    if f2 != "length":
                        emit({"type": "note", "text": "长输出达到单次上限，已自动续写补全"})
                        return merged
                if not tc2:
                    emit({"type": "note", "text": "长输出达到单次上限，已自动续写补全"})
                    return merged
                continue
            return content
        return "(达到最大工具调用轮数)"

    def admax_del_file(self, admin: str, ip: str):
        form = urllib.parse.parse_qs(self.read_body().decode("utf-8"), encoding="utf-8")
        rel = form.get("path", [""])[0]
        try:
            p = safe_files_path(rel)
        except PermissionError:
            return self.html_page('<div class="msg err">非法路径</div>', 403)
        if p.is_file():
            rp = files_rel(p)
            p.unlink()
            meta = load_json(FILE_META_FILE, {})
            meta.pop(rp, None)
            save_json(FILE_META_FILE, meta)
            admin_audit(admin, "删除文件", rp, ip)
        return self.redirect("/admax/panel?tab=files")


# ---------- 启动 ----------
def startup_checks():
    for d in (WEB_ROOT, FILES_DIR, ASSETS, ADMAX, NOTICE_HIST_DIR, QUARANTINE, TMP_DIR,
              CLASS_DIR, CLASS_FILES):
        d.mkdir(parents=True, exist_ok=True)
    guide = TMP_DIR / "核心引导.md"
    if not guide.is_file():
        guide.write_text(
            "# 核心引导\n\n"
            "这是站长专属核心AI的引导文件，**每次对话都会自动加载本文件全部内容**。\n\n"
            "## 站长给AI的常驻指令（在此书写）\n\n"
            "- 工作目录为 tmp，可无限制读写本目录所有文件\n"
            "- 重要决定与任务进度请用 write_file 记录到 tmp/任务状态.md，保证跨会话延续\n"
            "- （在此追加站长规则…）\n", "utf-8")
    if not CLASS_NOTICE.is_file():
        CLASS_NOTICE.write_text(
            "# 一班专属空间使用须知\n\n"
            "- 本区域仅限本班成员使用，考号 + 名字首字母验证\n"
            "- 文件区**任何人可编辑、上传、锁定**，请互相尊重劳动成果\n"
            "- 重要文件请及时**锁定**，防止误改；需要修改时先解锁\n"
            "- 禁止上传违规、违法、广告类内容，违者取消使用资格\n"
            "- 可执行文件上传后会自动加 .1 后缀隔离，无法直接运行\n"
            "- 遇到问题找站长\n", "utf-8")
    sweep_files()
    deny_execute_acl()
    mgr_token()   # 确保管理器令牌文件存在（桌面管理器读取它完成绑定）
    seclog("启动检查", "清扫+ACL 完成")


def ensure_global_autostart():
    """全局自启动：写入 HKLM Run，任何用户登录都会拉起服务器（已有实例会被单实例守卫拦下退出）"""
    val = f'"{sys.executable}" "{Path(__file__).resolve()}"'
    key = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    try:
        r = subprocess.run(["reg", "add", key, "/v", "AiwoServer", "/t", "REG_SZ",
                            "/d", val, "/f"], capture_output=True, text=True,
                           creationflags=0x08000000)
        if r.returncode == 0:
            seclog("全局自启动", "HKLM Run 已就绪（所有用户登录均自动拉起）")
        else:
            seclog("全局自启动", f"写入失败(需管理员)：{(r.stderr or '').strip()[:80]}")
    except Exception as e:
        seclog("全局自启动", str(e)[:80])


def _security_loop():
    while True:
        time.sleep(120)
        try:
            sweep_files()
            deny_execute_acl()
        except Exception as e:
            seclog("周期检查异常", str(e)[:100])


def _already_running() -> bool:
    """单实例守卫：80 端口已有服务器在跑则返回 True（SO_REUSEADDR 下 bind 探测不可靠，用连接探测）"""
    import socket as _s
    c = _s.socket()
    c.settimeout(0.6)
    try:
        return c.connect_ex(("127.0.0.1", 80)) == 0
    finally:
        c.close()


if __name__ == "__main__":
    if _already_running():
        print("LoveVodyanitsa already running on port 80, exit")
        raise SystemExit(0)
    ensure_global_autostart()
    startup_checks()
    threading.Thread(target=_security_loop, daemon=True).start()
    threading.Thread(target=ollama_watchdog, daemon=True).start()
    servers = []
    for port in (80, 52329):
        try:
            servers.append(ThreadingHTTPServer(("0.0.0.0", port), Handler))
        except OSError:
            print(f"port {port} busy, skip")
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    if servers:
        print(f"LoveVodyanitsa serving {WEB_ROOT} on ports {[s.server_address[1] for s in servers]}")
        servers[0].serve_forever()
