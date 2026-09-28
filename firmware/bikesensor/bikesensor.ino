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
// Behaviour: at boot, and after every ride, it tries home Wi-Fi, uploads every
// ride file on the SD card and switches Wi-Fi off again. A ride starts when the
// bike has been moving for a few seconds and ends after three still minutes;
// nothing is written while the bike is parked.
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
static constexpr uint32_t SAMPLE_INTERVAL_US = 1000000UL / SAMPLE_RATE_HZ;

static const char CSV_HEADER[] = "millis,ax,ay,az,lat,lon,ele,speed_kmh,battery_pct,gps_time";

// Ride detection. A ride starts after MOVE_START_S consecutive moving seconds
// and ends after STILL_END_S consecutive still seconds; rides with fewer than
// MIN_RIDE_MOVING_S moving seconds (wheeling the bike out of the hallway) are
// deleted instead of uploaded. A second counts as moving when the accelerometer
// is busy or the GPS says we are rolling.
//
// MOTION_THRESHOLD is PROVISIONAL, reasoned rather than measured: the metric is
// the mean |sample-to-sample change| summed over the three axes, in raw counts.
// MPU-6050 noise at the 94 Hz DLPF is ~4 mg rms per axis, which gives ~110 at
// rest; smooth asphalt at 0.02 g rms gives several hundred. The serial console
// prints the metric every 5 s — calibrate from a real ride.
static constexpr uint32_t MOTION_THRESHOLD   = 300;
static constexpr float    GPS_MOVING_KMH     = 8.0;
static constexpr float    GPS_STILL_KMH      = 3.0;
static constexpr uint16_t MOVE_START_S       = 3;
static constexpr uint16_t STILL_END_S        = 180;  // longer than any red light
static constexpr uint16_t MIN_RIDE_MOVING_S  = 60;

// A position older than this is not written. TinyGPS++ keeps location.isValid()
// true forever after the first fix and keeps committing the time without one, so
// without an age check a lost fix repeats the last position with fresh times.
static constexpr uint32_t FIX_MAX_AGE_MS = 1500;

// The battery shield's divider halves the cell voltage. A reading outside
// BATTERY_MIN_V..BATTERY_MAX_V is not a Li-ion cell — typically the carrier
// without its GPIO9 -> GPIO3 bodge, where the ADC pin floats — so the column is
// left empty rather than filled with a made-up percentage.
static constexpr float BATTERY_DIVIDER = 2.0;
static constexpr float BATTERY_MIN_V   = 2.8;
static constexpr float BATTERY_MAX_V   = 4.5;

// State Variables
File logFile;
char currentRideFilename[32];
bool isLoggingActive = false;
static bool sdReady = false;
static bool imuReady = false;

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
// Returns -1 when the reading is not plausibly a cell (see BATTERY_MIN_V).
static int getBatteryPercent() {
  uint32_t mv = 0;
  for (int i = 0; i < 8; i++) mv += analogReadMilliVolts(PIN_BATTERY);
  float voltage = (mv / 8.0) * BATTERY_DIVIDER / 1000.0;
  if (voltage < BATTERY_MIN_V || voltage > BATTERY_MAX_V) return -1;
  if (voltage >= 4.2) return 100;
  if (voltage <= 3.3) return 0;
  return (int)(((voltage - 3.3) / (4.2 - 3.3)) * 100.0);
}

// ---------- MPU-6050 ACCEL ONLY ----------
static void w8(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.write(v);
  Wire.endTransmission();
}

// Probes and configures the IMU. Called at boot and again whenever it has gone
// missing, so a loose header costs a gap in the data rather than the ride.
static bool mpuInit() {
  Wire.beginTransmission(MPU_ADDR);
  if (Wire.endTransmission() != 0) return false;
  w8(0x6B, 0x80); delay(100); // Reset MPU-6050
  w8(0x6B, 0x01);             // Clock source PLL with X gyro
  w8(0x1A, 0x02);             // DLPF CONFIG: Accel BW = 94Hz — the anti-alias filter
                              // must sit just under Nyquist (100Hz at 200Hz sampling).
                              // Leaving this at 44Hz would throw away exactly the band
                              // that cobblestone lives in.
  w8(0x1C, 0x08);             // ACCEL_CONFIG: Full scale range ±4 g
  Serial.println("MPU-6050 initialized.");
  return true;
}

// False when the IMU did not answer; the caller must not use ax/ay/az then.
static bool readAccel(int16_t &ax, int16_t &ay, int16_t &az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B); // Accel data register 59
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)MPU_ADDR, 6) != 6 || Wire.available() < 6) return false;
  ax = (Wire.read() << 8) | Wire.read();
  ay = (Wire.read() << 8) | Wire.read();
  az = (Wire.read() << 8) | Wire.read();
  return true;
}

// ---------- MICROSD ----------
static bool mountSd() {
  if (!SD.begin(PIN_SPI_CS)) return false;
  Serial.println("MicroSD card mounted successfully.");
  return true;
}

static String getServerUrl() {
  return String("https://") + SERVER_HOST + SERVER_UPLOAD_PATH;
}

// The ride id is the SD filename without its extension, fixed when the file is
// created. Because it lives in the filename it is stable across reboots — so if
// an upload response is lost, the retry carries the same id and the server
// recognises the replay instead of storing the ride twice. Rides start on
// motion, usually after the GPS has its clock, so the name is normally the start
// time; without a GPS clock it falls back to a hardware random.
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
static void uploadPendingRides() {
  Serial.println("Checking SD card for offline rides to sync...");
  const String serverUrl = getServerUrl();
  Serial.printf("Using upload endpoint: %s\n", serverUrl.c_str());

  // Open the root directory of the SD card to search for pending ride files
  File root = SD.open("/");
  if (!root) {
    Serial.println("Failed to open SD card root directory.");
    return;
  }

  // 1. Safe Collector Stage: Read filenames first to avoid modifying the directory
  // structure while iterating, which can corrupt index pointers in the SD library.
  std::vector<String> filesToSync;
  while (true) {
    File entry = root.openNextFile();
    if (!entry) break; // No more files

    // Every ride file makeRideFilename() can produce starts with "r": r<date>_<time>.csv,
    // its salted variant, and rboot_<hex>.csv. The old ride_NNN.csv names match too,
    // so a card logged by older firmware still drains. This used to require "ride_",
    // which silently skipped every ride once the names changed.
    String filename = entry.name();
    if (!entry.isDirectory() && filename.startsWith("r") && filename.endsWith(".csv")) {
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

    // Safety guard: a file holding at most the header has no ride in it
    // (power cut right after it was created). Delete it and skip the upload.
    if (fileSize <= sizeof(CSV_HEADER) + 1) {
      Serial.printf("File %s has no data rows. Skipping upload and deleting.\n", filename.c_str());
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
    int httpCode = http.sendRequest("POST", &entry, fileSize);

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
}

// Runs at boot and after every ride. Wi-Fi is switched fully off before this
// returns, whatever happened: left on, it drew ~80-100 mA for the whole ride and
// its reconnect attempts competed with the sampling loop on the single core.
void attemptWiFiSync() {
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(WIFI_SSID);

  // Turn off Wi-Fi sleep mode to prevent connection timeouts during security handshake
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  // Wait up to 10 seconds for Wi-Fi connection with rapid visual LED feedback
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    ledSet(true); // Turn LED ON
    delay(100);
    ledSet(false); // Turn LED OFF
    delay(400);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nWi-Fi connection failed. Not at home; rides stay on the SD card.");
  } else {
    Serial.println("\nConnected to home Wi-Fi!");
    ledSet(true); // Turn LED solid ON to indicate active connected/syncing mode!
    uploadPendingRides();
  }

  ledSet(false);
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  Serial.println("Wi-Fi off.");
}

// ---------- RIDE FILES ----------
void startNewRideLogging() {
  makeRideFilename(currentRideFilename, sizeof(currentRideFilename));
  if (SD.exists(currentRideFilename)) {
    // Same second as an existing file (a ride ending and restarting within a
    // second, or two random names colliding): salt it.
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

  logFile.println(CSV_HEADER);
  logFile.flush();

  isLoggingActive = true;
  Serial.println("Ride logging active. Accelerometer and GPS recording started...");
}

static void endRideLogging(uint32_t movingSeconds) {
  logFile.close();
  isLoggingActive = false;
  if (movingSeconds < MIN_RIDE_MOVING_S) {
    SD.remove(currentRideFilename);
    Serial.printf("Ride %s had only %u moving seconds; deleted.\n", currentRideFilename, movingSeconds);
  } else {
    Serial.printf("Ride %s ended after %u moving seconds.\n", currentRideFilename, movingSeconds);
  }
}

// Latched once per GPS second, written with the next accelerometer row.
static uint32_t lastGpsTimeVal = 0;
static bool newGpsDataAvailable = false;
static bool lastFixFresh = false;
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

  // Nothing below hangs on missing hardware: a missing SD card or IMU is
  // retried from loop(), so reseating it recovers without a power cycle.
  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
  sdReady = mountSd();
  if (!sdReady) {
    Serial.println("❌ ERROR: MicroSD card mounting failed! Check the card; retrying every 5 s.");
  } else {
    attemptWiFiSync();
  }

  // Initialize NEO-6M GPS Module on UART1
  gpsBegin();

  // A stuck bus must not stall the 5 ms loop for the core's 50 ms default.
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  Wire.setTimeOut(10);
  imuReady = mpuInit();
  if (!imuReady) {
    Serial.println("❌ ERROR: MPU-6050 not responding at I2C address 0x68! Retrying every second.");
  }
}

void loop() {
  uint32_t now = millis();
  uint32_t nowUs = micros();

  // 1. Process the incoming NMEA stream from the GPS module continuously
  while (GPSSerial.available() > 0) {
    gps.encode(GPSSerial.read());
  }

  // 2. Latch once per GPS clock second. The time keeps ticking without a fix, so
  // the row still carries the battery and gps_time; the position fields are
  // written only when the fix is current.
  if (gps.time.isValid()) {
    uint32_t currentGpsTimeVal = gps.time.value(); // Format: HHMMSSCC
    if (currentGpsTimeVal != lastGpsTimeVal) {
      lastGpsTimeVal = currentGpsTimeVal;
      newGpsDataAvailable = true;
      lastFixFresh = gps.location.isValid() && gps.location.age() < FIX_MAX_AGE_MS;
      lastLat = gps.location.lat();
      lastLon = gps.location.lng();
      lastEle = gps.altitude.meters();
      lastSpeed = gps.speed.kmph();
      if (gps.date.isValid() && gps.date.year() > 2020) {
        snprintf(lastGpsTime, sizeof(lastGpsTime), "%04d-%02d-%02dT%02d:%02d:%02dZ",
                 gps.date.year(), gps.date.month(), gps.date.day(),
                 gps.time.hour(), gps.time.minute(), gps.time.second());
      } else {
        lastGpsTime[0] = '\0';
      }
    }
  }

  // 3. Missing hardware is retried rather than fatal.
  static uint32_t lastSdRetryMs = 0;
  if (!sdReady && now - lastSdRetryMs > 5000) {
    lastSdRetryMs = now;
    SD.end();
    sdReady = mountSd();
  }
  static uint32_t lastImuRetryMs = 0;
  if (!imuReady && now - lastImuRetryMs > 1000) {
    lastImuRetryMs = now;
    imuReady = mpuInit();
  }

  // 4. Strict SAMPLE_RATE_HZ sampling, scheduled in microseconds so millis()'s
  // 1 ms steps do not add jitter. A late sample is caught up only if it is less
  // than half a period late. Anything later resyncs the phase, which turns a
  // stall into a clean gap in the millis column that the DSP splits on, instead
  // of two samples bunched a millisecond or two apart that it cannot detect.
  static uint32_t nextSampleUs = 0;
  static bool scheduled = false;
  if (!scheduled) { nextSampleUs = nowUs; scheduled = true; }

  // Ride detection accumulators, evaluated once a second.
  static uint32_t motionSum = 0, motionCount = 0;
  static int16_t pax = 0, pay = 0, paz = 0;
  static bool havePrev = false;
  static uint32_t lastMotionMetric = 0;
  static uint16_t movingStreak = 0, stillStreak = 0;
  static uint32_t rideMovingSeconds = 0;
  static uint32_t imuFailStreak = 0;
  static uint32_t writeFailStreak = 0;

  if ((int32_t)(nowUs - nextSampleUs) >= 0) {
    if (nowUs - nextSampleUs > SAMPLE_INTERVAL_US / 2) {
      nextSampleUs = nowUs + SAMPLE_INTERVAL_US;
    } else {
      nextSampleUs += SAMPLE_INTERVAL_US;
    }

    int16_t ax = 0, ay = 0, az = 0;
    bool accelOk = imuReady && readAccel(ax, ay, az);
    if (accelOk) {
      imuFailStreak = 0;
      if (havePrev) {
        motionSum += abs(ax - pax) + abs(ay - pay) + abs(az - paz);
        motionCount++;
      }
      pax = ax; pay = ay; paz = az; havePrev = true;
    } else if (imuReady && ++imuFailStreak >= SAMPLE_RATE_HZ) {
      // A whole second of failed reads: treat the IMU as gone and re-probe it.
      Serial.println("❌ MPU-6050 stopped answering; re-probing every second.");
      imuReady = false;
      havePrev = false;
    }

    if (isLoggingActive &&
        (accelOk || (newGpsDataAvailable && (lastFixFresh || lastGpsTime[0])))) {
      // A failed accelerometer read writes no 200 Hz row (a gap the DSP splits
      // on), but a GPS-second row is still written with ax/ay/az left empty.
      char row[160];
      int n = snprintf(row, sizeof(row), "%lu,", (unsigned long)now);
      if (accelOk) n += snprintf(row + n, sizeof(row) - n, "%d,%d,%d", ax, ay, az);
      else         n += snprintf(row + n, sizeof(row) - n, ",,");
      if (newGpsDataAvailable) {
        int batt = getBatteryPercent();
        char battStr[8] = "";
        if (batt >= 0) snprintf(battStr, sizeof(battStr), "%d", batt);
        if (lastFixFresh) {
          n += snprintf(row + n, sizeof(row) - n, ",%.6f,%.6f,%.1f,%.2f,%s,%s\n",
                        lastLat, lastLon, lastEle, lastSpeed, battStr, lastGpsTime);
        } else {
          n += snprintf(row + n, sizeof(row) - n, ",,,,,%s,%s\n", battStr, lastGpsTime);
        }
      } else {
        n += snprintf(row + n, sizeof(row) - n, ",,,,,,\n");
      }
      if (logFile.write((const uint8_t *)row, n) == (size_t)n) {
        writeFailStreak = 0;
      } else if (++writeFailStreak >= SAMPLE_RATE_HZ) {
        // A second of failed writes: the card is gone. Keep what was written.
        Serial.println("❌ Writes to the SD card are failing; ending the ride and remounting.");
        logFile.close();
        isLoggingActive = false;
        sdReady = false;
        writeFailStreak = 0;
      }
    }
    newGpsDataAvailable = false;

    // Put what is written on the card every 5 s. flush() writes the buffer and
    // syncs the FAT size (fsync), so a power cut loses at most 5 s. It replaces a
    // close/reopen, which also rewrote the directory entry and took longer.
    static uint32_t lastFlushMs = 0;
    if (isLoggingActive && now - lastFlushMs > 5000) {
      logFile.flush();
      lastFlushMs = now;
    }
  }

  // 5. Once a second: is the bike moving? Start or end the ride.
  static uint32_t lastMotionEvalMs = 0;
  if (now - lastMotionEvalMs >= 1000) {
    lastMotionEvalMs = now;
    lastMotionMetric = motionCount ? motionSum / motionCount : 0;
    motionSum = 0; motionCount = 0;

    bool gpsFresh = gps.location.isValid() && gps.location.age() < FIX_MAX_AGE_MS &&
                    gps.speed.isValid() && gps.speed.age() < FIX_MAX_AGE_MS;
    float speed = gpsFresh ? gps.speed.kmph() : 0.0;
    bool moving = lastMotionMetric > MOTION_THRESHOLD || speed >= GPS_MOVING_KMH;
    bool still  = lastMotionMetric <= MOTION_THRESHOLD && speed < GPS_STILL_KMH;

    if (!isLoggingActive) {
      movingStreak = moving ? movingStreak + 1 : 0;
      if (movingStreak >= MOVE_START_S && sdReady) {
        movingStreak = 0; stillStreak = 0; rideMovingSeconds = 0;
        startNewRideLogging();
      }
    } else {
      if (moving) rideMovingSeconds++;
      stillStreak = still ? stillStreak + 1 : 0;
      if (stillStreak >= STILL_END_S) {
        stillStreak = 0;
        endRideLogging(rideMovingSeconds);
        // Parked: if this is home, upload now rather than at the next power-on.
        attemptWiFiSync();
        nextSampleUs = micros();
        havePrev = false;
        lastMotionEvalMs = millis();
      }
    }
  }

  // 6. Periodic diagnostic print (every 5 seconds) to Serial console
  static uint32_t lastDiagPrintMs = 0;
  if (now - lastDiagPrintMs > 5000) {
    lastDiagPrintMs = now;
    // -1 means "not a plausible cell" (no battery, or no bodge wire): the CSV
    // leaves the field empty, so the console says so instead of printing -1%.
    int batt = getBatteryPercent();
    char battStr[16] = "no battery";
    if (batt >= 0) snprintf(battStr, sizeof(battStr), "%d%%", batt);
    Serial.printf("[DIAGNOSTIC] %s | motion %lu (threshold %lu) | GPS %s | battery ADC %d mV = %s | SD %s | IMU %s\n",
                  isLoggingActive ? "RIDING" : "idle",
                  (unsigned long)lastMotionMetric, (unsigned long)MOTION_THRESHOLD,
                  gps.location.isValid() && gps.location.age() < FIX_MAX_AGE_MS ? "fix" : "no fix",
                  analogReadMilliVolts(PIN_BATTERY), battStr,
                  sdReady ? "ok" : "MISSING", imuReady ? "ok" : "MISSING");
  }

  // 7. Onboard LED (prototype only; the carrier has none): fast blink while the
  // SD card or IMU is missing, a short pulse every 2 s while recording.
  static uint32_t lastLEDMs = 0;
  static bool ledOn = false;
  if (!sdReady || !imuReady) {
    if (now - lastLEDMs > 100) { ledOn = !ledOn; ledSet(ledOn); lastLEDMs = now; }
  } else if (isLoggingActive) {
    if (!ledOn && now - lastLEDMs > 2000) { ledSet(true); ledOn = true; lastLEDMs = now; }
    else if (ledOn && now - lastLEDMs > 15) { ledSet(false); ledOn = false; }
  } else if (ledOn) {
    ledSet(false); ledOn = false;
  }

  delay(1); // keeps loop snappy
}
