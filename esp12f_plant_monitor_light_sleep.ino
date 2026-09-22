#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClient.h>
#include <ESP8266httpUpdate.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_ADS1X15.h>
#include <DHT.h>
#include <ArduinoOTA.h>
#include <time.h>

extern "C" {
  #include "user_interface.h"
}

// ================= NETWORK CONFIGURATION =================
const char* WIFI_SSID     = "Mufaddal_router";
const char* WIFI_PASSWORD = "darbar@777";

const int32_t WIFI_CHANNEL = 6;
const uint8_t ROUTER_BSSID[] = { 0x5A, 0x23, 0xE0, 0x8C, 0x3C, 0xBC };

// Telemetry Server (Raspberry Pi Zero W)
const char* SERVER_HOST        = "192.168.31.113"; 
const uint16_t SERVER_PORT     = 7000;
const char* SERVER_PATH        = "/api/telemetry";

// OTA Pull Server (Raspberry Pi Zero W serving /home/mufaddal/esp_OTSA_binary)
const uint16_t OTA_PULL_PORT   = 8080;
const char* OTA_PULL_PATH      = "/firmware.bin"; 
const char* CURRENT_FW_VERSION = "1.0.0";

// NTP / Time Configuration (IST: UTC + 5:30 = 19800 seconds)
const long  GMT_OFFSET_SEC      = 19800; 
const int   DAYLIGHT_OFFSET_SEC = 0;
const char* NTP_SERVER_1        = "pool.ntp.org";
const char* NTP_SERVER_2        = "time.nist.gov";

// ================= TIMING CONFIGURATION ==================
const unsigned long ACTIVE_DURATION_MS = 60000; // 30s awake window
const uint32_t SLEEP_INTERVAL_MINUTES  = 10;    // 10 minutes sleep

// ================= HARDWARE DEFINITIONS ==================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

Adafruit_ADS1115 ads;

#define DHTPIN 13 // GPIO13 (Pin 7 on ESP-12F)
#define DHTTYPE DHT22
DHT dht(DHTPIN, DHTTYPE);

const int16_t SOIL_AIR_VALUE   = 17500;
const int16_t SOIL_WATER_VALUE =  8900;

// Variables
float currentTemp    = 0.0f;
float currentHum     = 0.0f;
int16_t rawLDR       = 0;
float estimatedLux   = 0.0f;
int16_t rawSoil      = 0;
float soilPct        = 0.0f;
float batteryVoltage = 0.0f;
int batteryPercent   = 0;
int32_t wifiRssi     = 0;

char timeStr[10]     = "--:--:--";
char dateStr[14]     = "--/--/----";

bool telemetrySent   = false;
bool otaInProgress   = false;
bool otaInitialized  = false;
unsigned long awakeStartTime = 0;
unsigned long lastTick       = 0;

// Helper: Show full-screen event banner on OLED
void showOledBanner(const char* line1, const char* line2 = "", const char* line3 = "") {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 10);
  display.println(line1);
  if (strlen(line2) > 0) {
    display.setCursor(0, 26);
    display.println(line2);
  }
  if (strlen(line3) > 0) {
    display.setCursor(0, 42);
    display.println(line3);
  }
  display.display();
}

// Fetch and format local time from ESP8266 internal clock
void updateDateTimeStrings() {
  time_t now = time(nullptr);
  if (now > 100000) {
    struct tm* timeinfo = localtime(&now);
    strftime(timeStr, sizeof(timeStr), "%H:%M:%S", timeinfo);
    strftime(dateStr, sizeof(dateStr), "%d-%b-%y", timeinfo);
  }
}

// Helper: Converts raw LDR ADC into estimated Lux
float calculateLux(int16_t rawAdc) {
  float vOut = rawAdc * 0.000125f;
  if (vOut <= 0.05f) return 0.0f;
  if (vOut >= 3.25f) return 50000.0f;

  float rLdr = 10000.0f * ((3.3f / vOut) - 1.0f);
  if (rLdr <= 0.0f) return 50000.0f;

  float lux = pow((500000.0f / rLdr), 1.4f);
  return constrain(lux, 0.0f, 65000.0f);
}

// Empty callback for FPM sleep timer
void lightSleepWakeCb() {}

// Timed Forced Peripheral Modem (FPM) Light Sleep
// Timed Forced Peripheral Modem (FPM) Light Sleep
void enterLightSleep(uint32_t total_minutes) {
  Serial.println(F("\n[Light Sleep] Shutting down OLED and Wi-Fi radio..."));
  showOledBanner("SLEEP CYCLE", "Entering Light Sleep", "Duration: 10 Min");
  delay(1200);

  // 1. Put SSD1306 into hardware sleep
  display.clearDisplay();
  display.display();
  display.ssd1306_command(SSD1306_DISPLAYOFF);

  // 2. Shut down Wi-Fi modem completely
  WiFi.disconnect(true);
  delay(10);
  wifi_station_disconnect();
  wifi_set_opmode(NULL_MODE);

  // 3. Configure FPM Light Sleep
  wifi_fpm_set_sleep_type(LIGHT_SLEEP_T);
  wifi_fpm_open();
  wifi_fpm_set_wakeup_cb(lightSleepWakeCb);

  Serial.printf("[Light Sleep] Sleeping for %u minutes...\n", total_minutes);
  Serial.flush();

  for (uint32_t i = 0; i < total_minutes; i++) {
    wifi_fpm_do_sleep(60000000); // 60s per chunk
    delay(60001);                // Execution halts here in low power
  }

  // --- HARDWARE WOKE UP ---
  wifi_fpm_close();
  Serial.println(F("\n[Light Sleep] Sleep finished! Warm resetting for clean Wi-Fi PHY..."));
  Serial.flush();

  // Clean software reboot: Re-initializes WiFi PHY from scratch without cold-boot hardware bounce
  ESP.restart();
}

// Pull OTA: Checks Raspberry Pi Zero W HTTP server
void checkPullOTA() {
  if (WiFi.status() != WL_CONNECTED) return;

  Serial.println(F("[OTA Pull] Checking Raspberry Pi for new firmware..."));
  showOledBanner("OTA PULL CHECK", "Querying Pi Zero W", "Checking firmware.bin");

  WiFiClient otaClient;
  ESPhttpUpdate.setLedPin(2, LOW);
  ESPhttpUpdate.rebootOnUpdate(true);

  t_httpUpdate_return ret = ESPhttpUpdate.update(otaClient, SERVER_HOST, OTA_PULL_PORT, OTA_PULL_PATH, CURRENT_FW_VERSION);

  switch (ret) {
    case HTTP_UPDATE_FAILED:
      Serial.printf("[OTA Pull] Failed: (%d): %s\n", ESPhttpUpdate.getLastError(), ESPhttpUpdate.getLastErrorString().c_str());
      showOledBanner("OTA PULL FAILED", ESPhttpUpdate.getLastErrorString().c_str());
      delay(1200);
      break;
    case HTTP_UPDATE_NO_UPDATES:
      Serial.println(F("[OTA Pull] No new update found."));
      showOledBanner("FIRMWARE UP-TO-DATE", "Version: 1.0.0", "Resuming Monitor");
      delay(800);
      break;
    case HTTP_UPDATE_OK:
      Serial.println(F("[OTA Pull] Update successful! Rebooting..."));
      showOledBanner("OTA SUCCESSFUL", "Firmware Updated!", "Rebooting MCU...");
      delay(1500);
      break;
  }
}

void initArduinoOTA() {
  ArduinoOTA.setHostname("plant-monitor");

  ArduinoOTA.onStart([]() {
    otaInProgress = true;
    Serial.println(F("\n[OTA Push] Update started! Locking awake state."));
    showOledBanner("PUSH OTA ACTIVE", "Receiving binary...", "Flash in progress");
  });

  ArduinoOTA.onEnd([]() {
    Serial.println(F("\n[OTA Push] Update finished. Rebooting..."));
    showOledBanner("OTA COMPLETED", "Flashing Finished", "Rebooting ESP8266");
  });

  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    unsigned int pct = progress / (total / 100);
    Serial.printf("[OTA Push] Progress: %u%%\r", pct);

    display.clearDisplay();
    display.setCursor(0, 10);
    display.println(F("PUSH OTA FLASHING"));
    display.setCursor(0, 26);
    display.printf("Progress: %u%%\n", pct);
    display.drawRect(0, 42, 128, 8, SSD1306_WHITE);
    display.fillRect(2, 44, (pct * 124) / 100, 4, SSD1306_WHITE);
    display.display();
  });

  ArduinoOTA.onError([](ota_error_t error) {
    otaInProgress = false;
    Serial.printf("[OTA Push] Error: %u\n", error);
    showOledBanner("OTA PUSH ERROR", "Transfer Failed", "Returning to Loop");
    delay(2000);
  });

  ArduinoOTA.begin();
  otaInitialized = true;
  Serial.println(F("[OK] ArduinoOTA listening on port 8266"));
}

void syncNtpTime() {
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, NTP_SERVER_1, NTP_SERVER_2);
  Serial.print(F("Syncing NTP Time"));
  
  unsigned long start = millis();
  while (time(nullptr) < 100000 && millis() - start < 3000) {
    delay(100);
    Serial.print(F("."));
  }
  Serial.println();
  updateDateTimeStrings();
}

void connectWiFi() {
  // Ensure the Wi-Fi stack cleanly cycles from OFF to STA
  WiFi.disconnect(true);
  delay(10);
  WiFi.mode(WIFI_OFF);
  delay(20);
  WiFi.mode(WIFI_STA);
  wifi_set_opmode(STATION_MODE);
  
  WiFi.setOutputPower(17.5);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, WIFI_CHANNEL, ROUTER_BSSID, true);
  Serial.print(F("Connecting to WiFi"));
  showOledBanner("CONNECTING WIFI", WIFI_SSID, "Fast-locking Ch 6...");
  
  wifi_station_connect();

  Serial.print(F("Connecting to WiFi"));
  showOledBanner("CONNECTING WIFI", WIFI_SSID, "Waiting for IP...");

  unsigned long startWait = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startWait < 15000) {
    delay(250);
    Serial.print(F("."));
    yield();
  }

  if (WiFi.status() == WL_CONNECTED) {
    wifiRssi = WiFi.RSSI();
    String ipStr = WiFi.localIP().toString();
    Serial.println(F("\n[OK] WiFi Connected!"));
    Serial.printf("IP: %s | RSSI: %d dBm\n", ipStr.c_str(), wifiRssi);

    showOledBanner("WIFI CONNECTED!", ipStr.c_str(), "Syncing NTP Time...");
    syncNtpTime();
    delay(800);

    if (!otaInitialized) {
      initArduinoOTA();
    } else {
      ArduinoOTA.begin();
    }

   // checkPullOTA();
  } else {
    Serial.println(F("\n[WARN] WiFi offline, running locally..."));
    showOledBanner("WIFI OFFLINE", "Running Local Loop", "No Sync Available");
    delay(1200);
  }
}

void setup() {
  awakeStartTime = millis();
  Serial.begin(115200);
  delay(100);
  Serial.println(F("\n=== ESP-12F Plant Monitor Starting ==="));

  Wire.begin(4, 5); // SDA = GPIO4, SCL = GPIO5
  Wire.setClock(100000);
  Wire.setClockStretchLimit(2000);

  if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    showOledBanner("ESP-12F BOOT", "Initializing I2C...", "Starting Plant Mon");
    delay(800);
  }

  ads.setGain(GAIN_ONE);
  ads.begin(0x48);
  dht.begin();

  connectWiFi();
}

void loop() {
  yield();

  if (WiFi.status() == WL_CONNECTED) {
    ArduinoOTA.handle();
  }

  if (otaInProgress) return;

  unsigned long now = millis();

  // 30s awake window expired -> sleep for 10 minutes
  if (now - awakeStartTime >= ACTIVE_DURATION_MS) {
    enterLightSleep(SLEEP_INTERVAL_MINUTES);
    
    // Reset state after sleep wake-up
    awakeStartTime = millis();
    lastTick = 0;
    telemetrySent = false;
    
    connectWiFi();
    return;
  }

  // Periodic sensor readings & OLED refresh every 1.5s
  if (now - lastTick >= 1500) {
    lastTick = now;

    updateDateTimeStrings();

    rawLDR       = ads.readADC_SingleEnded(0);
    estimatedLux = calculateLux(rawLDR);

    rawSoil = ads.readADC_SingleEnded(1);
    int16_t rawBatt = ads.readADC_SingleEnded(2);
    batteryVoltage = (rawBatt * 0.000125f) * 2.0f;
    batteryPercent = (int)constrain(map((long)(batteryVoltage * 100), 320, 415, 0, 100), 0, 100);

    soilPct = constrain((float)map(rawSoil, SOIL_AIR_VALUE, SOIL_WATER_VALUE, 0, 100), 0.0f, 100.0f);

    float h = dht.readHumidity();
    float t = dht.readTemperature();
    if (!isnan(h)) currentHum = h;
    if (!isnan(t)) currentTemp = t;

    if (WiFi.status() == WL_CONNECTED) {
      wifiRssi = WiFi.RSSI();
    }

    int secondsLeft = (ACTIVE_DURATION_MS - (now - awakeStartTime)) / 1000;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);

    // Row 0 (Y=0): Time | Awake Countdown | Battery Volts
    // Example: "11:32:05  28s  4.03V"
    display.setCursor(0, 0);
    display.printf("%s %2ds %4.2fV", timeStr, secondsLeft, batteryVoltage);
    display.drawLine(0, 9, 127, 9, SSD1306_WHITE);

    // Row 1 (Y=11): Date | Wi-Fi RSSI (or OFFLINE) | Batt %
    // Example: "21-Sep-26 -65dBm 98%"
    display.setCursor(0, 11);
    if (WiFi.status() == WL_CONNECTED) {
      display.printf("%s %3ddB %3d%%", dateStr, (int)wifiRssi, batteryPercent);
    } else {
      display.printf("%s NO-NET %3d%%", dateStr, batteryPercent);
    }

    // Row 2 (Y=24): Soil Moisture % & Raw ADC
    display.setCursor(0, 24);
    display.printf("Soil: %5.1f%% [%5d]", soilPct, rawSoil);

    // Row 3 (Y=37): Temperature & Humidity
    display.setCursor(0, 37);
    display.printf("T:%4.1fC   H:%4.1f%%", currentTemp, currentHum);

    // Row 4 (Y=50): Sunlight in Lux (auto-scaled)
    display.setCursor(0, 50);
    if (estimatedLux < 1000.0f) {
      display.printf("Sun : %5.0f Lux", estimatedLux);
    } else {
      display.printf("Sun : %5.1fk Lux", estimatedLux / 1000.0f);
    }

    display.display();

    // Send HTTP POST telemetry around the 5s mark
    if (!telemetrySent && (now - awakeStartTime >= 5000) && (WiFi.status() == WL_CONNECTED)) {
      WiFiClient client;
      HTTPClient http;
      http.setTimeout(2500);

      if (http.begin(client, SERVER_HOST, SERVER_PORT, SERVER_PATH)) {
        http.addHeader("Content-Type", "application/json");

        String payload = "{";
        payload += "\"battery_voltage\":" + String(batteryVoltage, 2) + ",";
        payload += "\"battery_percent\":" + String(batteryPercent) + ",";
        payload += "\"wifi_rssi\":" + String(wifiRssi) + ",";
        payload += "\"temperature\":" + String(currentTemp, 1) + ",";
        payload += "\"humidity\":" + String(currentHum, 1) + ",";
        payload += "\"soil_moisture\":" + String(soilPct, 1) + ",";
        payload += "\"lux\":" + String(estimatedLux, 0) + ",";
        payload += "\"timestamp\":\"" + String(dateStr) + " " + String(timeStr) + "\"";
        payload += "}";

        Serial.print(F("Sending telemetry: "));
        Serial.println(payload);

        int code = http.POST(payload);
        Serial.printf("POST Response: %d\n", code);
        http.end();

        telemetrySent = true;
      }
    }
  }

  delay(10);
}