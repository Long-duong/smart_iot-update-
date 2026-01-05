#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DHT.h>
#include <ArduinoJson.h>

// ================= CẤU HÌNH CHÂN =================
#define DHTPIN D4     
#define DHTTYPE DHT11

#define LED_RED D5
#define LED_GREEN D6
#define LED_YELLOW D7

// ================= CẤU HÌNH WIFI & SERVER =================
const char* server = "http://....:3000"; 
const char* ssid = "....";
const char* password = ".....";

// ================= KHỞI TẠO =================
LiquidCrystal_I2C lcd(0x27, 16, 2);
DHT dht(DHTPIN, DHTTYPE);
WiFiClient client;

unsigned long lastEnv = 0;
unsigned long lastLCD = 0;
unsigned long lastLed = 0;
String lastMessage = ""; 
bool tempWarning = false;

void setup() {
  Serial.begin(115200);
  
  pinMode(LED_RED, OUTPUT);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_YELLOW, OUTPUT);
  
  digitalWrite(LED_RED, LOW);
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_YELLOW, LOW);

  Wire.begin();
  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.print("System Starting");

  dht.begin();
  connectWiFi();
}

void loop() {
  unsigned long currentMillis = millis();

  // 1. Kiểm tra WiFi
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    return;
  }

  // 2. Gửi Env & Hiện Temp/Hum (Mỗi 5 giây)
  if (currentMillis - lastEnv >= 5000) {
    sendEnv();
    lastEnv = currentMillis;
  }

  // 3. Lấy lệnh LED (Mỗi 2 giây)
  if (currentMillis - lastLed >= 2000) {
    getLedCommand();
    lastLed = currentMillis;
  }

  // 4. Lấy Cảnh Báo (Mỗi 3 giây)
  if (currentMillis - lastLCD >= 3000) {
    getLastAlert();
    lastLCD = currentMillis;
  }
}

// ================= HÀM WIFI =================
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  
  lcd.clear();
  lcd.print("WiFi Connecting");
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    digitalWrite(LED_YELLOW, !digitalRead(LED_YELLOW));
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    lcd.clear();
    lcd.print("WiFi Connected!");
    lcd.setCursor(0, 1);
    lcd.print(WiFi.localIP());
    digitalWrite(LED_YELLOW, LOW);
    digitalWrite(LED_GREEN, HIGH);
    delay(2000);
    digitalWrite(LED_GREEN, LOW);
  } else {
    lcd.clear();
    lcd.print("WiFi Failed!");
    digitalWrite(LED_RED, HIGH);
  }
}

// ================= HÀM GỬI MÔI TRƯỜNG =================
void sendEnv() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();

  if (isnan(h) || isnan(t)) return;

  // Hiển thị Nhiệt độ/Độ ẩm (Trạng thái bình thường)
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Temp: " + String(t, 1) + (char)223 + "C");
  lcd.setCursor(0, 1);
  lcd.print("Hum:  " + String(h, 1) + "%");

  // Gửi API
  HTTPClient http;
  String url = String(server) + "/api/env";
  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");
  String json = "{\"temp\":" + String(t) + ",\"hum\":" + String(h) + "}";
  http.POST(json);
  
  // Cảnh báo nhiệt độ nóng
  if (t > 35) {
    digitalWrite(LED_YELLOW, HIGH);
    tempWarning = true;
  } else {
    tempWarning = false;
  }
  http.end();
}

// ================= HÀM LED =================
void getLedCommand() {
  HTTPClient http;
  String url = String(server) + "/api/esp/led";
  http.begin(client, url);
  if (http.GET() == HTTP_CODE_OK) {
    StaticJsonDocument<200> doc;
    deserializeJson(doc, http.getString());
    String color = doc["color"].as<String>();
      
    if (color == "red") {
      digitalWrite(LED_RED, HIGH); digitalWrite(LED_GREEN, LOW); digitalWrite(LED_YELLOW, LOW);
    } else if (color == "yellow") {
      digitalWrite(LED_RED, LOW); digitalWrite(LED_GREEN, LOW); digitalWrite(LED_YELLOW, HIGH);
    } else if (color == "green") {
      digitalWrite(LED_RED, LOW); digitalWrite(LED_GREEN, HIGH); digitalWrite(LED_YELLOW, LOW);
    } else { 
      digitalWrite(LED_RED, LOW); digitalWrite(LED_GREEN, LOW);
      if (!tempWarning) digitalWrite(LED_YELLOW, LOW);
    }
  }
  http.end();
}

// ================= HÀM CẢNH BÁO (ĐÃ SỬA) =================
void getLastAlert() {
  HTTPClient http;
  String url = String(server) + "/api/esp/last-alert";
  http.begin(client, url);
  
  if (http.GET() == HTTP_CODE_OK) {
    StaticJsonDocument<300> doc;
    deserializeJson(doc, http.getString());
    String msg = doc["message"].as<String>();
    
    // Nếu có tin nhắn mới (Khác tin cũ và không phải rỗng)
    if (msg != "No alert" && msg != "" && msg != lastMessage) {
      lastMessage = msg;
      
      // === PHẦN SỬA ĐỔI: HIỂN THỊ TRỰC TIẾP ===
      lcd.clear();
      
      // In dòng 1 (16 ký tự đầu)
      lcd.setCursor(0, 0);
      lcd.print(msg.substring(0, 16)); 
      
      // In dòng 2 (Ký tự còn lại nếu dài)
      if (msg.length() > 16) {
        lcd.setCursor(0, 1);
        lcd.print(msg.substring(16, 32));
      }
      
      // Nháy đèn đỏ và giữ màn hình trong 3 giây để đọc
      for(int i=0; i<6; i++) { // Nháy 6 lần nhanh (khoảng 2-3s)
        digitalWrite(LED_RED, HIGH); delay(200);
        digitalWrite(LED_RED, LOW); delay(200);
      }
      
      // Sau khi loop xong, lần chạy sau của sendEnv() sẽ trả lại màn hình Temp/Hum
    }
  }
  http.end();
}
