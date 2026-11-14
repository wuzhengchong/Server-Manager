#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱沃安全检测脚本（独立运行或由 server.py 周期调用）

职责：
  1. files/ 公共文件区清扫：可执行文件未加 .1 → 立即补上；后门信道文件(_r[qsc]_*.txt) → 删除；
     仿冒标准库包(含 __init__.py 的目录) → 隔离
  2. 对 files/ 内全部脚本与可执行文件设置 NTFS「拒绝执行」ACL —— 即使被改名绕过，
     任何人在该目录运行的脚本也无法执行（操作系统层无权限）
  3. 服务器根目录(server1.1)防仿冒检查：web 根 rooooot 内出现任何脚本/包 → 隔离
  4. 生成检测报告到 asssssets/seclog.jsonl

用法：
  python security_check.py          # 全量检查一次
  python security_check.py --watch  # 每 120 秒循环检查
"""
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(r"d:\server\server1.1")
FILES_DIR = BASE / "files"
WEB_ROOT = BASE / "rooooot"
ASSETS = BASE / "asssssets"
QUARANTINE = ASSETS / "quarantine"
SECLOG = ASSETS / "seclog.jsonl"

EXEC_EXTS = {".exe", ".bat", ".cmd", ".ps1", ".sh", ".py", ".pyw", ".vbs", ".vbe",
             ".js", ".jse", ".wsf", ".wsh", ".msi", ".com", ".scr", ".jar",
             ".php", ".asp", ".aspx", ".dll", ".hta", ".cpl", ".reg", ".pth"}


def seclog(action: str, detail: str = ""):
    try:
        SECLOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action, "detail": detail}
        with SECLOG.open("a", encoding="utf-8") as f:
            f.write(__import__("json").dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def sweep_files() -> list:
    moved = []
    if not FILES_DIR.is_dir():
        return moved
    for f in list(FILES_DIR.rglob("*")):
        try:
            if f.is_dir() and (f / "__init__.py").is_file():
                dst = QUARANTINE / f"files_{f.name}_{int(time.time())}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                f.rename(dst)
                moved.append(str(f.relative_to(FILES_DIR)).replace("\\", "/") + "/ →隔离")
                continue
            if f.is_file() and re.match(r"^_r[qsc]_.+\.txt$", f.name):
                f.unlink()
                moved.append(str(f.relative_to(FILES_DIR)).replace("\\", "/") + " →删除(后门信道)")
        except OSError:
            pass
    for f in FILES_DIR.rglob("*"):
        if f.is_file() and f.suffix.lower() in EXEC_EXTS and not f.name.endswith(".1"):
            try:
                f.rename(f.parent / (f.name + ".1"))
                moved.append(str(f.relative_to(FILES_DIR)).replace("\\", "/") + " →加.1")
            except OSError:
                pass
    return moved


def deny_execute() -> int:
    """NTFS 拒绝执行：*S-1-1-0 是 Everyone SID，(X) 为执行/遍历权限"""
    if not FILES_DIR.is_dir():
        return 0
    n = 0
    for f in FILES_DIR.rglob("*"):
        if f.is_file() and (f.suffix.lower() in EXEC_EXTS or f.name.endswith(".1")):
            try:
                r = subprocess.run(["icacls", str(f), "/deny", "*S-1-1-0:(X)"],
                                   capture_output=True, timeout=10)
                if r.returncode == 0:
                    n += 1
            except Exception:
                pass
    return n


def sweep_web_root() -> list:
    """web 根内出现脚本/包 → 静态服务目录必须只有 html/css/图片等"""
    moved = []
    if not WEB_ROOT.is_dir():
        return moved
    for f in list(WEB_ROOT.rglob("*")):
        try:
            if f.is_dir() and (f / "__init__.py").is_file():
                dst = QUARANTINE / f"web_{f.name}_{int(time.time())}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                f.rename(dst)
                moved.append("rooooot/" + f.name + "/ →隔离")
            elif f.is_file() and f.suffix.lower() in EXEC_EXTS:
                dst = QUARANTINE / f"web_{f.name}_{int(time.time())}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                f.rename(dst)
                moved.append("rooooot/" + f.name + " →隔离")
        except OSError:
            pass
    return moved


def run_all() -> dict:
    r1 = sweep_files()
    r2 = sweep_web_root()
    n = deny_execute()
    report = {"files": r1, "web": r2, "acl": n}
    if r1 or r2:
        seclog("清扫隔离", "；".join((r1 + r2)[:20]))
    seclog("ACL拒绝执行", f"本次应用 {n} 个文件")
    return report


if __name__ == "__main__":
    if "--watch" in sys.argv:
        print("安全检测守护已启动（每 120 秒一轮，Ctrl+C 退出）")
        while True:
            rep = run_all()
            if rep["files"] or rep["web"]:
                print(time.strftime("%H:%M:%S"), rep)
            time.sleep(120)
    else:
        rep = run_all()
        print("files 清扫:", rep["files"] or "无异常")
        print("web 根清扫:", rep["web"] or "无异常")
        print(f"拒绝执行 ACL 已应用: {rep['acl']} 个文件")
