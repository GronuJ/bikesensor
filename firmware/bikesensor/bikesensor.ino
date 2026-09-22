// ESP32-C3 Pure IoT Standalone Bike Mapping Logger & Wi-Fi Sync.
//
// Wiring (ESP32-C3 SuperMini) — two pin maps, see the PIN MAP block below:
//   Carrier PCB (default build, env esp32-c3-supermini):
//     MPU-6050 (I2C): SDA->GPIO 1, SCL->GPIO 2
//     MicroSD (SPI):  SCK->GPIO 5, MISO->GPIO 0, MOSI->GPIO 6, CS->GPIO 7
//     NEO-6M (GPS):   UART on GPIO 8 / GPIO 21, direction detected at boot
//     Battery:        GPIO 3, only after the J2.5 -> J1.5 bodge
//   Hand-wired prototype (env handwired):
//     MPU-6050 (I2C): SDA->GPIO 6, SCL->GPIO 7
//     MicroSD (SPI):  SCK->GPIO 4, MISO->GPIO 5, MOSI->GPIO 3, CS->GPIO 2
//     NEO-6M (GPS):   UART on GPIO 10 / GPIO 1, direction detected at boot
//     Battery:        GPIO 0
//
// Build: PlatformIO (firmware/platformio.ini).

#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// Mozilla root bundle shipped with the Arduino-ESP32 core. Pinning a leaf
// certificate would break every 90 days when it rotates; the bundle does not.
extern const uint8_t rootca_crt_bundle_start[] asm("_binary_x509_crt_bundle_start");
#include <TinyGPS++.h>
#include <vector>

// ---------- CONFIGURATION ----------
#if __has_include("private_credentials.h")
#include "private_credentials.h"
#else
#define WIFI_SSID "YourHomeWiFi"        // Fallback SSID
#define WIFI_PASS "YourPassword"        // Fallback password
#define SERVER_HOST "kiel.earth"
#define SERVER_PORT 443
#define SERVER_UPLOAD_PATH "/api/bike/ingest"
#define DEVICE_TOKEN "set_me_in_private_credentials_h"
#endif

#ifndef SERVER_HOST
#define SERVER_HOST "kiel.earth"
#endif

#ifndef SERVER_PORT
#define SERVER_PORT 443
#endif

#ifndef SERVER_UPLOAD_PATH
#define SERVER_UPLOAD_PATH "/api/bike/ingest"
#endif

#ifndef DEVICE_TOKEN
#define DEVICE_TOKEN "set_me_in_private_credentials_h"
#endif

// ---------- PIN MAP ----------
// The carrier PCB's J1/J2 were routed against a SuperMini pinout that does not
// exist, so every signal lands on a different GPIO than the hand-wired prototype
// used. The C3's GPIO matrix lets SPI, I2C and UART sit on any pin, so the
// firmware absorbs it; only the battery ADC needed a bodge.
// See custom_pcb/PIN_VERIFICATION.md.
#ifdef BIKESENSOR_HANDWIRED
static constexpr uint8_t PIN_SPI_SCK  = 4;
static constexpr uint8_t PIN_SPI_MISO = 5;
static constexpr uint8_t PIN_SPI_MOSI = 3;
static constexpr uint8_t PIN_SPI_CS   = 2;
static constexpr uint8_t PIN_I2C_SDA  = 6;
static constexpr uint8_t PIN_I2C_SCL  = 7;
// The two pins wired to the GPS UART. Which one carries the GPS's TX is
// detected at boot, since breakouts ship with either header order.
static constexpr uint8_t PIN_GPS_A    = 10;
static constexpr uint8_t PIN_GPS_B    = 1;
static constexpr uint8_t PIN_BATTERY  = 0;
static constexpr int8_t  PIN_LED      = 8;  // onboard blue LED, active-low
#else
static constexpr uint8_t PIN_SPI_SCK  = 5;  // J2.1
static constexpr uint8_t PIN_SPI_MISO = 0;  // J1.8
static constexpr uint8_t PIN_SPI_MOSI = 6;  // J2.2
static constexpr uint8_t PIN_SPI_CS   = 7;  // J2.3, R3 pull-up
static constexpr uint8_t PIN_I2C_SDA  = 1;  // J1.7
static constexpr uint8_t PIN_I2C_SCL  = 2;  // J1.6
static constexpr uint8_t PIN_GPS_A    = 21; // J2.8 -> J5.3
static constexpr uint8_t PIN_GPS_B    = 8;  // J2.4 -> J5.2
// BATTERY_ADC is routed to J2.5 = GPIO9, which has no ADC. Readings are only
// real after the bodge wire moves the divider to J1.5 = GPIO3.
static constexpr uint8_t PIN_BATTERY  = 3;
static constexpr int8_t  PIN_LED      = -1; // GPIO8 carries the GPS UART here
#endif

// MPU-6050 I2C Address
static constexpr uint8_t MPU_ADDR = 0x68;

// Logging Parameters
// 200 Hz, not 100. Cobblestone excites the frame at (speed / sett pitch): 10cm
// setts at 15 km/h is 42 Hz, at 20 km/h 56 Hz. At 100 Hz sampling the Nyquist
// limit is 50 Hz, so the most common German Kleinpflaster aliases away at normal
// riding speed and no amount of processing recovers it. 200 Hz moves the limit
// to 100 Hz and covers setts down to ~6 cm.
static constexpr uint16_t SAMPLE_RATE_HZ = 200;
static constexpr uint32_t SAMPLE_INTERVAL_MS = 1000 / SAMPLE_RATE_HZ;

// State Variables
File logFile;
char currentRideFilename[32];
bool isLoggingActive = false;

// GPS Object
TinyGPSPlus gps;
HardwareSerial GPSSerial(1); // Use hardware UART1

// ---------- STATUS LED ----------
static void ledSet(bool on) {
  if (PIN_LED >= 0) digitalWrite(PIN_LED, on ? LOW : HIGH); // active-low
}

// ---------- GPS UART ----------
// Listens on one pin with TX left unassigned, so nothing drives a line the GPS
// may itself be driving. The NEO-6M emits sentences every second, fix or not.
static bool gpsTalksOn(uint8_t rxPin, uint32_t baud) {
  static const char kTalker[] = "$GP";
  GPSSerial.begin(baud, SERIAL_8N1, rxPin, -1);
  uint8_t matched = 0;
  bool found = false;
  uint32_t t0 = millis();
  while (!found && millis() - t0 < 1500) {
    while (GPSSerial.available() > 0) {
      char c = GPSSerial.read();
      matched = (c == kTalker[matched]) ? matched + 1 : (c == '$' ? 1 : 0);
      if (matched == 3) { found = true; break; }
    }
    delay(5);
  }
  GPSSerial.end();
  return found;
}

static void gpsBegin() {
  const uint8_t pins[2] = {PIN_GPS_A, PIN_GPS_B};
  // 115200 is what the module was configured to on the prototype; 9600 is the
  // NEO-6M factory default, in case that setting was never saved.
  const uint32_t bauds[2] = {115200, 9600};
  for (uint32_t baud : bauds) {
    for (int i = 0; i < 2; i++) {
      if (gpsTalksOn(pins[i], baud)) {
        GPSSerial.begin(baud, SERIAL_8N1, pins[i], pins[1 - i]);
        Serial.printf("NEO-6M found: GPS TX on GPIO %u, %lu baud.\n", pins[i], (unsigned long)baud);
        return;
      }
    }
  }
  Serial.println("⚠ No NMEA on either GPS pin. Check J5 and GPS power; assuming the default order.");
  GPSSerial.begin(bauds[0], SERIAL_8N1, pins[0], pins[1]);
}

// ---------- BATTERY MEASUREMENT ----------
static uint8_t getBatteryPercent() {
  float mv = analogReadMilliVolts(PIN_BATTERY) * 2.0; 
  float voltage = mv / 1000.0;
  if (voltage >= 4.2) return 100;
  if (voltage <= 3.3) return 0;
  return (uint8_t)(((voltage - 3.3) / (4.2 - 3.3)) * 100.0); 
}

// ---------- MPU-6050 ACCEL ONLY ----------
static void w8(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.write(v);
  Wire.endTransmission();
}

static void mpuInit() {
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  w8(0x6B, 0x80); delay(100); // Reset MPU-6050
  w8(0x6B, 0x01);             // Clock source PLL with X gyro
  w8(0x1A, 0x02);             // DLPF CONFIG: Accel BW = 94Hz — the anti-alias filter
                              // must sit just under Nyquist (100Hz at 200Hz sampling).
                              // Leaving this at 44Hz would throw away exactly the band
                              // that cobblestone lives in.
  w8(0x1C, 0x08);             // ACCEL_CONFIG: Full scale range ±4 g
  Serial.println("MPU-6050 initialized.");
}

static void readAccel(int16_t &ax, int16_t &ay, int16_t &az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B); // Accel data register 59
  Wire.endTransmission(false);
  Wire.requestFrom((int)MPU_ADDR, 6);
  if (Wire.available() >= 6) {
    ax = (Wire.read() << 8) | Wire.read();
    ay = (Wire.read() << 8) | Wire.read();
    az = (Wire.read() << 8) | Wire.read();
  }
}

static String getServerUrl() {
  return String("https://") + SERVER_HOST + SERVER_UPLOAD_PATH;
}

// The ride id is the SD filename without its extension. Deriving it from the
// satellite clock at file creation makes it globally unique and, crucially,
// stable across reboots — so if an upload response is lost, the retry carries
// the same id and the server recognises the replay instead of storing the ride
// twice. Falls back to a hardware random when there is no fix yet.
static void makeRideFilename(char *out, size_t n) {
  if (gps.date.isValid() && gps.time.isValid() && gps.date.year() > 2020) {
    snprintf(out, n, "/r%02d%02d%02d_%02d%02d%02d.csv",
             gps.date.year() % 100, gps.date.month(), gps.date.day(),
             gps.time.hour(), gps.time.minute(), gps.time.second());
  } else {
    snprintf(out, n, "/rboot_%08lx.csv", (unsigned long)esp_random());
  }
}

static String rideIdFromFilename(const String &filename) {
  String id = filename;
  if (id.startsWith("/")) id = id.substring(1);
  int dot = id.lastIndexOf('.');
  if (dot > 0) id = id.substring(0, dot);
  return id;
}

// ---------- WI-FI SYNC FUNCTION ----------
bool attemptWiFiSync() {
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(WIFI_SSID);
  
  // Turn off Wi-Fi sleep mode to prevent connection timeouts during security handshake
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  // Wait up to 6 seconds for Wi-Fi connection with rapid visual LED feedback!
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 12) {
    ledSet(true); // Turn LED ON
    delay(100);
    ledSet(false); // Turn LED OFF
    delay(400);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nWi-Fi connection failed. Starting standalone logging mode!");
    ledSet(false); // Ensure LED is OFF
    WiFi.disconnect(true);
    return false;
  }

  Serial.println("\nConnected to home Wi-Fi!");
  ledSet(true); // Turn LED solid ON to indicate active connected/syncing mode!
  Serial.println("Checking SD card for offline rides to sync...");
  const String serverUrl = getServerUrl();
  Serial.printf("Using upload endpoint: %s\n", serverUrl.c_str());

  // Open the root directory of the SD card to search for pending ride files
  File root = SD.open("/");
  if (!root) {
    Serial.println("Failed to open SD card root directory.");
    return true;
  }

  // 1. Safe Collector Stage: Read filenames first to avoid modifying the directory 
  // structure while iterating, which can corrupt index pointers in the SD library.
  std::vector<String> filesToSync;
  while (true) {
    File entry = root.openNextFile();
    if (!entry) break; // No more files

    String filename = entry.name();
    if (filename.startsWith("ride_") && filename.endsWith(".csv")) {
      filesToSync.push_back(filename);
    }
    entry.close();
  }
  root.close();

  Serial.printf("Found %u unsynced ride(s) on the SD Card.\n", filesToSync.size());

  // 2. Ingestion & Cleanup Stage: Loop through collected filenames to upload and delete sequentially
  for (const String& filename : filesToSync) {
    String path = "/" + filename;
    File entry = SD.open(path.c_str(), FILE_READ);
    if (!entry) {
      Serial.printf("❌ Error: Could not open file for uploading: %s\n", path.c_str());
      continue;
    }

    size_t fileSize = entry.size();
    Serial.printf("Found unsynced ride: %s (%u bytes). Uploading...\n", filename.c_str(), fileSize);
    
    // Safety guard: If the file is completely empty, delete it and skip upload
    if (entry.size() == 0) {
      Serial.printf("File %s is empty (0 bytes). Skipping upload and deleting.\n", filename.c_str());
      entry.close();
      SD.remove(path.c_str());
      continue;
    }
    
    // A fresh TLS client per file. WiFiClientSecure is documented to leak memory
    // when certificate verification FAILS, so the retry path below must not spin
    // on a broken handshake — one attempt per file per sync, then move on.
    WiFiClientSecure client;
    client.setCACertBundle(rootca_crt_bundle_start);
    client.setTimeout(20);

    HTTPClient http;
    http.begin(client, serverUrl);
    http.setTimeout(65000); // large ride logs need a long read timeout
    http.addHeader("Content-Type", "text/csv");
    http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
    http.addHeader("X-Ride-Id", rideIdFromFilename(filename));

    // Stream the file as the body; passing the size avoids chunked encoding,
    // which the ESP32 HTTP client handles poorly for large payloads.
    int httpCode = http.sendRequest("POST", &entry, entry.size());

    // Delete ONLY on an explicit 2xx. The server stores the raw bytes before it
    // acknowledges, so a 2xx means the ride is durable somewhere other than this
    // SD card. Anything else — timeout, TLS failure, 5xx — leaves the file in
    // place for the next sync. Since the ride id is stable, a replay after a lost
    // response is recognised as a duplicate rather than stored twice.
    if (httpCode >= 200 && httpCode < 300) {
      Serial.printf("✨ Uploaded %s: %s\n", filename.c_str(), http.getString().c_str());
      http.end();
      entry.close();
      SD.remove(path.c_str());
      Serial.printf("Deleted synced file: %s\n", path.c_str());
    } else {
      Serial.printf("Upload failed for %s (HTTP %d). Keeping the file for the next sync.\n",
                    filename.c_str(), httpCode);
      http.end();
      entry.close();
    }
  }
  
  Serial.println("Offline ride sync sequence completed.");
  ledSet(false); // Turn LED OFF when sync is fully completed!
  return true;
}

// ---------- STANDALONE LOGGING ----------
void startNewRideLogging() {
  // Names used to be /ride_001.csv, restarting from 1 on every boot — so a new
  // ride could silently overwrite an older, not-yet-uploaded one, and two rides
  // from different boots could share an id. Derived from the satellite clock now.
  makeRideFilename(currentRideFilename, sizeof(currentRideFilename));
  if (SD.exists(currentRideFilename)) {
    // Same second as an existing file (only reachable on a fast reboot): salt it.
    char salted[48];
    snprintf(salted, sizeof(salted), "/%.*s_%04x.csv",
             (int)(strlen(currentRideFilename) - 5), currentRideFilename + 1,
             (unsigned)(esp_random() & 0xffff));
    strncpy(currentRideFilename, salted, sizeof(currentRideFilename) - 1);
    currentRideFilename[sizeof(currentRideFilename) - 1] = '\0';
  }

  Serial.printf("Creating new ride log file: %s\n", currentRideFilename);
  logFile = SD.open(currentRideFilename, FILE_WRITE);
  if (!logFile) {
    Serial.println("❌ ERROR: Failed to create ride file on SD Card!");
    return;
  }

  // Write CSV headers (vibration + in-band GPS + battery + satellite clock columns!)
  logFile.println("millis,ax,ay,az,lat,lon,ele,speed_kmh,battery_pct,gps_time");
  logFile.close(); // Force directory entry size update!
  
  logFile = SD.open(currentRideFilename, FILE_APPEND);
  if (!logFile) {
    Serial.println("❌ ERROR: Failed to reopen ride file in append mode after header creation!");
    return;
  }
  
  isLoggingActive = true;
  Serial.println("Ride logging active. Accelerometer and GPS recording started...");
}

// Latching GPS variables for race-free 1Hz satellite tracking
static uint32_t lastGpsTimeVal = 0;
static bool newGpsDataAvailable = false;
static double lastLat = 0.0;
static double lastLon = 0.0;
static double lastEle = 0.0;
static double lastSpeed = 0.0;
static char lastGpsTime[32] = "";

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("=== BIKESENSOR STANDALONE GPS + SD LOGGER ===");

  // Initialize onboard blue LED immediately and turn it OFF (active-low)
  if (PIN_LED >= 0) pinMode(PIN_LED, OUTPUT);
  ledSet(false);

  // Initialize custom SPI for MicroSD Module
  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
  if (!SD.begin(PIN_SPI_CS)) {
    Serial.println("❌ ERROR: MicroSD card mounting failed! Check wiring.");
    // Hardware Alert: Rapidly flash the LED (100ms ON / 100ms OFF) to signal SD card failure
    while (1) {
      ledSet(true); // ON
      delay(100);
      ledSet(false); // OFF
      delay(100);
    }
  }
  Serial.println("MicroSD card mounted successfully.");

  // Check if we can sync with home Wi-Fi and upload saved rides
  bool synced = attemptWiFiSync();

  // Initialize NEO-6M GPS Module on UART1
  gpsBegin();

  // Initialize I2C bus and MPU-6050
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  
  // Verify that the accelerometer responds over I2C before proceeding
  Wire.beginTransmission(MPU_ADDR);
  if (Wire.endTransmission() != 0) {
    Serial.println("❌ ERROR: MPU-6050 not responding at I2C address 0x68!");
    // Hardware Alert: Double-flash LED rapidly (80ms ON / 80ms OFF) to signal IMU failure
    while (1) {
      ledSet(true); // ON
      delay(80);
      ledSet(false); // OFF
      delay(80);
    }
  }

  w8(0x6B, 0x80); delay(100); // Reset MPU-6050
  w8(0x6B, 0x01);             // Clock source PLL with X gyro
  w8(0x1A, 0x02);             // DLPF CONFIG: Accel BW = 94Hz — the anti-alias filter
                              // must sit just under Nyquist (100Hz at 200Hz sampling).
                              // Leaving this at 44Hz would throw away exactly the band
                              // that cobblestone lives in.
  w8(0x1C, 0x08);             // ACCEL_CONFIG: Full scale range ±4 g
  Serial.println("MPU-6050 initialized.");

  startNewRideLogging();
}

void loop() {
  static bool gpsLockSignal = false;
  uint32_t now = millis();

  // 1. Process the incoming NMEA stream from the GPS module continuously
  while (GPSSerial.available() > 0) {
    gps.encode(GPSSerial.read());
  }

  // 2. Check for a new GPS clock second tick. This is race-free, timing-insensitive,
  // and completely bypasses any TinyGPS++ isUpdated() race conditions.
  if (gps.location.isValid() && gps.date.isValid() && gps.time.isValid()) {
    uint32_t currentGpsTimeVal = gps.time.value(); // Format: HHMMSSCC
    if (currentGpsTimeVal != lastGpsTimeVal) {
      lastGpsTimeVal = currentGpsTimeVal;
      newGpsDataAvailable = true;
      lastLat = gps.location.lat();
      lastLon = gps.location.lng();
      lastEle = gps.altitude.meters();
      lastSpeed = gps.speed.kmph();
      snprintf(lastGpsTime, sizeof(lastGpsTime), "%04d-%02d-%02dT%02d:%02d:%02dZ",
               gps.date.year(), gps.date.month(), gps.date.day(),
               gps.time.hour(), gps.time.minute(), gps.time.second());
    }
  }

  // 3. Periodic battery diagnostic print (every 5 seconds) to Serial console
  static uint32_t lastBattPrintMs = 0;
  if (now - lastBattPrintMs > 5000) {
    lastBattPrintMs = now;
    Serial.printf("[DIAGNOSTIC] Battery ADC: %d mV | Calculated: %u%%\n", analogReadMilliVolts(PIN_BATTERY), getBatteryPercent());
  }

  if (!isLoggingActive) {
    delay(1);
    return;
  }

  // 4. Strict 100Hz periodic sampling with phase-drift correction
  static uint32_t lastSampleMs = 0;
  if (lastSampleMs == 0) {
    lastSampleMs = now;
  }

  if (now - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    // Phase handling. The old version added one interval whenever it was behind,
    // which meant that after an SD stall several loop iterations fired in quick
    // succession — samples taken 1-2 ms apart but spaced as if they were regular.
    // That silently violates the uniform-rate assumption the whole FFT pipeline
    // rests on, and it is invisible in the data.
    //
    // Now: if more than one slot was missed, give up on catching up and resync
    // the phase. That turns a stall into a clean gap in the millis column, which
    // the DSP detects and splits the ride on, instead of into bunched samples it
    // cannot detect at all.
    if (now - lastSampleMs >= 2 * SAMPLE_INTERVAL_MS) {
      lastSampleMs = now;
    } else {
      lastSampleMs += SAMPLE_INTERVAL_MS;
    }
    
    int16_t ax, ay, az;
    readAccel(ax, ay, az);

    // Format and write the data row directly as CSV
    if (newGpsDataAvailable) {
      newGpsDataAvailable = false; // Reset the latch
      uint8_t batt = getBatteryPercent(); // Live analog battery level
      
      logFile.printf("%lu,%d,%d,%d,%.6f,%.6f,%.1f,%.2f,%u,%s\n", now, ax, ay, az, lastLat, lastLon, lastEle, lastSpeed, batt, lastGpsTime);
      
      // Trigger non-blocking double-blink signal to indicate satellite lock
      gpsLockSignal = true;
    } else {
      // Print empty commas for GPS, battery, and clock columns when there is no new coordinate fix
      logFile.printf("%lu,%d,%d,%d,,,,,,\n", now, ax, ay, az);
    }

    // Periodic close and reopen (syncing FAT directory entry file size) to prevent data loss in case of sudden power cutoff
    static uint32_t lastFlushMs = 0;
    if (now - lastFlushMs > 5000) {
      logFile.close();
      logFile = SD.open(currentRideFilename, FILE_APPEND);
      if (!logFile) {
        Serial.println("❌ ERROR: Failed to reopen ride file in append mode!");
      }
      lastFlushMs = now;
    }
  }

  // 5. Visual LED Indicator system on the onboard LED (2-Second Heartbeat vs. Rapid GPS Lock Double-Blink)
  static uint32_t lastLEDMs = 0;
  static int blinkPhase = 0; // 0 = idle, 1 = first blink on, 2 = first blink off, 3 = second blink on
  
  if (isLoggingActive) {
    if (gpsLockSignal) {
      gpsLockSignal = false;
      blinkPhase = 1;
      lastLEDMs = now;
      ledSet(true); // Start first flash of GPS lock double-blink
    }
    
    if (blinkPhase > 0) {
      // Non-blocking double-blink animation for GPS lock:
      // Phase 1 (ON): 15ms -> Phase 2 (OFF): 80ms -> Phase 3 (ON): 15ms -> Phase 0 (Idle)
      if (blinkPhase == 1 && now - lastLEDMs > 15) {
        ledSet(false); // OFF
        blinkPhase = 2;
        lastLEDMs = now;
      } else if (blinkPhase == 2 && now - lastLEDMs > 80) {
        ledSet(true); // ON
        blinkPhase = 3;
        lastLEDMs = now;
      } else if (blinkPhase == 3 && now - lastLEDMs > 15) {
        ledSet(false); // OFF
        blinkPhase = 0;        // Animation complete
      }
    } else {
      // Idle phase: Slow steady 2-second heartbeat logging blink (short 15ms pulse)
      static uint32_t lastHeartbeatMs = 0;
      static bool ledHeartbeatState = false;
      if (now - lastHeartbeatMs > 2000) {
        ledSet(true); // ON
        lastHeartbeatMs = now;
        ledHeartbeatState = true;
      }
      if (ledHeartbeatState && now - lastHeartbeatMs > 15) {
        ledSet(false); // OFF
        ledHeartbeatState = false;
      }
    }
  }

  delay(1); // keeps loop snappy
}
