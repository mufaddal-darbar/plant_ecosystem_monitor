#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h> // Fixed: Required for WiFiClientSecure
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
#include <LittleFS.h>
#include <WiFiManager.h>
#include <ArduinoJson.h> // Ensure ArduinoJson v6 is installed

extern "C" {
  #include "user_interface.h"
}

// ================= NETWORK CONFIGURATION =================
const char* WIFI_SSID     = "Mufaddal_router";
const char* WIFI_PASSWORD = "darbar@777";

const int32_t WIFI_CHANNEL = 6;
const uint8_t ROUTER_BSSID[] = { 0x5A, 0x23, 0xE0, 0x8C, 0x3C, 0xBC };

// Telemetry Server Targets
const char* LOCAL_SERVER_HOST    = "192.168.31.113";
const uint16_t LOCAL_SERVER_PORT = 7000;

const char* PUBLIC_SERVER_HOST   = "plant-dashboard.mohammadielectronics.com"; 
const uint16_t PUBLIC_SERVER_PORT = 443;
const char* SERVER_PATH          = "/api/telemetry";

// OTA Pull Server (Pi Zero W)
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

// ================= HARDWARE DEFINITIONS ==================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

Adafruit_ADS1115 ads;

#define WAKE_BUTTON_PIN 14 // GPIO14 (Button to Ground, 10k pullup to 3.3V)
#define DHTPIN 13          // GPIO13 (Pin 7 on ESP-12F)
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
char dateStr[16]     = "2026-01-01"; // Default ISO fallback

bool telemetrySent   = false;
bool otaInProgress   = false;
bool otaInitialized  = false;
unsigned long awakeStartTime = 0;
unsigned long lastTick       = 0;

// Structures
struct FastConnectConfig {
  char ssid[33];
  char password[65];
  int32_t channel;
  uint8_t bssid[6];
  uint32_t magic;
};

struct SystemThresholds {
  float lowBattThreshold;
  float nightLuxThreshold;
  uint32_t daySleepMinutes;
  uint32_t nightSleepMinutes;
  uint32_t configVersion; // <--- Tracks sync version
  uint32_t magic;
};

SystemThresholds sysThresh = { 3.65f, 50.0f, 10, 30, 0, 0xABCD1234 };

FastConnectConfig netConfig;
const uint32_t CONFIG_MAGIC = 0xA5A5FACE;

struct SystemThresholds {
  float lowBattThreshold;     // e.g., 3.65 V
  float nightLuxThreshold;    // e.g., 50.0 Lux
  uint32_t daySleepMinutes;   // e.g., 10 Min
  uint32_t nightSleepMinutes; // e.g., 30 Min
  uint32_t magic;
};

SystemThresholds sysThresh = { 3.65f, 50.0f, 10, 30, 0xABCD1234 };
const uint32_t THRESH_MAGIC = 0xABCD1234;

// Forward Declarations
void saveFastConfig();
bool loadFastConfig();

// Filesystem Threshold Helpers
bool loadThresholds() {
  if (!LittleFS.exists("/thresholds.dat")) return false;
  File f = LittleFS.open("/thresholds.dat", "r");
  if (!f) return false;
  size_t bytes = f.read((uint8_t*)&sysThresh, sizeof(sysThresh));
  f.close();
  return (bytes == sizeof(sysThresh) && sysThresh.magic == THRESH_MAGIC);
}

void saveThresholds() {
  sysThresh.magic = THRESH_MAGIC;
  File f = LittleFS.open("/thresholds.dat", "w");
  if (f) {
    f.write((uint8_t*)&sysThresh, sizeof(sysThresh));
    f.flush();
    f.close();
    Serial.println(F("[Config] Custom thresholds saved to LittleFS."));
  }
}

// OLED Banner Helper
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

void runConfigPortal(bool forcePortal = false) {
  WiFiManager wm;
  wm.setConfigPortalTimeout(180);

  char strLowBatt[8];
  char strNightLux[8];
  char strDaySleep[8];
  char strNightSleep[8];

  snprintf(strLowBatt, sizeof(strLowBatt), "%.2f", sysThresh.lowBattThreshold);
  snprintf(strNightLux, sizeof(strNightLux), "%.0f", sysThresh.nightLuxThreshold);
  snprintf(strDaySleep, sizeof(strDaySleep), "%u", sysThresh.daySleepMinutes);
  snprintf(strNightSleep, sizeof(strNightSleep), "%u", sysThresh.nightSleepMinutes);

  WiFiManagerParameter custom_low_batt("low_batt", "Low Batt Cutoff (V)", strLowBatt, 6);
  WiFiManagerParameter custom_night_lux("night_lux", "Night Lux Threshold", strNightLux, 6);
  WiFiManagerParameter custom_day_sleep("day_sleep", "Day Sleep (min)", strDaySleep, 6);
  WiFiManagerParameter custom_night_sleep("night_sleep", "Night Sleep (min)", strNightSleep, 6);

  wm.addParameter(&custom_low_batt);
  wm.addParameter(&custom_night_lux);
  wm.addParameter(&custom_day_sleep);
  wm.addParameter(&custom_night_sleep);

  wm.setAPCallback([](WiFiManager *myWiFiManager) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.println(F("! SETUP / CONFIG AP !"));
    display.drawLine(0, 10, 127, 10, SSD1306_WHITE);
    display.setCursor(0, 16);
    display.println(F("Connect Phone to:"));
    display.setCursor(0, 28);
    display.println(F("SSID: Plant-Config-AP"));
    display.setCursor(0, 40);
    display.println(F("Pass: 12345678"));
    display.setCursor(0, 52);
    display.println(F("URL:  192.168.4.1"));
    display.display();
  });

  bool connected = false;

  if (forcePortal) {
    Serial.println(F("[Portal] Button forced config mode."));
    connected = wm.startConfigPortal("Plant-Config-AP", "12345678");
  } else {
    connected = wm.autoConnect("Plant-Config-AP", "12345678");
  }

  if (connected) {
    sysThresh.lowBattThreshold = atof(custom_low_batt.getValue());
    sysThresh.nightLuxThreshold = atof(custom_night_lux.getValue());
    sysThresh.daySleepMinutes = atoi(custom_day_sleep.getValue());
    sysThresh.nightSleepMinutes = atoi(custom_night_sleep.getValue());
    saveThresholds();
    saveFastConfig();

    showOledBanner("CONFIG SAVED", "Settings Stored!", "Rebooting MCU...");
    delay(1500);
    ESP.restart();
  } else {
    Serial.println(F("[Portal Timeout] No config provided. Sleeping..."));
    showOledBanner("SETUP TIMED OUT", "Running Local Loop", "Sleeping soon...");
    delay(1500);
  }
}

bool loadFastConfig() {
  if (!LittleFS.exists("/netcfg.dat")) return false;
  File f = LittleFS.open("/netcfg.dat", "r");
  if (!f) return false;
  size_t bytesRead = f.read((uint8_t*)&netConfig, sizeof(netConfig));
  f.close();
  return (bytesRead == sizeof(netConfig) && netConfig.magic == CONFIG_MAGIC);
}

void saveFastConfig() {
  strncpy(netConfig.ssid, WiFi.SSID().c_str(), sizeof(netConfig.ssid));
  strncpy(netConfig.password, WiFi.psk().c_str(), sizeof(netConfig.password));
  
  int32_t currentCh = WiFi.channel();
  netConfig.channel = (currentCh >= 1 && currentCh <= 14) ? currentCh : WIFI_CHANNEL;

  uint8_t* bssidPtr = WiFi.BSSID();
  if (bssidPtr != nullptr) {
    memcpy(netConfig.bssid, bssidPtr, 6);
  } else {
    memcpy(netConfig.bssid, ROUTER_BSSID, 6);
  }
  
  netConfig.magic = CONFIG_MAGIC;

  File f = LittleFS.open("/netcfg.dat", "w");
  if (f) {
    f.write((uint8_t*)&netConfig, sizeof(netConfig));
    f.flush();
    f.close();
    Serial.println(F("[Config] Saved new Channel, BSSID & Credentials to LittleFS!"));
  }
}

void updateDateTimeStrings() {
  time_t now = time(nullptr);
  if (now > 100000) {
    struct tm* timeinfo = localtime(&now);
    strftime(timeStr, sizeof(timeStr), "%H:%M:%S", timeinfo);    
    strftime(dateStr, sizeof(dateStr), "%Y-%m-%d", timeinfo);
  }
}

float calculateLux(int16_t rawAdc) {
  float vOut = rawAdc * 0.000125f;
  if (vOut <= 0.05f) return 0.0f;
  if (vOut >= 3.25f) return 50000.0f;

  float rLdr = 10000.0f * ((3.3f / vOut) - 1.0f);
  if (rLdr <= 0.0f) return 50000.0f;

  float lux = pow((500000.0f / rLdr), 1.4f);
  return constrain(lux, 0.0f, 65000.0f);
}

void lightSleepWakeCb() {}

bool isNightTime(float currentLux) {
  time_t now = time(nullptr);
  if (now > 100000) {
    struct tm* timeinfo = localtime(&now);
    int currentHour = timeinfo->tm_hour; // 0 - 23 (IST)
    if ((currentHour >= 20 || currentHour < 6) && (currentLux < sysThresh.nightLuxThreshold)) {
      return true;
    }
  } else {
    if (currentLux < sysThresh.nightLuxThreshold) {
      return true;
    }
  }
  return false;
}

uint32_t calculateDynamicSleepMinutes(float vBatt, float currentLux) {
  bool night = isNightTime(currentLux);

  if (night) {
    if (vBatt >= sysThresh.lowBattThreshold) {
      Serial.printf("[Power Mgmt] Night Mode: %u-min sleep cycle.\n", sysThresh.nightSleepMinutes);
      return sysThresh.nightSleepMinutes;
    } else {
      Serial.println(F("[Power Mgmt] Night Mode (Critical Battery): 60-min sleep cycle."));
      return 60;
    }
  }

  // Daytime sleep strategy
  if (vBatt <= sysThresh.lowBattThreshold) {
    Serial.println(F("[Power Mgmt] Critical Low Battery: 60-min emergency cycle."));
    return 60;
  }

  Serial.printf("[Power Mgmt] Daytime Mode: %u-min regular cycle.\n", sysThresh.daySleepMinutes);
  return sysThresh.daySleepMinutes;
}

void enterLightSleep(uint32_t total_minutes) {
  Serial.println(F("\n[Light Sleep] Shutting down OLED and Wi-Fi radio..."));

  char sleepBanner[24];
  snprintf(sleepBanner, sizeof(sleepBanner), "Sleep Duration: %um", total_minutes);

  bool night = isNightTime(estimatedLux);
  const char* modeLabel = night ? "NIGHT SLEEP CYCLE" : "DAY SLEEP CYCLE";

  showOledBanner("POWER MANAGEMENT", modeLabel, sleepBanner);
  delay(1200);

  display.clearDisplay();
  display.display();
  display.ssd1306_command(SSD1306_DISPLAYOFF);

  WiFi.disconnect(true);
  delay(10);
  wifi_station_disconnect();
  wifi_set_opmode(NULL_MODE);

  wifi_fpm_set_sleep_type(LIGHT_SLEEP_T);
  wifi_fpm_open();
  wifi_fpm_set_wakeup_cb(lightSleepWakeCb);

  Serial.printf("[Light Sleep] Sleeping for %u minutes...\n", total_minutes);
  Serial.flush();

  for (uint32_t i = 0; i < total_minutes; i++) {
    // Break sleep immediately if user presses button
    if (digitalRead(WAKE_BUTTON_PIN) == LOW) {
      Serial.println(F("[Wake] Button pressed during sleep! Waking up early..."));
      break;
    }
    wifi_fpm_do_sleep(60000000); // 60s per chunk
    delay(60001);                // Execution halts here in low power
  }

  wifi_fpm_close();
  Serial.println(F("\n[Light Sleep] Sleep finished! Warm resetting for clean Wi-Fi PHY..."));
  Serial.flush();

  ESP.restart();
}

void checkPullOTA() {
  if (WiFi.status() != WL_CONNECTED) return;

  Serial.println(F("[OTA Pull] Checking Raspberry Pi for new firmware..."));
  showOledBanner("OTA PULL CHECK", "Querying Pi Zero W", "Checking firmware.bin");

  WiFiClient otaClient;
  ESPhttpUpdate.setLedPin(2, LOW);
  ESPhttpUpdate.rebootOnUpdate(true);

  t_httpUpdate_return ret = ESPhttpUpdate.update(otaClient, LOCAL_SERVER_HOST, OTA_PULL_PORT, OTA_PULL_PATH, CURRENT_FW_VERSION);

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

void seedDefaultConfig() {
  strncpy(netConfig.ssid, WIFI_SSID, sizeof(netConfig.ssid));
  strncpy(netConfig.password, WIFI_PASSWORD, sizeof(netConfig.password));
  netConfig.channel = WIFI_CHANNEL;
  memcpy(netConfig.bssid, ROUTER_BSSID, 6);
  netConfig.magic = CONFIG_MAGIC;
}

void connectWiFi() {
  WiFi.disconnect(true);
  delay(20);
  WiFi.mode(WIFI_STA);
  WiFi.setOutputPower(17.5);

  bool hasFastConfig = loadFastConfig();

  if (!hasFastConfig) {
    Serial.println(F("[Config] No config found in flash, using sketch defaults..."));
    seedDefaultConfig();
  }

  Serial.printf("[FastConnect] Trying %s on Ch %d...\n", netConfig.ssid, (int)netConfig.channel);
  showOledBanner("CONNECTING WIFI", netConfig.ssid, "Fast-locking channel...");

  WiFi.begin(netConfig.ssid, netConfig.password, netConfig.channel, netConfig.bssid, true);

  unsigned long startWait = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - startWait < 15000)) {
    delay(200);
    yield();
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("\n[Notice] Fast connect failed. Launching unified portal..."));
    runConfigPortal(false);
  }

  wifiRssi = WiFi.RSSI();
  String ipStr = WiFi.localIP().toString();
  Serial.printf("\n[OK] WiFi Connected! IP: %s | RSSI: %d dBm\n", ipStr.c_str(), (int)wifiRssi);

  showOledBanner("WIFI CONNECTED!", ipStr.c_str(), "Syncing NTP Time...");
  syncNtpTime();
  delay(800);

  if (!otaInitialized) {
    initArduinoOTA();
  } else {
    ArduinoOTA.begin();
  }
}


// Helper: Draws a 4-bar Wi-Fi icon at coordinates (x, y)
void drawWifiIcon(int16_t x, int16_t y, int32_t rssi, bool connected) {
  if (!connected) {
    // Draw an 'X' icon if Wi-Fi is offline
    display.drawLine(x, y, x + 8, y + 8, SSD1306_WHITE);
    display.drawLine(x + 8, y, x, y + 8, SSD1306_WHITE);
    return;
  }

  // Determine number of active bars based on RSSI (dBm)
  int bars = 1;
  if (rssi >= -60)      bars = 4; // Excellent
  else if (rssi >= -70) bars = 3; // Good
  else if (rssi >= -80) bars = 2; // Fair
  else                  bars = 1; // Weak

  // Dimensions: 4 bars, 2px wide each, with 1px gap
  // Heights: 2px, 4px, 6px, 8px
  for (int i = 0; i < 4; i++) {
    int barHeight = (i + 1) * 2;
    int barX = x + (i * 3);
    int barY = y + (8 - barHeight);

    if (i < bars) {
      display.fillRect(barX, barY, 2, barHeight, SSD1306_WHITE); // Active bar
    } else {
      display.drawPixel(barX, y + 7, SSD1306_WHITE);              // Dim/inactive base dot
    }
  }
}

void setup() {
  awakeStartTime = millis();
  Serial.begin(115200);
  delay(100);
  Serial.println(F("\n=== ESP-12F Plant Monitor Starting ==="));

  // 1. Hardware Pin Configurations
  pinMode(WAKE_BUTTON_PIN, INPUT_PULLUP);

  Wire.begin(4, 5); // SDA = GPIO4, SCL = GPIO5
  Wire.setClock(100000);
  Wire.setClockStretchLimit(2000);

  if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    showOledBanner("ESP-12F BOOT", "Initializing I2C...", "Starting Plant Mon");
    delay(600);
  }

  // 2. Mount LittleFS First
  if (!LittleFS.begin()) {
    Serial.println(F("[LittleFS] Mount failed! Formatting partition..."));
    ESP.wdtDisable();
    LittleFS.format();
    ESP.wdtEnable(1000);
    LittleFS.begin();
    saveThresholds();
    Serial.println(F("[LittleFS] Formatted and initialized."));
  } else {
    Serial.println(F("[LittleFS] Mounted successfully."));
    if (!loadThresholds()) {
      saveThresholds();
    }
  }

  // 3. Check for 3-Second Button Hold
  if (digitalRead(WAKE_BUTTON_PIN) == LOW) {
    unsigned long pressStart = millis();
    bool longPressDetected = false;

    showOledBanner("BUTTON DETECTED", "Hold 3s for Setup...", "Release for Normal");

    while (digitalRead(WAKE_BUTTON_PIN) == LOW) {
      if (millis() - pressStart >= 3000) {
        longPressDetected = true;
        break;
      }
      delay(50);
      yield();
    }

    if (longPressDetected) {
      runConfigPortal(true);
    }
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

  // Sleep check
  if (now - awakeStartTime >= ACTIVE_DURATION_MS) {
    uint32_t dynamicSleepMinutes = calculateDynamicSleepMinutes(batteryVoltage, estimatedLux);
    enterLightSleep(dynamicSleepMinutes);
  }

  // Sensor reading and display refresh every 1.5s
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

    display.setCursor(0, 0);
    display.printf("%s %2ds %4.2fV", timeStr, secondsLeft, batteryVoltage);
    display.drawLine(0, 9, 127, 9, SSD1306_WHITE);

    display.setCursor(0, 11);
    if (WiFi.status() == WL_CONNECTED) {
      display.printf("%s %3ddB %3d%%", dateStr, (int)wifiRssi, batteryPercent);
    } else {
      display.printf("%s NO-NET %3d%%", dateStr, batteryPercent);
    }

    display.setCursor(0, 24);
    display.printf("Soil: %5.1f%% [%5d]", soilPct, rawSoil);

    display.setCursor(0, 37);
    display.printf("T:%4.1fC   H:%4.1f%%", currentTemp, currentHum);

    display.setCursor(0, 50);
    if (estimatedLux < 1000.0f) {
      display.printf("Sun : %5.0f Lux", estimatedLux);
    } else {
      display.printf("Sun : %5.1fk Lux", estimatedLux / 1000.0f);
    }

    display.display();

    // Telemetry Delivery (Local LAN with Cloudflare Fallback)
    if (!telemetrySent && (now - awakeStartTime >= 5000) && (WiFi.status() == WL_CONNECTED)) {
      bool sentSuccessfully = false;

      // JSON Builder
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

      // Attempt 1: Local LAN
      WiFiClient localClient;
      HTTPClient http;
      http.setTimeout(1500);

      if (http.begin(localClient, LOCAL_SERVER_HOST, LOCAL_SERVER_PORT, SERVER_PATH)) {
        http.addHeader("Content-Type", "application/json");
        Serial.println(F("[Telemetry] Attempting local LAN transfer..."));
        int code = http.POST(payload);

        if (code == 200) {
          String response = http.getString();
          StaticJsonDocument<384> doc;
          DeserializationError err = deserializeJson(doc, response);

          if (!err && doc.containsKey("config")) {
            JsonObject cfg = doc["config"];
            uint32_t serverVer = cfg["config_version"];

            // Only commit to Flash if settings actually changed
            if (serverVer != sysThresh.configVersion) {
              sysThresh.lowBattThreshold = cfg["low_batt"] | sysThresh.lowBattThreshold;
              sysThresh.nightLuxThreshold = cfg["night_lux"] | sysThresh.nightLuxThreshold;
              sysThresh.daySleepMinutes = cfg["day_sleep"] | sysThresh.daySleepMinutes;
              sysThresh.nightSleepMinutes = cfg["night_sleep"] | sysThresh.nightSleepMinutes;
              sysThresh.configVersion = serverVer;

              saveThresholds(); // Save to /thresholds.dat in LittleFS
              Serial.printf("[Config] Synced remote version %u to LittleFS!\n", serverVer);
            }
          }
          sentSuccessfully = true; 
        }
        else {
          Serial.printf("[Telemetry] Local LAN failed (code %d). Router ARP may be frozen.\n", code);
        }
        http.end();
      }

      // Attempt 2: Cloudflare HTTPS Fallback
      if (!sentSuccessfully) {
        Serial.println(F("[Telemetry] Falling back to Cloudflare Tunnel URL..."));
        
        WiFiClientSecure secureClient;
        secureClient.setInsecure();
        secureClient.setTimeout(4000);

        HTTPClient https;
        https.setTimeout(4000);

        if (https.begin(secureClient, PUBLIC_SERVER_HOST, PUBLIC_SERVER_PORT, SERVER_PATH, true)) {
          https.addHeader("Content-Type", "application/json");
          int httpsCode = https.POST(payload);
          Serial.printf("[Telemetry] Cloudflare POST Response: %d\n", httpsCode);
          if (code == 200) {
            String response = http.getString();
            StaticJsonDocument<384> doc;
            DeserializationError err = deserializeJson(doc, response);

            if (!err && doc.containsKey("config")) {
              JsonObject cfg = doc["config"];
              uint32_t serverVer = cfg["config_version"];

              // Only commit to Flash if settings actually changed
              if (serverVer != sysThresh.configVersion) {
                sysThresh.lowBattThreshold = cfg["low_batt"] | sysThresh.lowBattThreshold;
                sysThresh.nightLuxThreshold = cfg["night_lux"] | sysThresh.nightLuxThreshold;
                sysThresh.daySleepMinutes = cfg["day_sleep"] | sysThresh.daySleepMinutes;
                sysThresh.nightSleepMinutes = cfg["night_sleep"] | sysThresh.nightSleepMinutes;
                sysThresh.configVersion = serverVer;

                saveThresholds(); // Save to /thresholds.dat in LittleFS
                Serial.printf("[Config] Synced remote version %u to LittleFS!\n", serverVer);
              }
            }
            sentSuccessfully =  true; 
          }
          https.end();
        } else {
          Serial.println(F("[Telemetry] Failed to initialize Cloudflare HTTPS connection."));
        }
      }

      telemetrySent = sentSuccessfully;
    }
  }

  delay(10);
}