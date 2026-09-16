// BIKESENSOR SHORT TEST — software continuity, no multimeter needed.
//
// Drive each pin HIGH and read the pad back. The ESP32-C3 sources ~40mA; a real
// short to ground wins that fight, so a pin that cannot be driven high is
// shorted. Then drive LOW and read back, which catches a pin stuck to 3V3.
//
// This distinguishes the three cases the pull-up test could not:
//   drives both ways      -> pin is fine, whatever a passive test said
//   cannot go high        -> shorted to GND
//   cannot go low         -> shorted to 3V3
//
// Each pin is driven for ~2ms, so if it IS shorted the current pulse is brief.
// GPIO5 (MISO) may be driven by the SD module; brief 3V3 CMOS contention is
// tolerable but this is why the pulses are short.

#include <Arduino.h>

struct Pin { uint8_t gpio; const char *fn; };
static const Pin PINS[] = {
  {0,  "BATTERY_ADC"}, {1,  "GPS_RX/TX"},  {2,  "SPI_CS"},
  {3,  "SPI_MOSI"},    {4,  "SPI_SCK"},    {5,  "SPI_MISO"},
  {6,  "I2C_SDA"},     {7,  "I2C_SCL"},    {9,  "unused"},
  {10, "GPS_TX/RX"},   {20, "UART"},       {21, "UART"},
};
static const int N = sizeof(PINS) / sizeof(PINS[0]);

void setup() { Serial.begin(115200); delay(1500); }

void loop() {
  Serial.println("\n\n#### SOFTWARE SHORT TEST ####");
  Serial.println("Driving each pin high, then low, and reading the pad back.\n");
  Serial.println("  gpio  function       drive HIGH   drive LOW    verdict");
  Serial.println("  ---------------------------------------------------------------");

  for (int i = 0; i < N; i++) {
    const uint8_t p = PINS[i].gpio;

    pinMode(p, OUTPUT);
    digitalWrite(p, HIGH); delayMicroseconds(1500);
    int hi = 0; for (int k = 0; k < 5; k++) hi += digitalRead(p);
    digitalWrite(p, LOW);  delayMicroseconds(1500);
    int lo = 0; for (int k = 0; k < 5; k++) lo += digitalRead(p);
    pinMode(p, INPUT);                       // release immediately
    delay(5);

    const bool canHigh = (hi >= 4);
    const bool canLow  = (lo <= 1);

    const char *verdict;
    if (canHigh && canLow)       verdict = "ok - drives both ways";
    else if (!canHigh && canLow) verdict = "SHORTED TO GND   <<<<<<";
    else if (canHigh && !canLow) verdict = "SHORTED TO 3V3   <<<<<<";
    else                         verdict = "stuck - cannot drive at all";

    Serial.printf("  %-4u  %-13s  %s        %s        %s\n",
                  p, PINS[i].fn,
                  canHigh ? "  ok " : " FAIL",
                  canLow  ? "  ok " : " FAIL",
                  verdict);
  }

  Serial.println("\n  'ok - drives both ways' means the pin is electrically free,");
  Serial.println("  even if the earlier pull-up test called it held low. A weak");
  Serial.println("  pull-down (a resistor, or a module input) reads as held low");
  Serial.println("  but cannot stop the pin being driven.");
  Serial.println("\n#### END ####");
  delay(15000);
}
