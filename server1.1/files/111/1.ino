// ============================================================================
// 特色代码片段合集（8 处，每处 8~15 行，均已加详细注释）
// 来源：打靶机器人项目（AprilTag 视觉 + 云台/舵机/激光/蜂鸣）
// 片段 1~6 为上位机 Python，片段 7~8 为下位机屏幕固件（111.ino）
// ============================================================================

// ---- 片段1：屏幕指令一问一答（tft_display.py）----
def cmd(self, text):
    # 发一条画图指令给屏幕，必须等到回 OK 才发下一条
    # 之前是连着发，发太快两条指令会粘在一起，屏上全是乱码花屏
    if self._ser is None:
        return False
    packet = (text.strip() + "\n").encode("ascii")
    try:
        with self._lock:            # 动画线程和主线程共用串口，不加锁必串包
            for _ in range(2):      # 没等到 OK 就重发一次
                self._ser.write(packet)
                resp = self._ser.readline().decode("ascii", "ignore").strip()
                if resp == "OK":    # 屏幕真执行完才回 OK，不会丢帧
                    return True
        self._ok = False            # 两次都没反应，八成是线被拔了
        return False                # 停动画，打靶主流程照常跑

// ---- 片段2：搜索状态雷达余辉动画（tft_display.py）----
def _anim_search(self):
    r, step = 46, 15                # 雷达半径46像素，每帧转15度，一圈约1.5秒
    for back in (1, 2, 3):
        a = math.radians((self._tick - back) * step)
        # 第一步用背景色把上一帧的三条线盖掉，只擦这一小块，整屏不闪
        self.cmd(f"LINE {CX} {CY} {CX+int(r*math.cos(a))} {CY+int(r*math.sin(a))} {BLACK}")
    colors = (GREEN, DGREEN, DARK)  # 主线最亮，身后两道逐级变暗 = 余辉
    for back, c in enumerate(colors):
        a = math.radians((self._tick - back) * step)
        self.cmd(f"LINE {CX} {CY} {CX+int(r*math.cos(a))} {CY+int(r*math.sin(a))} {c}")
    # 目标点做成"扫到了才亮"，一亮一灭比常亮有真实感
    if self._tick % 24 < 8:
        self.cmd(f"FCIRCLE {CX+30} {CY-24} 4 {RED}")
    else:
        self.cmd(f"FCIRCLE {CX+30} {CY-24} 4 {BLACK}")

// ---- 片段3：云台对准-脉冲步进与迟滞带（track_fire.py align_x）----
else:
    # err 是标签中心和瞄点的水平距离（像素），负数在左
    err = int(target.center[0] - cx - offset)
    if run_frames > 0:              # 这一步还没走完，按原方向继续
        run_frames -= 1
        speed = (-ALIGN_DIR * (1 if err > 0 else -1) * LEFT_DIR
                 * pulse_speed * VOLTAGE_POLARITY)
    elif pause > 0:                 # 走完一步先停两帧等惯性停稳
        pause -= 1
        speed = 0
    else:
        # 偏差进了容差带就停；没进就朝修正方向连续走
        speed = 0 if abs(err) <= half else (
            -ALIGN_DIR * (1 if err > 0 else -1) * LEFT_DIR
            * AIM_SPEED * VOLTAGE_POLARITY)
        # 已经停过车的话，偏差没出带就别再反向踢电机——
        # 不然正反两下互相抵消，云台看起来跟死机一样
        if speed != 0 and last_speed == 0 and abs(err) <= half:
            speed = 0

// ---- 片段4：动作音效表驱动（track_fire.py）----
# 每个动作配一组(频率,时长)，比赛要静音改一个开关就行
SND_FOUND = [(880, 0.06), (1175, 0.06)]    # 找到靶：叮咚
SND_FIRE  = [(1568, 0.05), (1047, 0.06)]   # 发射：哔啵
SND_DONE  = [(523, 0.1), (659, 0.1), (784, 0.1), (1047, 0.18)]  # 完成琶音

def beep(notes):
    # 蜂鸣器按表播报，操作手闭着眼都能听出打到第几步
    # 整个包在 try 里：音效坏了无所谓，不能把打靶流程带崩
    if not SOUND_ON:
        return
    try:
        for freq, dur in notes:
            board.buzzer_tone(freq)   # 发 @BU 让主板 tone() 响
            time.sleep(dur)
            board.buzzer_tone(0)      # 音与音之间断开，不然连成一片
            time.sleep(0.03)
    except Exception:
        pass

// ---- 片段5：屏幕动画后台线程（显示屏模块 tft_display.py）----
def start(self):
    # 刷新放到后台线程，视觉主循环就算卡住，屏幕动画照样转
    if self._thread is None or not self._thread.is_alive():
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()      # daemon=True：主程序退出线程自动跟着收

def _loop(self):
    interval = 1.0 / FPS          # 每秒 10 帧，一帧十来条指令，串口吃得下
    while self._running:
        begin = time.time()
        self._render()
        # 串口挂了：放慢节奏，每秒探一次，线插回来动画自动续上
        if not self._ok:
            try:
                self._ser.reset_input_buffer()
                if self.cmd("BL 1"):
                    self._static_state = None   # 强制重画静态层
            except Exception:
                pass

// ---- 片段6：边转边校对目标（track_fire.py turn_left_until）----
def turn_left_until(camera, detector, wanted_ids, ui, tft=None, idx=0, total=0):
    """云台持续往左转，看到 wanted_ids 中的标签就停下。"""
    last_speed = 0
    while True:
        frame, tags = read_tags(camera, detector)
        if frame is None:
            continue              # 这帧没拍到就跳过，转圈不能停
        hit = next((t for t in tags if t.tag_id in wanted_ids), None)
        if hit is not None:
            if last_speed != 0:
                board.set_motor_b(0)    # 找到靶立刻刹车
                last_speed = 0
            ui.log_write(f"发现 {hit.tag_id} 号靶，云台已停。")
            beep(SND_FOUND)
            return hit            # 交给 align_x 接手细调
        speed = left_speed()
        if speed != last_speed:
            board.set_motor_b(speed)    # 速度没变就不重发，省串口带宽
            last_speed = speed

// ---- 片段7：串口逐字节收包（下位机屏幕固件 111.ino）----
void loop() {
  // 串口是字节流，整块读会粘包。这里一个字节一个字节攒，
  // 攒到换行符才算一条完整指令；\r 直接扔掉（上位机换行带 \r\n）
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      rxBuffer[rxPos] = '\0';     // 补上字符串结尾，交给解析器
      processCommand(rxBuffer);
      rxPos = 0;                  // 收完复位，准备下一条
    } else if (c != '\r') {
      if (rxPos < RX_BUF_LEN - 1) {
        rxBuffer[rxPos++] = c;
      } else {
        rxPos = 0;                // 指令超过 128 字节直接作废，
        sendAck(false);           // 回 ERR 让上位机重发，顺便防内存越界
      }
    }
  }
}

// ---- 片段8：TEXT 文字命令解析（下位机屏幕固件 111.ino）----
if (strcmp(op, "TEXT") == 0) {
  // 难点：文字内容里可能带空格，普通 %s 读一半就断了。
  // 用 %[^\"] 匹配到下一个引号为止，坐标、颜色、"内容"一次拆完
  int x, y; uint32_t c; char str[64];
  if (sscanf(cmd, "TEXT %d %d %X \"%[^\"]\"", &x, &y, &c, str) == 4) {
    tft.setTextColor((uint16_t)c);
    tft.setCursor(x, y);
    tft.print(str);
    sendAck(true);          // 真画完了才回 OK，上位机才敢发下一条
  } else sendAck(false);    // 参数不齐回 ERR，上位机知道这条没画上
  return;
}
