// BIKESENSOR BOARD DIAGNOSTIC
//
// Finds peripherals instead of assuming the pin map, because this board has no
// signal names on the silkscreen and no ESP32 pin names in the schematic — so
// the mapping is exactly the thing most likely to be wrong on an assembly.
//
// KNOWN LIMITATION, learned the hard way: do NOT try to measure a pin's
// resistance to ground by engaging the internal pull-up and then calling
// analogReadMilliVolts(). On ESP32 the ADC driver reconfigures the pad and drops
// the pull-up, so you measure the floating pin twice and get a meaningless
// number. The digital pull-up/pull-down test below is the reliable one; treat
// the ADC block purely as "is there a plausible battery voltage here".

#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>

static const uint8_t PINS[] = {0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 20, 21};
static const int N = sizeof(PINS) / sizeof(PINS[0]);

// A pin pulled below V_IL while the ~45k internal pull-up is engaged is being
// held by something stronger than roughly 14k. That is a real finding. It does
// NOT distinguish a dead short from a few-k resistor — use a multimeter for that.
static void pinStates() {
  Serial.println("\n--- PIN STATES ---------------------------------------");
  Serial.println("pull-up LOW  => held down by < ~14k (short, or a driver)");
  Serial.println("pull-dn HIGH => pulled up by < ~14k (3V3, or a module pull-up)");
  Serial.println("both follow  => floating, nothing attached\n");
  for (int i = 0; i < N; i++) {
    const uint8_t p = PINS[i];
    pinMode(p, INPUT_PULLUP);   delay(5);
    int up = 0; for (int k = 0; k < 9; k++) { up += digitalRead(p); delay(1); }
    pinMode(p, INPUT_PULLDOWN); delay(5);
    int dn = 0; for (int k = 0; k < 9; k++) { dn += digitalRead(p); delay(1); }
    pinMode(p, INPUT);
    const char *v;
    if      (up >= 8 && dn <= 1) v = "floating";
    else if (up <= 1 && dn <= 1) v = "HELD LOW   <<<";
    else if (up >= 8 && dn >= 8) v = "PULLED HIGH";
    else                         v = "loaded/noisy";
    Serial.printf("  GPIO %-2u  up=%d/9 dn=%d/9  %s\n", p, up, dn, v);
  }
}

// WHO_AM_I, not ACK. A stuck-low SDA ACKs every address and proves nothing —
// an earlier version of this sketch "found" the MPU at 0x68 AND 0x69 across
// seven SCL pins, which is impossible and was exactly that artefact.
static void i2c() {
  Serial.println("\n--- I2C: MPU-6050 confirmed via WHO_AM_I -------------");
  const uint8_t cand[][2] = {{6,7},{7,6},{5,6},{6,5},{4,5},{3,1},{20,21}};
  int hits = 0;
  for (unsigned c = 0; c < sizeof(cand)/sizeof(cand[0]); c++) {
    Wire.end(); delay(25);
    if (!Wire.begin(cand[c][0], cand[c][1], 100000)) continue;
    Wire.setTimeOut(25); delay(8);
    for (uint8_t a = 0x68; a <= 0x69; a++) {
      Wire.beginTransmission(a); Wire.write(0x75);
      if (Wire.endTransmission(false) != 0) continue;
      if (Wire.requestFrom((int)a, 1) != 1) continue;
      const uint8_t who = Wire.read();
      Serial.printf("  SDA=%u SCL=%u 0x%02X -> WHO_AM_I=0x%02X %s\n", cand[c][0], cand[c][1], a, who,
                    (who == 0x68 || who == 0x70 || who == 0x71) ? "<<< REAL MPU" : "(bogus)");
      hits++;
    }
  }
  Wire.end();
  if (!hits) Serial.println("  nothing answered a register read on any candidate pair");
}

static void sd() {
  Serial.println("\n--- MICROSD (CS=2 SCK=4 MISO=5 MOSI=3) ---------------");
  SPI.end(); SPI.begin(4, 5, 3, 2);
  if (SD.begin(2)) { Serial.printf("  mounted, %llu MB\n", SD.cardSize() >> 20); SD.end(); }
  else Serial.println("  mount FAILED");
}

static void gps() {
  Serial.println("\n--- GPS ----------------------------------------------");
  Serial.println("  NOTE: the NEO-6M runs off +5V from the Wemos shield via SW1,");
  Serial.println("  NOT from USB. With no battery attached it has no power and");
  Serial.println("  silence here says nothing about the wiring.");
  const uint32_t bauds[] = {9600, 115200};
  for (int b = 0; b < 2; b++) for (int sw = 0; sw < 2; sw++) {
    const uint8_t rx = sw ? 1 : 10, tx = sw ? 10 : 1;
    HardwareSerial s(1); s.begin(bauds[b], SERIAL_8N1, rx, tx);
    uint32_t n = 0, d = 0; const uint32_t t0 = millis();
    while (millis() - t0 < 1200) while (s.available()) { char c = s.read(); n++; if (c == 36) d++; }
    s.end();
    Serial.printf("  RX=%-2u TX=%-2u @%6lu : %lu bytes, %lu NMEA%s\n", rx, tx,
                  (unsigned long)bauds[b], (unsigned long)n, (unsigned long)d, d ? "  <<< GPS HERE" : "");
  }
}

void setup() { Serial.begin(115200); delay(1500); }

void loop() {
  Serial.println("\n\n#### BIKESENSOR DIAGNOSTIC ####");
  pinStates(); sd(); i2c(); gps();
  Serial.println("\n#### END ####");
  delay(20000);
}
