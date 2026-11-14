#include <Arduino.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <SPI.h>

#define TFT_CS   16
#define TFT_DC   17
#define TFT_RST  5
#define TFT_SCL  19
#define TFT_SDA  18
#define TFT_BLK  4

#define COL_BLACK   0x0000
#define COL_WHITE   0xFFFF
#define COL_RED     0xF800
#define COL_GREEN   0x07E0
#define COL_BLUE    0x001F
#define COL_YELLOW  0xFFE0
#define COL_CYAN    0x07FF
#define COL_MAGENTA 0xF81F

SPIClass hspi(HSPI);
Adafruit_ST7735 tft = Adafruit_ST7735(&hspi, TFT_CS, TFT_DC, TFT_RST);

#define RX_BUF_LEN 128
char rxBuffer[RX_BUF_LEN];
uint16_t rxPos = 0;

uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

void sendAck(bool ok){
  if(ok) Serial.println("OK");
  else   Serial.println("ERR");
}

void processCommand(char *cmd);

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(TFT_BLK, OUTPUT);
  digitalWrite(TFT_BLK, HIGH);
  hspi.begin(TFT_SCL, -1, TFT_SDA, TFT_CS);

  // 三选一，哪个能亮用哪个
  // tft.initR(INITR_GREENTAB);
  // tft.initR(INITR_REDTAB);
  tft.initR(INITR_BLACKTAB);

  tft.setRotation(0);
  tft.fillScreen(COL_BLACK);
  tft.setTextWrap(false);  // ✅【关键】关闭Adafruit GFX自动换行！！
  Serial.println("READY");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      rxBuffer[rxPos] = '\0';
      processCommand(rxBuffer);
      rxPos = 0;
    } else if (c != '\r') {
      if(rxPos < RX_BUF_LEN -1){
        rxBuffer[rxPos++] = c;
      }else{
        rxPos = 0;
        sendAck(false);
      }
    }
  }
}

void processCommand(char *cmd) {
  if(strlen(cmd)==0) {sendAck(true); return;}
  char op[16];
  sscanf(cmd, "%s", op);

  if (strcmp(op, "CLS") == 0) {
    uint32_t c=COL_BLACK;
    sscanf(cmd, "CLS %X", &c);
    tft.fillScreen((uint16_t)c);
    sendAck(true);
    return;
  }
  if (strcmp(op, "PX") == 0) {
    int x,y; uint32_t c;
    if(sscanf(cmd, "PX %d %d %X", &x,&y,&c)==3){
      tft.drawPixel(x,y,(uint16_t)c); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "LINE") == 0) {
    int x0,y0,x1,y1; uint32_t c;
    if(sscanf(cmd, "LINE %d %d %d %d %X", &x0,&y0,&x1,&y1,&c)==5){
      tft.drawLine(x0,y0,x1,y1,(uint16_t)c); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "RECT") == 0) {
    int x,y,w,h; uint32_t c;
    if(sscanf(cmd, "RECT %d %d %d %d %X", &x,&y,&w,&h,&c)==5){
      tft.drawRect(x,y,w,h,(uint16_t)c); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "FRECT") == 0) {
    int x,y,w,h; uint32_t c;
    if(sscanf(cmd, "FRECT %d %d %d %d %X", &x,&y,&w,&h,&c)==5){
      tft.fillRect(x,y,w,h,(uint16_t)c); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "CIRCLE") == 0) {
    int x,y,r; uint32_t c;
    if(sscanf(cmd, "CIRCLE %d %d %d %X", &x,&y,&r,&c)==4){
      tft.drawCircle(x,y,r,(uint16_t)c); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "FCIRCLE") == 0) {
    int x,y,r; uint32_t c;
    if(sscanf(cmd, "FCIRCLE %d %d %d %X", &x,&y,&r,&c)==4){
      tft.fillCircle(x,y,r,(uint16_t)c); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "TRI") == 0) {
    int x0,y0,x1,y1,x2,y2; uint32_t c;
    if(sscanf(cmd, "TRI %d %d %d %d %d %d %X", &x0,&y0,&x1,&y1,&x2,&y2,&c)==7){
      tft.drawTriangle(x0,y0,x1,y1,x2,y2,(uint16_t)c); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "FTRI") == 0) {
    int x0,y0,x1,y1,x2,y2; uint32_t c;
    if(sscanf(cmd, "FTRI %d %d %d %d %d %d %X", &x0,&y0,&x1,&y1,&x2,&y2,&c)==7){
      tft.fillTriangle(x0,y0,x1,y1,x2,y2,(uint16_t)c); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "TEXT") == 0) {
    int x,y; uint32_t c; char str[64];
    if(sscanf(cmd, "TEXT %d %d %X \"%[^\"]\"", &x,&y,&c, str) ==4){
      tft.setTextColor((uint16_t)c);
      tft.setCursor(x,y);
      tft.print(str);
      sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "TSIZE") == 0) {
    int s;
    if(sscanf(cmd, "TSIZE %d", &s)==1){
      tft.setTextSize(s); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "ROT") == 0) {
    int r;
    if(sscanf(cmd, "ROT %d", &r)==1){
      tft.setRotation(r); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "BL") == 0) {
    int v;
    if(sscanf(cmd, "BL %d", &v)==1){
      digitalWrite(TFT_BLK, v ? HIGH : LOW); sendAck(true);
    }else sendAck(false);
    return;
  }
  if (strcmp(op, "RGB") == 0) {
    uint8_t r,g,b;
    if(sscanf(cmd, "RGB %d %d %d", &r,&g,&b)==3){
      uint16_t cc = rgb565(r,g,b);
      Serial.printf("RGB565=0x%04X\n", cc); sendAck(true);
    }else sendAck(false);
    return;
  }
  sendAck(false);
}
