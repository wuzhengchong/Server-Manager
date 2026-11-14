#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱沃远程控制模块：浏览器实时操控（WebSocket，复用 80 端口隧道）

原理：HTTP Upgrade 握手升级为 WebSocket →
  服务端线程用 mss 抓屏 → Pillow 缩放 → JPEG 编码 → 二进制帧推送（画面变化时才发送）
  客户端发 JSON 文本帧 → pynput 注入鼠标/键盘/滚轮/文字

安全：仅站长会话（adminsession Cookie）可建立连接；会话开始/结束写入审计日志。
"""
import base64
import ctypes
import hashlib
import io
import json
import socket
import struct
import threading
import time

try:
    import mss
except ImportError:
    mss = None
try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None
    ImageGrab = None
try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None
try:
    from pynput import mouse as mouse_mod
    from pynput import keyboard as kb_mod
except ImportError:
    mouse_mod = None
    kb_mod = None

# 进程级 DPI 感知：保证抓屏与鼠标坐标一致（物理像素）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()


def do_handshake(handler):
    """完成 WebSocket 升级握手，返回 WS 对象；失败返回 None"""
    key = handler.headers.get("Sec-WebSocket-Key", "")
    if not key:
        return None
    # 降低小帧（输入事件/心跳）传输延迟
    try:
        handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass
    resp = ("HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n")
    handler.connection.sendall(resp.encode())
    return WS(handler.connection)


class WS:
    """最小 RFC6455 实现：服务端收（客户端帧带掩码）、发（不带掩码）"""

    def __init__(self, sock):
        self.sock = sock
        self.send_lock = threading.Lock()
        self.alive = True

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def read_frame(self):
        """返回 (opcode, payload)；连接关闭返回 None"""
        try:
            h = self._recv_exact(2)
            b0, b1 = h[0], h[1]
            op = b0 & 0x0F
            masked = b1 & 0x80
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(ln) if ln else b""
            if mask and payload:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            return (op, payload)
        except Exception:
            return None

    def send_frame(self, op: int, payload: bytes) -> bool:
        if not self.alive:
            return False
        header = bytearray([0x80 | op])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack(">H", n)
        else:
            header.append(127)
            header += struct.pack(">Q", n)
        try:
            with self.send_lock:
                self.sock.sendall(bytes(header) + payload)
            return True
        except Exception:
            self.alive = False
            return False

    def send_binary(self, data: bytes) -> bool:
        return self.send_frame(2, data)

    def send_text(self, s: str) -> bool:
        return self.send_frame(1, s.encode("utf-8"))

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except Exception:
            pass


# pynput 键名映射
_KEYMAP = {
    "enter": "enter", "tab": "tab", "esc": "esc", "escape": "esc",
    "backspace": "backspace", "delete": "delete", "del": "delete",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end", "pageup": "page_up", "pagedown": "page_down",
    "space": "space", "win": "cmd", "meta": "cmd",
    # 修饰键与大小写锁定（此前 Shift/Ctrl/Alt/Caps 按名按下会被丢弃）
    "shift": "shift", "ctrl": "ctrl", "control": "ctrl", "alt": "alt",
    "capslock": "caps_lock", "caps": "caps_lock",
}
for i in range(1, 13):
    _KEYMAP[f"f{i}"] = f"f{i}"


class RemoteSession:
    """一次远程控制会话：抓屏推送 + 输入注入"""

    def __init__(self, ws: WS, username: str, audit=None):
        self.ws = ws
        self.username = username
        self.audit = audit or (lambda *a, **k: None)
        self.mouse = mouse_mod.Controller() if mouse_mod else None
        self.kb = kb_mod.Controller() if kb_mod else None
        self.quality = {"w": 640, "q": 60}   # 默认 360p（低带宽，操作优先）
        self.fps = 15                         # 帧率上限
        self.mode = ""                        # ""=普通推流  "smooth"=云游戏式流水线（测试入口）
        self.q_base = 60                      # smooth 模式的画质基准（拥塞降质后回升上限）
        self.frame_slot = None                # smooth：最新帧槽位（发送线程取走，慢客户端不阻塞抓屏）
        self.frame_lock = threading.Lock()
        self.last_input = 0.0                 # 最近一次输入时间（操作优先：输入后让路带宽）
        self.sticky = set()                   # 手机端粘滞修饰键
        self.pressed_keys = {}                # 按下/抬起模型：key名 -> (key对象, 修饰键列表)
        self.last_raw = None
        self.refresh = False
        self.sw = 1920
        self.sh = 1080
        # 同步探测真实屏幕尺寸：确保最早到达的触摸事件坐标也准确
        if mss is not None:
            try:
                with mss.mss() as _s:
                    _m = _s.monitors[1]
                    self.sw, self.sh = _m["width"], _m["height"]
            except Exception:
                pass
        self.sent_bytes = 0
        self.sent_frames = 0
        self.start = time.time()

    # ---------- 坐标 ----------
    def _abs(self, nx: float, ny: float):
        x = max(0, min(self.sw - 1, int(nx * self.sw)))
        y = max(0, min(self.sh - 1, int(ny * self.sh)))
        return (x, y)

    # ---------- 抓屏推送 ----------
    def _encode_cv(self, bgra: bytes, size, w: int, q: int) -> bytes:
        """OpenCV 路径：BGRA→BGR→缩放→JPEG（libjpeg-turbo，比 PIL 快 2-3 倍）"""
        arr = np.frombuffer(bgra, dtype=np.uint8).reshape(size[1], size[0], 4)
        frame = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        if w < size[0]:
            frame = cv2.resize(frame, (w, int(size[1] * w / size[0])),
                               interpolation=cv2.INTER_LINEAR)
        ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        return enc.tobytes() if ok else b""

    def _encode_pil(self, bgra: bytes, size, w: int, q: int) -> bytes:
        img = Image.frombytes("RGBA", size, bgra, "raw", "BGRA")
        if w < size[0]:
            img = img.resize((w, int(size[1] * w / size[0])), Image.BILINEAR)
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q)
        return buf.getvalue()

    def _grab_fallback(self):
        """无显示器/抓屏失败时的 GDI 回退"""
        if ImageGrab is None:
            return None
        try:
            img = ImageGrab.grab(all_screens=True)
            if img and img.size[0] > 0:
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=self.quality["q"])
                return buf.getvalue(), img.size
        except Exception:
            return None
        return None

    def capture_loop(self):
        if mss is None or Image is None:
            self.ws.send_text(json.dumps({"t": "err", "msg": "服务器缺少 mss/Pillow 依赖"}))
            return
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                self.sw, self.sh = mon["width"], mon["height"]
                self.ws.send_text(json.dumps({"t": "screen", "w": self.sw, "h": self.sh}))
                grab_fails = 0
                last_err_sent = 0.0
                idle_since = None  # 空闲自适应：画面静止 8 秒后降频抓屏，省 CPU
                while self.ws.alive:
                    t0 = time.time()
                    # 操作优先（普通模式）：刚收到输入事件时让出带宽（跳过本帧编码与发送）
                    # smooth 模式不跳帧：输入后画面立刻被下一帧取代，靠槽位机制保证始终发最新
                    if self.mode != "smooth" and time.time() - self.last_input < 0.05:
                        time.sleep(0.02)
                        continue
                    data = None
                    changed = False
                    try:
                        shot = sct.grab(mon)
                        if shot.size[0] <= 0:
                            raise OSError("显示器尺寸为 0（可能未连接显示器）")
                        if self.refresh or self.last_raw is None or shot.bgra != self.last_raw:
                            changed = True
                            idle_since = None
                            self.refresh = False
                            self.last_raw = shot.bgra
                            w = min(self.quality["w"], self.sw)
                            if cv2 is not None:
                                data = self._encode_cv(shot.bgra, shot.size, w, self.quality["q"])
                            else:
                                data = self._encode_pil(shot.bgra, shot.size, w, self.quality["q"])
                        elif idle_since is None:
                            idle_since = time.time()
                        grab_fails = 0
                    except Exception:
                        # mss 抓屏失败（无显示器等）→ GDI 回退
                        grab_fails += 1
                        fb = self._grab_fallback()
                        if fb:
                            data, size = fb
                            self.last_raw = None
                            if self.sw != size[0] or self.sh != size[1]:
                                self.sw, self.sh = size
                                self.ws.send_text(json.dumps({"t": "screen", "w": size[0], "h": size[1]}))
                        elif time.time() - last_err_sent > 30:
                            last_err_sent = time.time()
                            self.ws.send_text(json.dumps({
                                "t": "err",
                                "msg": "无法抓取屏幕：显示器可能未连接或已休眠（GDI 回退也失败）"}))
                    if data:
                        if self.mode == "smooth":
                            # 云游戏流水线：只保留最新帧，由发送线程推送（慢客户端不阻塞抓屏编码）
                            with self.frame_lock:
                                self.frame_slot = data
                        else:
                            if not self.ws.send_binary(data):
                                break
                            self.sent_bytes += len(data)
                            self.sent_frames += 1
                    dt = time.time() - t0
                    # 帧率上限；画面静止超 8 秒降频到约 4fps 采样（有变化立即恢复）
                    min_interval = 1.0 / max(5, self.fps)
                    if idle_since and time.time() - idle_since > 8 and not data:
                        min_interval = 0.25
                    time.sleep(max(0.005, min_interval - dt))  # 帧率上限
        except Exception as e:
            try:
                self.ws.send_text(json.dumps({"t": "err", "msg": str(e)[:150]}))
            except Exception:
                pass

    def _smooth_sender(self):
        """云游戏式发送线程：只发槽位里的最新帧；按发送耗时自适应画质（拥塞降质，空闲缓慢回升）"""
        while self.ws.alive:
            data = None
            with self.frame_lock:
                if self.frame_slot is not None:
                    data = self.frame_slot
                    self.frame_slot = None
            if data is None:
                time.sleep(0.004)
                continue
            t0 = time.time()
            if not self.ws.send_binary(data):
                break
            dt = time.time() - t0
            self.sent_bytes += len(data)
            self.sent_frames += 1
            budget = 1.0 / max(5, self.fps)
            if dt > budget:
                # 一帧发出去的时间超过帧预算 → 网络拥塞，降低画质
                if self.quality["q"] > 40:
                    self.quality["q"] -= 4
                    self.last_raw = None
            elif dt < budget * 0.4 and self.quality["q"] < self.q_base:
                self.quality["q"] += 1  # 画质缓慢回升

    # ---------- 输入注入 ----------
    def _mod_key(self, m: str):
        return {"ctrl": kb_mod.Key.ctrl, "alt": kb_mod.Key.alt,
                "shift": kb_mod.Key.shift, "win": kb_mod.Key.cmd}.get(m)

    def _key_obj(self, name: str):
        name = (name or "").strip()
        low = name.lower()
        if low in _KEYMAP:
            return getattr(kb_mod.Key, _KEYMAP[low], None)
        if len(name) == 1:
            return kb_mod.KeyCode.from_char(name)
        return None

    def _with_mods(self, mods: dict, action):
        """按住修饰键执行 action（含手机端粘滞键），用完清除粘滞"""
        pressed = []
        for m in ("ctrl", "alt", "shift", "win"):
            if (mods or {}).get(m) or m in self.sticky:
                k = self._mod_key(m)
                if k:
                    self.kb.press(k)
                    pressed.append(k)
        try:
            action()
        finally:
            for k in reversed(pressed):
                self.kb.release(k)
        self.sticky.clear()

    def _clipboard_paste(self, text: str):
        """长文本：写入远程剪贴板后模拟 Ctrl+V 粘贴"""
        try:
            import subprocess as _sp
            _sp.run(["powershell.exe", "-NoProfile", "-Command",
                     "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                     "Set-Clipboard -Value ([System.Text.Encoding]::UTF8.GetString("
                     "[Convert]::FromBase64String('" + base64.b64encode(text.encode('utf-8')).decode() + "')))"],
                    capture_output=True, timeout=15, creationflags=0x08000000)
            self.mouse.position = self.mouse.position  # no-op 保持焦点
            self.kb.press(kb_mod.Key.ctrl)
            self.kb.press("v")
            self.kb.release("v")
            self.kb.release(kb_mod.Key.ctrl)
        except Exception:
            try:
                self.kb.type(text[:200])
            except Exception:
                pass

    def handle_event(self, ev: dict):
        t = ev.get("t")
        if t == "ping":
            # 延迟测量：不标记 last_input（不影响画面节流）
            self.ws.send_text(json.dumps({"t": "pong", "ts": ev.get("ts", 0)}))
            return
        self.last_input = time.time()  # 操作优先：标记输入时刻，抓屏让路带宽
        if t == "quality":
            self.quality = {"w": max(200, min(1920, int(ev.get("w", 640)))),
                            "q": max(40, min(95, int(ev.get("q", 60))))}
            self.fps = max(5, min(30, int(ev.get("fps", 15))))
            if ev.get("mode") == "smooth":
                self.q_base = self.quality["q"]
                if self.mode != "smooth":
                    self.mode = "smooth"
                    threading.Thread(target=self._smooth_sender, daemon=True).start()
            else:
                self.mode = ""
            self.last_raw = None  # 立即按新参数重发
        elif t == "refresh":
            self.refresh = True
        elif t == "move" and self.mouse:
            self.mouse.position = self._abs(ev.get("x", 0), ev.get("y", 0))
        elif t == "click" and self.mouse:
            self.mouse.position = self._abs(ev.get("x", 0), ev.get("y", 0))
            btn = {0: mouse_mod.Button.left, 1: mouse_mod.Button.middle,
                   2: mouse_mod.Button.right}.get(int(ev.get("btn", 0)),
                                                  mouse_mod.Button.left)
            self._with_mods(ev.get("mods"), lambda: (self.mouse.press(btn),
                                                     self.mouse.release(btn)))
        elif t == "down" and self.mouse:
            self.mouse.position = self._abs(ev.get("x", 0), ev.get("y", 0))
            btn = mouse_mod.Button.left if int(ev.get("btn", 0)) == 0 else mouse_mod.Button.right
            self.mouse.press(btn)
        elif t == "up" and self.mouse:
            btn = mouse_mod.Button.left if int(ev.get("btn", 0)) == 0 else mouse_mod.Button.right
            self.mouse.release(btn)
        elif t == "scroll" and self.mouse:
            dy = int(ev.get("dy", 0))
            self.mouse.scroll(0, max(-5, min(5, dy)))
        elif t == "key" and self.kb:
            k = self._key_obj(ev.get("key", ""))
            if k:
                self._with_mods(ev.get("mods"), lambda: (self.kb.press(k),
                                                         self.kb.release(k)))
        elif t == "kdown" and self.kb:
            # 按下/抬起模型：长按重复交给远程 OS 自动处理，避免事件风暴
            name = str(ev.get("key", ""))
            k = self._key_obj(name)
            if k and name not in self.pressed_keys:
                mods = ev.get("mods") or {}
                held = []
                for m in ("ctrl", "alt", "shift", "win"):
                    if mods.get(m) or m in self.sticky:
                        mk = self._mod_key(m)
                        if mk:
                            self.kb.press(mk)
                            held.append(mk)
                self.kb.press(k)
                self.pressed_keys[name] = (k, held)
                self.sticky.clear()
        elif t == "kup" and self.kb:
            name = str(ev.get("key", ""))
            item = self.pressed_keys.pop(name, None)
            if item:
                k, held = item
                try:
                    self.kb.release(k)
                    for mk in held:
                        self.kb.release(mk)
                except Exception:
                    pass
        elif t == "text" and self.kb:
            text = str(ev.get("text", ""))[:2000]
            via = str(ev.get("via", "auto"))  # auto=智能(长文本走剪贴板) type=直接键入 clipboard=剪贴板粘贴
            if text:
                if via == "clipboard" or (via == "auto" and len(text) > 12):
                    # 剪贴板粘贴（比逐字键入快数十倍）
                    self._clipboard_paste(text)
                else:
                    self._with_mods(ev.get("mods"), lambda: self.kb.type(text))
        elif t == "sticky":
            k = ev.get("key", "")
            if k in ("ctrl", "alt", "shift", "win"):
                if k in self.sticky:
                    self.sticky.discard(k)
                else:
                    self.sticky.add(k)
                self.ws.send_text(json.dumps({"t": "sticky", "keys": sorted(self.sticky)}))

    def input_loop(self):
        while self.ws.alive:
            fr = self.ws.read_frame()
            if fr is None:
                break
            op, payload = fr
            if op == 8:  # close
                break
            if op == 9:  # ping → pong
                self.ws.send_frame(10, payload)
                continue
            if op == 1:
                try:
                    ev = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                try:
                    self.handle_event(ev)
                except Exception:
                    pass
        self.ws.alive = False

    # ---------- 会话主流程 ----------
    def run(self):
        self.audit(self.username, "远程控制", "会话开始")
        t_in = threading.Thread(target=self.input_loop, daemon=True)
        t_in.start()
        try:
            self.capture_loop()
        finally:
            # 会话结束：释放全部按住的键，防止卡键
            if self.kb:
                for name, (k, held) in list(self.pressed_keys.items()):
                    try:
                        self.kb.release(k)
                        for mk in held:
                            self.kb.release(mk)
                    except Exception:
                        pass
                self.pressed_keys.clear()
        self.ws.alive = False
        t_in.join(timeout=3)
        self.audit(self.username, "远程控制",
                   f"会话结束 {int(time.time()-self.start)}秒，{self.sent_frames}帧/{self.sent_bytes//1024}KB")
        self.ws.close()
