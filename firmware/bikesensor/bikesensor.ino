// ESP32 + MPU-6050 vibration logger over BLE.
//
// Wire protocol on characteristic 0xFFE1 (notify), distinguished by first byte:
//
//   SYNC (9 bytes, sent on connect and every 30 s):
//     [0xA5][uint32 sample_idx_now LE][uint16 fs_hz LE][uint8 n_axes=6][uint8 reserved]
//
//   DATA (6 + 12*N bytes):
//     [0x5A][uint32 first_sample_idx LE][uint8 n_samples][N * (int16 ax,ay,az,gx,gy,gz) BIG-ENDIAN]
//
// IMU bytes are forwarded RAW from the MPU-6050 FIFO (big-endian on the wire).
// Scale on host: ±4g -> 1/8192 g/LSB, ±500°/s -> 1/65.5 °/s/LSB.
//
// Build: PlatformIO (firmware/platformio.ini), env esp32-c3-supermini.
// Wiring (ESP32-C3 SuperMini): SDA->GPIO 6, SCL->GPIO 7, MPU AD0->GND, VCC->3V3.
// (GPIO 8 has the onboard LED, GPIO 9 is the BOOT button — both unsafe for I²C.)

#include <Arduino.h>
#include <Wire.h>
#include <NimBLEDevice.h>

// ---------- MPU-6050 ----------
static constexpr uint8_t MPU_ADDR        = 0x68;
static constexpr uint8_t REG_SMPLRT_DIV  = 0x19;
static constexpr uint8_t REG_CONFIG      = 0x1A;
static constexpr uint8_t REG_GYRO_CONF   = 0x1B;
static constexpr uint8_t REG_ACCEL_CONF  = 0x1C;
static constexpr uint8_t REG_FIFO_EN     = 0x23;
static constexpr uint8_t REG_USER_CTRL   = 0x6A;
static constexpr uint8_t REG_PWR_MGMT_1  = 0x6B;
static constexpr uint8_t REG_FIFO_COUNTH = 0x72;
static constexpr uint8_t REG_FIFO_RW     = 0x74;

static constexpr uint16_t FS_HZ        = 250;     // sample rate
static constexpr uint8_t  SAMPLES_PER_PKT = 10;   // 10 * 12B = 120B payload + 6B hdr = 126B
static constexpr uint8_t  BYTES_PER_SAMPLE = 12;  // ax ay az gx gy gz, int16 BE

static void w8(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.write(v);
  Wire.endTransmission();
}

static uint16_t fifoCount() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_FIFO_COUNTH);
  Wire.endTransmission(false);
  Wire.requestFrom((int)MPU_ADDR, 2);
  uint16_t hi = Wire.read(), lo = Wire.read();
  return (hi << 8) | lo;
}

static void mpuInit() {
  // MPU-6050 Initialization Sequence
  w8(REG_PWR_MGMT_1,  0x80); delay(100);   // Reset device
  w8(REG_PWR_MGMT_1,  0x01);               // Use PLL with X-axis gyroscope reference for stable clock
  
  // Digital Low Pass Filter (DLPF) Configuration
  // Setting 0x01: Accel BW = 184Hz, Gyro BW = 188Hz. 
  // This helps remove high-frequency noise before sampling.
  w8(REG_CONFIG,      0x01);               
  
  // Sample Rate Divider: fs = GyroOutputRate / (1 + divider)
  // GyroOutputRate is 1kHz when DLPF is enabled.
  w8(REG_SMPLRT_DIV,  (1000 / FS_HZ) - 1); 
  
  w8(REG_GYRO_CONF,   0x08);               // Full scale range: ±500 °/s
  w8(REG_ACCEL_CONF,  0x08);               // Full scale range: ±4 g (ideal for bike vibration)
  
  // Initialize FIFO
  w8(REG_USER_CTRL,   0x44);               // Enable FIFO and reset it
  w8(REG_FIFO_EN,     0x78);               // Load Accel X/Y/Z and Gyro X/Y/Z into FIFO
}

// ---------- BLE ----------
static constexpr const char* SVC_UUID  = "0000ffe0-0000-1000-8000-00805f9b34fb";
static constexpr const char* CHR_UUID  = "0000ffe1-0000-1000-8000-00805f9b34fb";

static NimBLECharacteristic* chr = nullptr;
static volatile bool subscribed = false;
static uint32_t sampleIdx = 0;       // Monotonically increasing index for the clock model
static uint32_t lastSyncMs = 0;

class SrvCb : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* s, NimBLEConnInfo&) override {
    // Request a fast connection interval (15-30ms) for responsive data throughput.
    s->updateConnParams(s->getPeerInfo(0).getConnHandle(), 12, 24, 0, 200); 
  }
  void onDisconnect(NimBLEServer* s, NimBLEConnInfo&, int) override {
    subscribed = false;
    NimBLEDevice::startAdvertising();
  }
};

class ChrCb : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo&, uint16_t v) override {
    subscribed = (v != 0);
    if (subscribed) {
      // CRITICAL: When the phone starts listening, we reset the sample index 
      // and FIFO so the linear clock model on the host starts from a clean zero.
      w8(REG_USER_CTRL, 0x44);
      sampleIdx = 0;
      lastSyncMs = 0;
    }
  }
};

static void sendSync() {
  /**
   * Sends a SYNC packet to anchor the phone's wallclock to our sample index.
   * Protocol: [0xA5][sampleIdx LE][fs LE][n_axes][reserved]
   */
  uint8_t pkt[9];
  pkt[0] = 0xA5;
  memcpy(pkt + 1, &sampleIdx, 4);
  uint16_t fs = FS_HZ;
  memcpy(pkt + 5, &fs, 2);
  pkt[7] = 6; pkt[8] = 0;
  chr->setValue(pkt, sizeof(pkt));
  chr->notify();
}

static void sendBatch() {
  /**
   * Reads a batch of samples from the MPU-6050 FIFO and sends them over BLE.
   * Protocol: [0x5A][firstSampleIdx LE][nSamples][Payload]
   */
  const uint16_t need = SAMPLES_PER_PKT * BYTES_PER_SAMPLE;
  if (fifoCount() < need) return;

  uint8_t pkt[6 + SAMPLES_PER_PKT * BYTES_PER_SAMPLE];
  pkt[0] = 0x5A;
  memcpy(pkt + 1, &sampleIdx, 4);
  pkt[5] = SAMPLES_PER_PKT;

  // Read FIFO in chunks.
  // The standard Arduino/ESP32 Wire buffer is often limited to 32 bytes.
  // We read the 120-byte payload in 32-byte segments to avoid buffer overflow.
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_FIFO_RW);
  Wire.endTransmission(false);
  uint16_t got = 0;
  while (got < need) {
    uint8_t want = min<uint16_t>(32, need - got);
    Wire.requestFrom((int)MPU_ADDR, (int)want, (int)(got + want == need));
    while (Wire.available() && got < need) pkt[6 + got++] = Wire.read();
  }

  chr->setValue(pkt, sizeof(pkt));
  chr->notify();
  sampleIdx += SAMPLES_PER_PKT;
}

void setup() {
  Serial.begin(115200);
  Wire.begin(6, 7, 400000);   // SDA=GPIO 6, SCL=GPIO 7 on ESP32-C3 SuperMini
  mpuInit();

  NimBLEDevice::init("bikesensor_jj");
  NimBLEDevice::setMTU(185);
  auto* srv = NimBLEDevice::createServer();
  srv->setCallbacks(new SrvCb());
  auto* svc = srv->createService(SVC_UUID);
  chr = svc->createCharacteristic(CHR_UUID, NIMBLE_PROPERTY::NOTIFY);
  chr->setCallbacks(new ChrCb());
  auto* adv = NimBLEDevice::getAdvertising();
  // NimBLE 2.x: device name is NOT auto-included in the advertisement —
  // must be set on the advertising object explicitly, or scanners show "Unknown".
  adv->setName("bikesensor_jj");
  adv->addServiceUUID(SVC_UUID);
  adv->enableScanResponse(true);
  adv->start();
  Serial.println("BLE advertising as 'bikesensor_jj'");
}

void loop() {
  static uint32_t lastBeatMs = 0;
  uint32_t now = millis();
  if (now - lastBeatMs > 1000) {
    Serial.printf("[%lus] subscribed=%d sample_idx=%lu fifo=%u\n",
                  now / 1000, subscribed ? 1 : 0,
                  (unsigned long)sampleIdx, fifoCount());
    lastBeatMs = now;
  }

  if (!subscribed) { delay(20); return; }

  if (lastSyncMs == 0 || now - lastSyncMs > 30000) {
    sendSync();
    lastSyncMs = now;
  }
  sendBatch();
  delay(2); // keep loop snappy without busy-spinning
}
