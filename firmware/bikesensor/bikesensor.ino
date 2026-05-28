// ESP32-C3 Real-Time GPS & Vibration Serial Debugger.
//
// Wiring (ESP32-C3 SuperMini):
//   MPU-6050 (I2C): SDA->GPIO 6, SCL->GPIO 7
//   NEO-6M (GPS):   VCC->3V3, GND->GND, TX->GPIO 10 (Connect to ESP32 RX), RX->GPIO 1 (Connect to ESP32 TX)
//
// Build: PlatformIO (firmware/platformio.ini), env esp32-c3-supermini.

#include <Arduino.h>
#include <Wire.h>
#include <TinyGPS++.h>

// ---------- CONFIGURATION ----------
static constexpr uint8_t PIN_GPS_RX = 10; // Connect to GPS TX
static constexpr uint8_t PIN_GPS_TX = 1;  // Connect to GPS RX

// MPU-6050 I2C Address
static constexpr uint8_t MPU_ADDR = 0x68;

// GPS Object & Hardware Serial
TinyGPSPlus gps;
HardwareSerial GPSSerial(1); // Use hardware UART1

// ---------- MPU-6050 FUNCTIONS ----------
static void w8(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.write(v);
  Wire.endTransmission();
}

static void mpuInit() {
  Wire.begin(6, 7, 400000); // SDA=GPIO 6, SCL=GPIO 7 on ESP32-C3 SuperMini
  w8(0x6B, 0x80); delay(100); // Reset MPU-6050
  w8(0x6B, 0x01);             // Clock source PLL with X gyro
  w8(0x1A, 0x03);             // DLPF CONFIG: Accel BW = 44Hz
  w8(0x1C, 0x08);             // ACCEL_CONFIG: Full scale range ±4 g
  Serial.println("MPU-6050 accelerometer initialized.");
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

void setup() {
  // Initialize USB Serial Monitor
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n=== BIKESENSOR REAL-TIME GPS & IMU DEBUGGER ===");

  // Initialize NEO-6M GPS Module on UART1 (9600 Baud standard)
  GPSSerial.begin(9600, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  Serial.println("NEO-6M GPS Module Serial Interface Started.");

  // Initialize Accelerometer
  mpuInit();

  Serial.println("\nSetup Complete! Waiting for GPS Satellite Fix...");
  Serial.println("Note: GPS modules can take 2-5 minutes to get a lock indoors. Try placing the antenna near a window!");
  Serial.println("--------------------------------------------------------------------------------------------------");
}

void loop() {
  // Feed incoming NMEA data from the GPS module to TinyGPS++
  while (GPSSerial.available() > 0) {
    gps.encode(GPSSerial.read());
  }

  static uint32_t lastPrintMs = 0;
  uint32_t now = millis();

  // Print diagnostics once per second (1 Hz)
  if (now - lastPrintMs >= 1000) {
    lastPrintMs = now;

    // Read accelerometer
    int16_t ax, ay, az;
    readAccel(ax, ay, az);

    // Convert raw IMU to g-units (±4g scale -> divide by 8192)
    float ax_g = ax / 8192.0;
    float ay_g = ay / 8192.0;
    float az_g = az / 8192.0;

    Serial.print("VIBRATION: ");
    Serial.printf("X: %+6.2fg | Y: %+6.2fg | Z: %+6.2fg  ||  ", ax_g, ay_g, az_g);

    // Print GPS Stats
    Serial.print("GPS: ");
    Serial.printf("Satellites: %2d | Chars: %lu | ", gps.satellites.value(), (unsigned long)gps.charsProcessed());

    if (gps.location.isValid()) {
      double lat = gps.location.lat();
      double lon = gps.location.lng();
      double ele = gps.altitude.meters();
      double speed = gps.speed.kmph();

      Serial.printf("FIX! Lat: %10.6f | Lon: %10.6f | Ele: %5.1fm | Speed: %5.1f km/h\n", 
                    lat, lon, ele, speed);
      
      // Briefly flash onboard LED (GPIO 8) to indicate valid GPS fix
      pinMode(8, OUTPUT);
      digitalWrite(8, LOW); delay(10); digitalWrite(8, HIGH);
    } else {
      Serial.println("No Fix (searching satellites...)");
    }
  }

  delay(1); // snaps loop
}
