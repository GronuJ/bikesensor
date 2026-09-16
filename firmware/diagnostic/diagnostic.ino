// BUS-STATE vs SHORT
//
// The short test found GPIO5 (MISO) and GPIO6 (SDA) unable to be driven high.
// Two independent solder faults landing on exactly the two pins that have an
// active device on them is a suspicious coincidence, and there is a much more
// ordinary explanation for each:
//
//   SDA  - classic I2C lockup. If the master reset mid-byte, the MPU is still
//          holding SDA low waiting for clocks to finish its transaction. Its
//          open-drain FET beats the ESP32 driving high. Cure: pulse SCL until
//          it lets go, then issue a STOP.
//
//   MISO - if CS is low the SD card is selected and actively drives MISO.
//          Cure: deassert CS and try again.
//
// If both release after this, the board is fine and there is no bridge at all.

#include <Arduino.h>
#include <Wire.h>

static const uint8_t SDA_PIN = 6, SCL_PIN = 7, CS_PIN = 2, MISO_PIN = 5;

static bool canDriveHigh(uint8_t p) {
  pinMode(p, OUTPUT);
  digitalWrite(p, HIGH);
  delayMicroseconds(1500);
  int hi = 0; for (int k = 0; k < 5; k++) hi += digitalRead(p);
  pinMode(p, INPUT);
  return hi >= 4;
}

static void recoverI2C() {
  Serial.println("\n--- I2C bus recovery ---------------------------------");
  Serial.printf("  SDA before: %s\n", canDriveHigh(SDA_PIN) ? "free" : "HELD LOW");

  // Bit-bang up to 16 clocks with SDA released; a stuck slave finishes its
  // byte, sees the bus idle and lets go.
  pinMode(SDA_PIN, INPUT_PULLUP);
  pinMode(SCL_PIN, OUTPUT);
  for (int i = 0; i < 16; i++) {
    digitalWrite(SCL_PIN, HIGH); delayMicroseconds(6);
    digitalWrite(SCL_PIN, LOW);  delayMicroseconds(6);
    if (digitalRead(SDA_PIN)) { Serial.printf("  SDA released after %d clocks\n", i + 1); break; }
  }
  digitalWrite(SCL_PIN, HIGH); delayMicroseconds(6);

  // STOP condition: SDA low->high while SCL is high.
  pinMode(SDA_PIN, OUTPUT);
  digitalWrite(SDA_PIN, LOW);  delayMicroseconds(6);
  digitalWrite(SDA_PIN, HIGH); delayMicroseconds(6);
  pinMode(SDA_PIN, INPUT); pinMode(SCL_PIN, INPUT);
  delay(5);

  Serial.printf("  SDA after : %s\n", canDriveHigh(SDA_PIN) ? "FREE  <<< was a bus lockup, not a short"
                                                            : "still held low - genuine short");
}

static void releaseMISO() {
  Serial.println("\n--- MISO with the SD card deselected -----------------");
  Serial.printf("  MISO before: %s\n", canDriveHigh(MISO_PIN) ? "free" : "HELD LOW");

  pinMode(CS_PIN, OUTPUT);
  digitalWrite(CS_PIN, HIGH);          // deselect the card
  delay(10);
  // Clock it a little so it finishes any transaction it thinks it is in.
  pinMode(4, OUTPUT);
  for (int i = 0; i < 80; i++) { digitalWrite(4, HIGH); delayMicroseconds(4); digitalWrite(4, LOW); delayMicroseconds(4); }
  delay(5);

  const bool free_ = canDriveHigh(MISO_PIN);
  Serial.printf("  MISO after : %s\n", free_ ? "FREE  <<< the card was driving it, not a short"
                                             : "still held low - genuine short");
  pinMode(CS_PIN, INPUT); pinMode(4, INPUT);
}

static void tryMPU() {
  Serial.println("\n--- MPU-6050 WHO_AM_I after recovery -----------------");
  Wire.end(); delay(20);
  Wire.begin(SDA_PIN, SCL_PIN, 100000);
  Wire.setTimeOut(30); delay(10);
  for (uint8_t a = 0x68; a <= 0x69; a++) {
    Wire.beginTransmission(a); Wire.write(0x75);
    if (Wire.endTransmission(false) != 0) continue;
    if (Wire.requestFrom((int)a, 1) != 1) continue;
    const uint8_t who = Wire.read();
    Serial.printf("  0x%02X -> WHO_AM_I = 0x%02X %s\n", a, who,
                  (who == 0x68 || who == 0x70 || who == 0x71) ? "<<<<< MPU-6050 ALIVE" : "(bogus)");
  }
  Wire.end();
}

static void gps() {
  Serial.println("\n--- GPS (5V rail should be live now) -----------------");
  const uint32_t bauds[] = {9600, 115200};
  for (int b = 0; b < 2; b++) for (int sw = 0; sw < 2; sw++) {
    const uint8_t rx = sw ? 1 : 10, tx = sw ? 10 : 1;
    HardwareSerial s(1); s.begin(bauds[b], SERIAL_8N1, rx, tx);
    uint32_t n = 0, d = 0; char head[90]; int hn = 0;
    const uint32_t t0 = millis();
    while (millis() - t0 < 1500) while (s.available()) {
      char c = s.read(); n++; if (c == 36) d++;
      if (hn < 85 && c >= 32) head[hn++] = c;
    }
    s.end(); head[hn] = 0;
    Serial.printf("  RX=%-2u TX=%-2u @%6lu : %lu bytes, %lu NMEA%s\n", rx, tx,
                  (unsigned long)bauds[b], (unsigned long)n, (unsigned long)d,
                  d ? "  <<<<< GPS ALIVE" : "");
    if (d) Serial.printf("      %s\n", head);
  }
}

void setup() { Serial.begin(115200); delay(1500); }

void loop() {
  Serial.println("\n\n#### BUS-STATE vs SHORT ####");
  recoverI2C();
  releaseMISO();
  tryMPU();
  gps();
  Serial.println("\n#### END ####");
  delay(15000);
}
