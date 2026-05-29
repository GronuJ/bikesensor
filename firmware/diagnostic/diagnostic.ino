#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <TinyGPS++.h>

// SPI Pins for MicroSD Card Reader
static constexpr uint8_t PIN_SPI_SCK  = 4;
static constexpr uint8_t PIN_SPI_MISO = 5;
static constexpr uint8_t PIN_SPI_MOSI = 3;
static constexpr uint8_t PIN_SPI_CS   = 2;

// UART Pins for NEO-6M GPS Module
static constexpr uint8_t PIN_GPS_RX   = 10; // Connects to GPS TX
static constexpr uint8_t PIN_GPS_TX   = 1;  // Connects to GPS RX
static constexpr uint8_t MPU_ADDR = 0x68;
static constexpr uint8_t PIN_BATTERY = 0;

TinyGPSPlus gps;
HardwareSerial GPSSerial(1);

float getBatteryVoltage() {
  float mv = analogReadMilliVolts(PIN_BATTERY) * 2.0; 
  return mv / 1000.0;
}

uint8_t getBatteryPercent(float voltage) {
  if (voltage >= 4.2) return 100;
  if (voltage <= 3.3) return 0;
  return (uint8_t)(((voltage - 3.3) / (4.2 - 3.3)) * 100.0); 
}

void mpuWrite(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.write(v);
  Wire.endTransmission();
}

bool mpuInit() {
  Wire.begin(6, 7, 100000); // SDA=GPIO 6, SCL=GPIO 7
  
  // Test connection
  Wire.beginTransmission(MPU_ADDR);
  if (Wire.endTransmission() != 0) {
    return false;
  }
  
  mpuWrite(0x6B, 0x80); delay(100); // Reset
  mpuWrite(0x6B, 0x01);             // Clock source PLL
  mpuWrite(0x1A, 0x03);             // DLPF CONFIG
  mpuWrite(0x1C, 0x08);             // ACCEL_CONFIG ±4 g
  return true;
}

void readAccel(int16_t &ax, int16_t &ay, int16_t &az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom((int)MPU_ADDR, 6);
  if (Wire.available() >= 6) {
    ax = (Wire.read() << 8) | Wire.read();
    ay = (Wire.read() << 8) | Wire.read();
    az = (Wire.read() << 8) | Wire.read();
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("==========================================");
  Serial.println("=== BIKESENSOR DIAGNOSTIC TOOL (WITH SD) ===");
  Serial.println("==========================================");
  
  // 1. MPU-6050
  if (mpuInit()) {
    Serial.println("[SUCCESS] MPU-6050 found and initialized.");
  } else {
    Serial.println("[WARNING] MPU-6050 NOT found at 0x68. Check SDA (GPIO 6) and SCL (GPIO 7) wiring.");
  }
  
  // 2. GPS
  GPSSerial.begin(115200, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  Serial.println("[INFO] NEO-6M GPS serial interface started on UART1 (115200 baud).");
  
  // 3. MicroSD Card SPI test
  Serial.println("[INFO] Initializing custom SPI for MicroSD Card...");
  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
  if (SD.begin(PIN_SPI_CS)) {
    Serial.println("[SUCCESS] MicroSD card mounted successfully.");
    
    // Try to open a file for writing
    File testFile = SD.open("/test.txt", FILE_WRITE);
    if (testFile) {
      testFile.println("Bikesensor SD card SPI write/read test successful!");
      testFile.close();
      Serial.println("[SUCCESS] Wrote test file to SD card.");
      
      // Try to read it back
      File readFile = SD.open("/test.txt", FILE_READ);
      if (readFile) {
        Serial.print("[SUCCESS] Read from SD card: ");
        while (readFile.available()) {
          Serial.write(readFile.read());
        }
        readFile.close();
        
        // Clean up
        SD.remove("/test.txt");
        Serial.println("[SUCCESS] Cleaned up test file from SD card.");
      } else {
        Serial.println("[ERROR] Failed to read test file from SD card.");
      }
    } else {
      Serial.println("[ERROR] Failed to write test file to SD card.");
    }
  } else {
    Serial.println("[ERROR] MicroSD card mounting failed! Check wiring (CS=2, MOS=3, SCK=4, MISO=5).");
  }
}

void loop() {
  // Read GPS serial continuously
  while (GPSSerial.available() > 0) {
    char c = GPSSerial.read();
    gps.encode(c);
  }
  
  static uint32_t lastPrintMs = 0;
  uint32_t now = millis();
  
  if (now - lastPrintMs >= 1000) {
    lastPrintMs = now;
    
    Serial.println("\n--- DIAGNOSTIC SAMPLE ---");
    
    // Battery
    float battVolts = getBatteryVoltage();
    uint8_t battPct = getBatteryPercent(battVolts);
    Serial.printf("Battery: %.2fV (%u%%)\n", battVolts, battPct);
    
    // Accel
    int16_t ax = 0, ay = 0, az = 0;
    readAccel(ax, ay, az);
    // Convert to Gs (full scale ±4g -> sensitivity is 8192 LSB/g)
    float gax = ax / 8192.0;
    float gay = ay / 8192.0;
    float gaz = az / 8192.0;
    Serial.printf("MPU-6050 Accel: ax=%d (%.3fg), ay=%d (%.3fg), az=%d (%.3fg)\n", ax, gax, ay, gay, az, gaz);
    
    // GPS Status
    if (gps.satellites.isValid()) {
      Serial.printf("GPS Satellites: %u\n", gps.satellites.value());
    } else {
      Serial.println("GPS Satellites: Unknown");
    }
    
    if (gps.location.isValid()) {
      Serial.printf("GPS Location: Lat=%.6f, Lng=%.6f, Alt=%.1fm, Speed=%.2f km/h\n", 
                    gps.location.lat(), gps.location.lng(), gps.altitude.meters(), gps.speed.kmph());
    } else {
      Serial.println("GPS Location: No lock (Wait for outdoor view/clear sky)");
    }
    
    // NMEA debug
    Serial.printf("GPS Sentences Processed: %u, Failed Checksums: %u\n", 
                  gps.charsProcessed(), gps.failedChecksum());
  }
}
