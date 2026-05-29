// ESP32-C3 Pure IoT Standalone Bike Mapping Logger & Wi-Fi Sync.
//
// Wiring (ESP32-C3 SuperMini):
//   MPU-6050 (I2C): SDA->GPIO 6, SCL->GPIO 7
//   MicroSD (SPI):  VCC->3V3, GND->GND, SCK->GPIO 4, MISO->GPIO 5, MOSI->GPIO 3, CS->GPIO 2
//   NEO-6M (GPS):   VCC->5V, GND->GND, TX->GPIO 10 (ESP32 RX), RX->GPIO 1 (ESP32 TX)
//
// Build: PlatformIO (firmware/platformio.ini), env esp32-c3-supermini.

#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <TinyGPS++.h>
#include <vector>

// ---------- CONFIGURATION ----------
#if __has_include("private_credentials.h")
#include "private_credentials.h"
#else
static const char* WIFI_SSID = "YourHomeWiFi";       // Fallback SSID
static const char* WIFI_PASS = "YourPassword";       // Fallback Password
#endif

// Raspberry Pi 3 B+ Local Server Ingestion Endpoint
static const char* SERVER_URL = "http://192.168.0.71:8000/api/upload-offline"; 

// SPI Pins for MicroSD Card Reader
static constexpr uint8_t PIN_SPI_SCK  = 4;
static constexpr uint8_t PIN_SPI_MISO = 5;
static constexpr uint8_t PIN_SPI_MOSI = 3;
static constexpr uint8_t PIN_SPI_CS   = 2;

// UART Pins for NEO-6M GPS Module
static constexpr uint8_t PIN_GPS_RX   = 10; // Connects to GPS TX
static constexpr uint8_t PIN_GPS_TX   = 1;  // Connects to GPS RX

// MPU-6050 I2C Address
static constexpr uint8_t MPU_ADDR = 0x68;

// Battery Divider Pin
static constexpr uint8_t PIN_BATTERY = 0;

// Logging Parameters
static constexpr uint16_t SAMPLE_RATE_HZ = 100; // 100 Hz vibration sampling is ideal for road surface PSD
static constexpr uint32_t SAMPLE_INTERVAL_MS = 1000 / SAMPLE_RATE_HZ;

// State Variables
File logFile;
char currentRideFilename[32];
bool isLoggingActive = false;

// GPS Object
TinyGPSPlus gps;
HardwareSerial GPSSerial(1); // Use hardware UART1

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
  Wire.begin(6, 7, 400000); // SDA=GPIO 6, SCL=GPIO 7
  w8(0x6B, 0x80); delay(100); // Reset MPU-6050
  w8(0x6B, 0x01);             // Clock source PLL with X gyro
  w8(0x1A, 0x03);             // DLPF CONFIG: Accel BW = 44Hz (Ideal lowpass filter for 100Hz sampling)
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

// ---------- WI-FI SYNC FUNCTION ----------
bool attemptWiFiSync() {
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(WIFI_SSID);
  
  // Turn off Wi-Fi sleep mode to prevent connection timeouts during security handshake
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  // Wait up to 10 seconds for Wi-Fi connection
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nWi-Fi connection failed. Starting standalone logging mode!");
    WiFi.disconnect(true);
    return false;
  }

  Serial.println("\nConnected to home Wi-Fi!");
  Serial.println("Checking SD card for offline rides to sync...");

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

    Serial.printf("Found unsynced ride: %s. Uploading...\n", filename.c_str());
    
    // Safety guard: If the file is completely empty, delete it and skip upload
    if (entry.size() == 0) {
      Serial.printf("File %s is empty (0 bytes). Skipping upload and deleting.\n", filename.c_str());
      entry.close();
      SD.remove(path.c_str());
      continue;
    }
    
    HTTPClient http;
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "text/csv");
    http.addHeader("X-Ride-Filename", filename);

    // Read file content and stream it in the HTTP POST request body (pass size to avoid chunked encoding hang)
    int httpCode = http.sendRequest("POST", &entry, entry.size());

    if (httpCode == 200 || httpCode == 201) {
      Serial.printf("✨ Upload success for %s! Server response: %s\n", filename.c_str(), http.getString().c_str());
      http.end();
      entry.close();
      
      // Remove the file on the SD card so we don't upload it again
      SD.remove(path.c_str());
      Serial.printf("Deleted synced file: %s\n", path.c_str());
    } else {
      Serial.printf("Upload failed for %s. HTTP code: %d, Response: %s\n", filename.c_str(), httpCode, http.getString().c_str());
      http.end();
      entry.close();
    }
  }
  
  Serial.println("Offline ride sync sequence completed.");
  return true;
}

// ---------- STANDALONE LOGGING ----------
void startNewRideLogging() {
  // Find a unique ride file name by incrementing indices
  int rideIndex = 1;
  while (true) {
    snprintf(currentRideFilename, sizeof(currentRideFilename), "/ride_%03d.csv", rideIndex);
    if (!SD.exists(currentRideFilename)) {
      break; // Found a unique name!
    }
    rideIndex++;
  }

  Serial.printf("Creating new ride log file: %s\n", currentRideFilename);
  logFile = SD.open(currentRideFilename, FILE_WRITE);
  if (!logFile) {
    Serial.println("❌ ERROR: Failed to create ride file on SD Card!");
    return;
  }

  // Write CSV headers (vibration + in-band GPS + battery columns!)
  logFile.println("millis,ax,ay,az,lat,lon,ele,speed_kmh,battery_pct");
  logFile.flush();
  isLoggingActive = true;
  Serial.println("Ride logging active. Accelerometer and GPS recording started...");
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("=== BIKESENSOR STANDALONE GPS + SD LOGGER ===");

  // Initialize custom SPI for MicroSD Module
  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
  if (!SD.begin(PIN_SPI_CS)) {
    Serial.println("❌ ERROR: MicroSD card mounting failed! Check wiring.");
    while (1) delay(100);
  }
  Serial.println("MicroSD card mounted successfully.");

  // Check if we can sync with home Wi-Fi and upload saved rides
  bool synced = attemptWiFiSync();

  // Initialize NEO-6M GPS Module on UART1 (115200 Baud verified)
  GPSSerial.begin(115200, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  Serial.println("NEO-6M GPS Module Serial Interface Started.");

  // Initialize accelerometer and start logging
  mpuInit();
  startNewRideLogging();
}

void loop() {
  // Process the incoming NMEA stream from the GPS module continuously
  while (GPSSerial.available() > 0) {
    gps.encode(GPSSerial.read());
  }

  if (!isLoggingActive) {
    delay(1);
    return;
  }

  static uint32_t lastSampleMs = 0;
  uint32_t now = millis();

  // Exact periodic sampling interval
  if (now - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = now;
    
    int16_t ax, ay, az;
    readAccel(ax, ay, az);

    // Format and write the data row directly as CSV
    // Format: milliseconds, ax, ay, az, lat, lon, ele, speed_kmh, battery_pct
    // To save huge amounts of SD card space, we only output GPS and battery status
    // when a new valid location fix is received from the satellites!
    if (gps.location.isUpdated() && gps.location.isValid()) {
      double lat = gps.location.lat();
      double lon = gps.location.lng();
      double ele = gps.altitude.meters();
      double speed = gps.speed.kmph(); // TinyGPS++ speed method is kmph()
      uint8_t batt = getBatteryPercent(); // Live analog battery level
      
      logFile.printf("%lu,%d,%d,%d,%.6f,%.6f,%.1f,%.2f,%u\n", now, ax, ay, az, lat, lon, ele, speed, batt);
      
      // Flash the onboard LED (GPIO 8) briefly to indicate satellite lock
      pinMode(8, OUTPUT);
      digitalWrite(8, LOW); delay(5); digitalWrite(8, HIGH);
    } else {
      // Print empty commas for GPS and battery columns when there is no new coordinate fix
      logFile.printf("%lu,%d,%d,%d,,,,,\n", now, ax, ay, az);
    }

    // Periodic flush to prevent data loss in case of sudden power cutoff
    static uint32_t lastFlushMs = 0;
    if (now - lastFlushMs > 5000) {
      logFile.flush();
      lastFlushMs = now;
    }
  }

  delay(1); // keeps loop snappy
}
