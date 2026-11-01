// FlexPlay glove firmware v2 (ESP32, Arduino core 2.x or 3.x)
//
// What changed versus Game.cpp (see AUDIT_AND_DEPLOY.md for the reasons):
//   * No Adafruit library. A minimal ADS1115 driver checks every I2C transaction,
//     so a pulled/flexed cable can no longer hang the loop forever.
//   * Pipelined conversions: each ADS1115 converts the next channel while the others
//     are being read; fixed conversion wait instead of polling the OS bit over I2C.
//   * Optional third ADS1115 at 0x4A (ADDR->SDA) takes the thumb flex (A0) and the
//     squeeze-ball FSR (A1) so every channel has the same 16-bit front end. If it is
//     absent the firmware falls back to the ESP32 ADC with 8x oversampling.
//   * Every line carries a sequence number, a device timestamp, a flags field and an
//     XOR checksum, so the page can detect dropped, corrupted and stale data.
//   * Scheduler resyncs after a stall instead of bursting catch-up samples.
//   * Failed boards are re-probed once a second and the I2C bus is reset (hot-plug
//     recovery). The sample rate is 100 Hz and the baud rate is 460800.
//
// Line format (one per sample, 100 Hz):
//   seq,t_ms,thumb,idx,mid,ring,lit,fsrIdx,fsrMid,fsrRing,fsrLit,ball,flags*HH\n
//   - values in millivolts with 0.1 mV resolution
//   - t_ms: device time of the start of the acquisition, ms with 3 decimals
//   - flags: bit0 board 0x48 fault, bit1 board 0x49 fault, bit2 board 0x4A fault,
//            bit3 sample took longer than the period, bit4 ESP32 ADC near its limits
//   - HH: two hex digits, XOR of every character before the '*'
// Lines that start with '#' are human-readable info and are ignored by the page.
//
// Wiring (I2C: SDA=21, SCL=22, 2.2-4.7 kOhm pull-ups to 3V3, 100 nF at every board):
//   ADS1115 #0 at 0x48 (ADDR->GND): A0..A3 = flex index, middle, ring, little
//   ADS1115 #1 at 0x49 (ADDR->VDD): A0..A3 = FSR index, middle, ring, little tips
//   ADS1115 #2 at 0x4A (ADDR->SDA): A0 = flex thumb, A1 = squeeze-ball FSR   (optional)
//   Fallback without #2: GPIO34 = flex thumb, GPIO35 = squeeze-ball FSR (ADC1 only).
//   Divider convention is the same as before (FSR on the high side, Rm to ground).
//
// Serial commands (optional, from the Arduino serial monitor): 'S' status, 'R' re-init.

#include <Arduino.h>
#include <Wire.h>

// ---------------------------------------------------------------- configuration
static const uint32_t BAUD = 460800;
static const uint32_t PERIOD_US = 10000;      // 100 Hz
static const uint32_t CONV_WAIT_US = 1300;    // 860 SPS: 1.163 ms nominal + oscillator tolerance
static const int PIN_SDA = 21, PIN_SCL = 22;
static const int PIN_FLEX_THUMB = 34, PIN_FSR_BALL = 35;
static const int ESP_OVERSAMPLE = 8;
static const uint32_t REPROBE_MS = 1000;
static const char* FW_VERSION = "2.0";

enum : uint8_t {
  F_ADS0 = 1, F_ADS1 = 2, F_ADS2 = 4, F_OVERRUN = 8, F_ESP_RANGE = 16
};

struct Ads {
  uint8_t addr;
  bool present;   // found at boot (or recovered)
  bool failed;    // an I2C transaction failed
  float mv[4];
};
static Ads ads[3] = {{0x48, false, false, {0, 0, 0, 0}},
                     {0x49, false, false, {0, 0, 0, 0}},
                     {0x4A, false, false, {0, 0, 0, 0}}};

static uint32_t seq = 0;
static uint32_t nextTick = 0;
static uint32_t lastProbeMs = 0;
static uint32_t maxAcqUs = 0, overruns = 0, skipped = 0;

// ---------------------------------------------------------------- minimal ADS1115 driver
static bool i2cWrite16(uint8_t a, uint8_t reg, uint16_t v) {
  Wire.beginTransmission(a);
  Wire.write(reg);
  Wire.write((uint8_t)(v >> 8));
  Wire.write((uint8_t)(v & 0xFF));
  return Wire.endTransmission() == 0;
}

static bool i2cRead16(uint8_t a, uint8_t reg, int16_t& out) {
  Wire.beginTransmission(a);
  Wire.write(reg);
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom((int)a, 2) != 2) return false;
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  out = (int16_t)(((uint16_t)hi << 8) | lo);
  return true;
}

static bool probe(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

// single-shot, +/-4.096 V (125 uV/LSB), 860 SPS, comparator off. ch 0..3 = AINch vs GND.
static uint16_t adsCfg(int ch) {
  return 0x8000 | (uint16_t)((4 + ch) << 12) | 0x0200 | 0x0100 | 0x00E0 | 0x0003;
}

// Reads channels 0..3 of every healthy board. The boards convert in parallel and each one
// starts its next conversion right after its result is read, so I2C traffic overlaps with
// conversion time. Every transaction is checked; a failure marks only that board.
static void acquireAds() {
  uint32_t tStart[3] = {0, 0, 0};
  for (int d = 0; d < 3; d++) {
    Ads& a = ads[d];
    if (!a.present || a.failed) continue;
    if (!i2cWrite16(a.addr, 1, adsCfg(0))) { a.failed = true; continue; }
    tStart[d] = micros();
  }
  for (int ch = 0; ch < 4; ch++) {
    for (int d = 0; d < 3; d++) {
      Ads& a = ads[d];
      if (!a.present || a.failed) continue;
      while ((uint32_t)(micros() - tStart[d]) < CONV_WAIT_US) {}
      int16_t raw;
      if (!i2cRead16(a.addr, 0, raw)) { a.failed = true; continue; }
      a.mv[ch] = raw * 0.125f;
      if (ch < 3) {
        if (!i2cWrite16(a.addr, 1, adsCfg(ch + 1))) { a.failed = true; continue; }
        tStart[d] = micros();
      }
    }
  }
}

static float readEspMv(int pin, uint8_t& flags) {
  uint32_t s = 0;
  for (int i = 0; i < ESP_OVERSAMPLE; i++) s += analogReadMilliVolts(pin);
  float v = s / (float)ESP_OVERSAMPLE;
  // The ESP32 ADC is nonlinear: below ~100 mV it reads zero, above ~3.1 V it saturates.
  if (v < 100.0f || v > 3100.0f) flags |= F_ESP_RANGE;
  return v;
}

// ---------------------------------------------------------------- setup helpers
static void busReset() {
  Wire.end();
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  Wire.setTimeOut(10);  // ms; a stuck bus must not block the loop
}

static void probeAll(bool atBoot) {
  for (int d = 0; d < 3; d++) {
    Ads& a = ads[d];
    if (atBoot) {
      a.present = probe(a.addr);
      a.failed = false;
    } else if (a.failed || (d < 2 && !a.present)) {
      // recover a board that failed; the optional board is never hot-added (it would
      // silently move the thumb/ball channels to a different source mid-stream)
      if (probe(a.addr)) { a.present = true; a.failed = false; }
    }
  }
}

static void printStatus() {
  Serial.printf("# status fw=%s ads0=%d ads1=%d ads2=%d fail=%d%d%d maxAcq_us=%lu overruns=%lu skipped=%lu\n",
                FW_VERSION, ads[0].present, ads[1].present, ads[2].present,
                ads[0].failed, ads[1].failed, ads[2].failed,
                (unsigned long)maxAcqUs, (unsigned long)overruns, (unsigned long)skipped);
}

static void reinitAll() {
  busReset();
  probeAll(true);
  maxAcqUs = overruns = skipped = 0;
  printStatus();
}

void setup() {
  Serial.setTxBufferSize(1024);  // must precede begin(); one line is ~90 bytes
  Serial.begin(BAUD);
  delay(300);
  busReset();
  probeAll(true);
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_FLEX_THUMB, ADC_11db);
  analogSetPinAttenuation(PIN_FSR_BALL, ADC_11db);

  Serial.printf("# FlexPlay glove fw %s fs=%lu baud=%lu\n", FW_VERSION,
                (unsigned long)(1000000UL / PERIOD_US), (unsigned long)BAUD);
  Serial.println("# columns: seq,t_ms,flex_thumb,flex_index,flex_middle,flex_ring,flex_little,"
                 "fsr_index,fsr_middle,fsr_ring,fsr_little,fsr_ball,flags*XORHEX (mV)");
  if (!ads[0].present) Serial.println("# WARN ADS1115 0x48 (flex) not found");
  if (!ads[1].present) Serial.println("# WARN ADS1115 0x49 (FSR) not found");
  if (!ads[2].present) Serial.println("# INFO no ADS1115 at 0x4A: thumb and ball use the ESP32 ADC (noisier)");
  printStatus();
  nextTick = micros() + PERIOD_US;
}

// ---------------------------------------------------------------- main loop
void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'R') reinitAll();
    else if (c == 'S') printStatus();
  }

  uint32_t now = micros();
  if ((int32_t)(now - nextTick) < 0) return;
  if ((uint32_t)(now - nextTick) > PERIOD_US) {  // stalled for a whole period: do not burst
    skipped++;
    nextTick = now;
  }
  nextTick += PERIOD_US;

  uint32_t t0 = micros();
  acquireAds();

  uint8_t flags = 0;
  float v[10];
  if (ads[2].present) {
    v[0] = ads[2].mv[0];
    v[9] = ads[2].mv[1];
  } else {
    v[0] = readEspMv(PIN_FLEX_THUMB, flags);
    v[9] = readEspMv(PIN_FSR_BALL, flags);
  }
  for (int i = 0; i < 4; i++) {
    v[1 + i] = ads[0].mv[i];
    v[5 + i] = ads[1].mv[i];
  }
  if (!ads[0].present || ads[0].failed) flags |= F_ADS0;
  if (!ads[1].present || ads[1].failed) flags |= F_ADS1;
  if (ads[2].present && ads[2].failed) flags |= F_ADS2;

  uint32_t acq = micros() - t0;
  if (acq > maxAcqUs) maxAcqUs = acq;
  if (acq > PERIOD_US) { flags |= F_OVERRUN; overruns++; }

  char line[200];
  int n = snprintf(line, sizeof(line), "%lu,%lu.%03lu", (unsigned long)seq,
                   (unsigned long)(t0 / 1000UL), (unsigned long)(t0 % 1000UL));
  for (int i = 0; i < 10; i++) n += snprintf(line + n, sizeof(line) - n, ",%.1f", v[i]);
  n += snprintf(line + n, sizeof(line) - n, ",%u", (unsigned)flags);
  uint8_t cs = 0;
  for (int i = 0; i < n; i++) cs ^= (uint8_t)line[i];
  n += snprintf(line + n, sizeof(line) - n, "*%02X\n", (unsigned)cs);
  Serial.write((const uint8_t*)line, n);
  seq++;

  // hot-plug recovery, outside the time-critical part
  uint32_t ms = millis();
  if (ms - lastProbeMs >= REPROBE_MS) {
    lastProbeMs = ms;
    bool needReset = false;
    for (int d = 0; d < 3; d++)
      if (ads[d].failed || (d < 2 && !ads[d].present)) needReset = true;
    if (needReset) {
      busReset();
      probeAll(false);
    }
  }
}
