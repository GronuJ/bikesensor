// SD card check now that the GPIO6 short is gone.
#include <Arduino.h>
#include <SPI.h>
#include <SD.h>

static void tryMount(uint8_t sck, uint8_t miso, uint8_t mosi, uint8_t cs, uint32_t hz){
  SPI.end(); delay(20);
  SPI.begin(sck, miso, mosi, cs);
  if (SD.begin(cs, SPI, hz)) {
    Serial.printf("  MOUNTED  SCK=%u MISO=%u MOSI=%u CS=%u @%lukHz  size=%llu MB\n",
                  sck, miso, mosi, cs, (unsigned long)(hz/1000), SD.cardSize() >> 20);
    File f = SD.open("/probe.txt", FILE_WRITE);
    if (f) { f.println("bikesensor"); f.close();
             Serial.println("    write OK"); SD.remove("/probe.txt"); }
    else Serial.println("    mounted but write FAILED");
    SD.end();
  } else {
    Serial.printf("  failed   SCK=%u MISO=%u MOSI=%u CS=%u @%lukHz\n", sck, miso, mosi, cs, (unsigned long)(hz/1000));
  }
}

void setup(){ Serial.begin(115200); delay(1500); }

void loop(){
  Serial.println("\n\n#### SD CHECK ####");
  Serial.println("Cheap modules and long hand-wiring often need a slower clock.\n");
  tryMount(4,5,3,2, 400000);     // as wired per the README, slow
  tryMount(4,5,3,2, 1000000);
  tryMount(4,5,3,2, 4000000);
  Serial.println("\n#### END ####");
  delay(12000);
}
