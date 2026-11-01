// Host test for Game_v2.cpp: behavioural ADS1115 model + simulated clock.
// Build: g++ -std=c++17 -O1 -I tests/mock tests/fw_host_test.cpp Game_v2.cpp -o /tmp/fwtest
// Run:   /tmp/fwtest <outdir>   (writes one serial capture per scenario, prints PASS/FAIL)
#include <Arduino.h>
#include <Wire.h>
#include <string>
#include <vector>
#include <map>
#include <cmath>
#include <cstdlib>

void setup();
void loop();

// ---------------------------------------------------------------- simulated clock
uint64_t g_us = 1000000;
static const uint32_t US_PER_MICROS_CALL = 3;   // spin loops make time move
void mock_advance(uint32_t us) { g_us += us; }
uint32_t micros() { g_us += US_PER_MICROS_CALL; return (uint32_t)g_us; }
uint32_t millis() { return (uint32_t)(g_us / 1000); }
void analogReadResolution(int) {}
void analogSetPinAttenuation(int, int) {}

// ---------------------------------------------------------------- ESP32 ADC mock
static std::map<int, uint32_t> espMv;
uint32_t analogReadMilliVolts(int pin) { g_us += 30; return espMv.count(pin) ? espMv[pin] : 0; }

// ---------------------------------------------------------------- serial mock
static std::string serialOut;
static std::string serialIn;
void mock_serial_out(const char* s, size_t n) { serialOut.append(s, n); }
int mock_serial_in_available() { return (int)serialIn.size(); }
int mock_serial_in_read() { int c = serialIn[0]; serialIn.erase(0, 1); return c; }
HardwareSerial_ Serial;

// ---------------------------------------------------------------- I2C + ADS1115 model
static const uint32_t TX_COST_US = 90;   // one I2C transaction incl. driver overhead
struct AdsModel {
  bool connected = false;
  uint8_t ptr = 0;
  uint16_t cfg = 0x8583;
  int mux = 0;
  uint64_t doneAt = 0;
  int16_t result = 0;
  int16_t last = 0;
  long earlyReads = 0, reads = 0;
  // raw LSB values returned per channel (125 uV/LSB)
  int16_t lsb[4] = {0, 0, 0, 0};
};
static std::map<uint8_t, AdsModel> bus;
static uint8_t curAddr = 0;
static std::vector<uint8_t> txBytes;
static std::vector<uint8_t> rxBytes;
static size_t rxPos = 0;
static bool busHung = false;

void TwoWire::begin(int, int, uint32_t) { busHung = false; }
void TwoWire::end() {}
void TwoWire::beginTransmission(uint8_t a) { curAddr = a; txBytes.clear(); }
size_t TwoWire::write(uint8_t b) { txBytes.push_back(b); return 1; }
uint8_t TwoWire::endTransmission() {
  g_us += TX_COST_US;
  auto it = bus.find(curAddr);
  if (busHung || it == bus.end() || !it->second.connected) return 2;  // NACK on address
  AdsModel& d = it->second;
  if (txBytes.empty()) return 0;                                       // address-only probe
  d.ptr = txBytes[0];
  if (txBytes.size() == 3 && d.ptr == 1) {                             // config write
    d.cfg = (uint16_t)((txBytes[1] << 8) | txBytes[2]);
    if (d.cfg & 0x8000) {                                              // OS=1: start conversion
      d.mux = ((d.cfg >> 12) & 7) - 4;
      d.doneAt = g_us + 1163;                                          // 860 SPS
      d.result = d.lsb[d.mux & 3];
    }
  }
  return 0;
}
size_t TwoWire::requestFrom(int addr, int n) {
  g_us += TX_COST_US;
  rxBytes.clear(); rxPos = 0;
  auto it = bus.find((uint8_t)addr);
  if (busHung || it == bus.end() || !it->second.connected) return 0;
  AdsModel& d = it->second;
  if (d.ptr == 0) {
    d.reads++;
    if (g_us < d.doneAt) d.earlyReads++;                               // stale data would be returned
    else d.last = d.result;
    rxBytes = {(uint8_t)(d.last >> 8), (uint8_t)(d.last & 0xFF)};
  } else {
    rxBytes = {(uint8_t)(d.cfg >> 8), (uint8_t)(d.cfg & 0xFF)};
  }
  return rxBytes.size() >= (size_t)n ? n : rxBytes.size();
}
int TwoWire::read() { return rxPos < rxBytes.size() ? rxBytes[rxPos++] : -1; }
TwoWire Wire;

// ---------------------------------------------------------------- helpers
static int g_fail = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("  FAIL: %s\n", msg); g_fail++; } else { printf("  ok:   %s\n", msg); } } while (0)

struct Row { long seq; double t; double v[10]; int flags; bool csOK; };
static std::vector<Row> parseSerial(const std::string& s, int& badLines) {
  std::vector<Row> rows; badLines = 0;
  size_t i = 0;
  while (i < s.size()) {
    size_t j = s.find('\n', i); if (j == std::string::npos) break;
    std::string line = s.substr(i, j - i); i = j + 1;
    if (line.empty() || line[0] == '#') continue;
    size_t star = line.rfind('*');
    if (star == std::string::npos) { badLines++; continue; }
    uint8_t cs = 0; for (size_t k = 0; k < star; k++) cs ^= (uint8_t)line[k];
    Row r; r.csOK = (cs == (uint8_t)strtol(line.c_str() + star + 1, nullptr, 16));
    std::string body = line.substr(0, star);
    std::vector<double> f; size_t p = 0;
    while (p <= body.size()) { size_t q = body.find(',', p); if (q == std::string::npos) q = body.size(); f.push_back(atof(body.substr(p, q - p).c_str())); p = q + 1; }
    if (f.size() != 13) { badLines++; continue; }
    r.seq = (long)f[0]; r.t = f[1]; for (int k = 0; k < 10; k++) r.v[k] = f[2 + k]; r.flags = (int)f[12];
    if (!r.csOK) badLines++;
    rows.push_back(r);
  }
  return rows;
}

static void resetWorld() {
  bus.clear(); espMv.clear(); serialOut.clear(); serialIn.clear(); busHung = false;
  g_us = 1000000;
}
static void addBoard(uint8_t a, int16_t l0, int16_t l1, int16_t l2, int16_t l3) {
  AdsModel m; m.connected = true; m.lsb[0] = l0; m.lsb[1] = l1; m.lsb[2] = l2; m.lsb[3] = l3; bus[a] = m;
}
static void runUntil(uint64_t endUs) { while (g_us < endUs) loop(); }
static void save(const char* dir, const char* name) {
  std::string p = std::string(dir) + "/" + name;
  FILE* f = fopen(p.c_str(), "wb"); if (f) { fwrite(serialOut.data(), 1, serialOut.size(), f); fclose(f); }
}
static long totalEarly() { long e = 0; for (auto& kv : bus) e += kv.second.earlyReads; return e; }

int main(int argc, char** argv) {
  const char* outdir = argc > 1 ? argv[1] : ".";

  // ---------------- scenario A: all three boards, slot mapping, timing
  printf("A: three ADS1115 boards\n");
  resetWorld();
  addBoard(0x48, 8000, 16000, 24000, 12000);     // 1000, 2000, 3000, 1500 mV
  addBoard(0x49, 800, 1600, 2400, 3200);          // 100, 200, 300, 400 mV
  addBoard(0x4A, 4000, 6400, 0, 0);               // thumb 500 mV, ball 800 mV
  setup();
  uint64_t t0 = g_us; runUntil(t0 + 10000000ULL);  // 10 s
  save(outdir, "scenarioA.txt");
  int bad; auto rows = parseSerial(serialOut, bad);
  CHECK(rows.size() >= 995 && rows.size() <= 1001, "about 1000 samples in 10 s");
  CHECK(bad == 0, "all checksums valid and all lines have 13 fields");
  bool contiguous = true, spacingOK = true, mapOK = true, flagsOK = true;
  for (size_t i = 0; i < rows.size(); i++) {
    if (rows[i].seq != (long)i) contiguous = false;
    if (i && std::fabs((rows[i].t - rows[i - 1].t) - 10.0) > 0.05) spacingOK = false;
    const double e[10] = {500, 1000, 2000, 3000, 1500, 100, 200, 300, 400, 800};
    for (int k = 0; k < 10; k++) if (std::fabs(rows[i].v[k] - e[k]) > 0.06) mapOK = false;
    if (rows[i].flags != 0) flagsOK = false;
  }
  CHECK(contiguous, "sequence numbers contiguous");
  CHECK(spacingOK, "device timestamps spaced exactly 10 ms (no jitter, no bursts)");
  CHECK(mapOK, "channel-to-column mapping correct for all 10 columns");
  CHECK(flagsOK, "no flags raised");
  CHECK(totalEarly() == 0, "no conversion result read before the conversion finished");
  serialIn = "S"; runUntil(g_us + 20000);
  size_t sp = serialOut.rfind("# status");
  printf("  info: %s", sp != std::string::npos ? serialOut.substr(sp, serialOut.find('\n', sp) - sp + 1).c_str() : "(no status)\n");
  int ovr = 0; for (auto& r : rows) if (r.flags & 8) ovr++;
  CHECK(ovr == 0, "acquisition fits inside the 10 ms period with three boards (under the mock timing model)");

  // ---------------- scenario B: ESP32 ADC fallback
  printf("B: no 0x4A board, ESP32 ADC fallback\n");
  resetWorld();
  addBoard(0x48, 8000, 16000, 24000, 12000);
  addBoard(0x49, 800, 1600, 2400, 3200);
  espMv[34] = 700; espMv[35] = 1234;
  setup(); t0 = g_us; runUntil(t0 + 2000000ULL);
  rows = parseSerial(serialOut, bad);
  bool fbOK = !rows.empty() && std::fabs(rows.back().v[0] - 700) < 0.06 && std::fabs(rows.back().v[9] - 1234) < 0.06;
  CHECK(fbOK, "thumb and ball come from the ESP32 ADC");
  CHECK(!rows.empty() && rows.back().flags == 0, "no range flag at normal values");
  espMv[35] = 40; runUntil(g_us + 100000);
  rows = parseSerial(serialOut, bad);
  CHECK(rows.back().flags & 16, "range flag when the ESP32 ADC reads in its dead zone");
  ovr = 0; for (auto& r : rows) if (r.flags & 8) ovr++;
  CHECK(ovr == 0, "no overrun with two boards + ESP ADC fallback");

  // ---------------- scenario C: cable pulled and re-inserted
  printf("C: board 0x49 unplugged at 3 s, re-plugged at 5 s\n");
  resetWorld();
  addBoard(0x48, 8000, 16000, 24000, 12000);
  addBoard(0x49, 800, 1600, 2400, 3200);
  addBoard(0x4A, 4000, 6400, 0, 0);
  setup(); t0 = g_us;
  runUntil(t0 + 3000000ULL);
  bus[0x49].connected = false;
  runUntil(t0 + 5000000ULL);
  bus[0x49].connected = true;
  runUntil(t0 + 8000000ULL);
  save(outdir, "scenarioC.txt");
  rows = parseSerial(serialOut, bad);
  CHECK(rows.size() > 790, "loop never hung: samples kept flowing during the fault");
  bool seqOK = true; for (size_t i = 0; i < rows.size(); i++) if (rows[i].seq != rows[0].seq + (long)i) seqOK = false;  // firmware statics persist across scenarios
  CHECK(seqOK, "sequence stayed contiguous");
  int errDuring = 0, errBefore = 0, errAfter = 0;
  double tFirstErr = -1, tRecover = -1;
  for (auto& r : rows) {
    double ts = r.t - rows[0].t;
    if (r.flags & 2) { if (tFirstErr < 0) tFirstErr = ts; if (ts < 3000) errBefore++; else if (ts < 5000) errDuring++; else errAfter++; }
    else if (tFirstErr >= 0 && tRecover < 0) tRecover = ts;
  }
  CHECK(errBefore == 0, "no fault flag before the unplug");
  CHECK(errDuring > 150, "fault flag raised while the board is missing");
  printf("  info: fault first flagged at %.0f ms, cleared at %.0f ms (re-plugged at 5000 ms)\n", tFirstErr, tRecover);
  CHECK(tRecover > 5000 && tRecover < 6600, "flag clears within ~1.5 s of re-plugging");
  bool otherOK = true; for (auto& r : rows) { double ts = r.t - rows[0].t; if (ts > 3000 && ts < 5000 && std::fabs(r.v[1] - 1000) > 0.06) otherOK = false; }
  CHECK(otherOK, "other boards keep delivering correct data during the fault");
  bool valsBack = std::fabs(rows.back().v[5] - 100) < 0.06;
  CHECK(valsBack, "board values correct again after recovery");

  // ---------------- scenario D: a required board missing at boot
  printf("D: board 0x48 missing at boot\n");
  resetWorld();
  addBoard(0x49, 800, 1600, 2400, 3200);
  addBoard(0x4A, 4000, 6400, 0, 0);
  setup(); t0 = g_us; runUntil(t0 + 3000000ULL);
  rows = parseSerial(serialOut, bad);
  CHECK(rows.size() > 290, "keeps streaming (the old firmware hung here)");
  CHECK(!rows.empty() && (rows.back().flags & 1), "flag bit0 set");
  CHECK(serialOut.find("WARN ADS1115 0x48") != std::string::npos, "warning printed at boot");
  bus[0x48] = AdsModel(); bus[0x48].connected = true; bus[0x48].lsb[0] = 8000; bus[0x48].lsb[1] = 16000; bus[0x48].lsb[2] = 24000; bus[0x48].lsb[3] = 12000;
  runUntil(g_us + 2500000ULL);
  rows = parseSerial(serialOut, bad);
  CHECK(!(rows.back().flags & 1) && std::fabs(rows.back().v[1] - 1000) < 0.06, "board found later and used");

  // ---------------- scenario E: stalled loop does not produce a burst
  printf("E: 55 ms stall, then normal operation\n");
  resetWorld();
  addBoard(0x48, 8000, 16000, 24000, 12000);
  addBoard(0x49, 800, 1600, 2400, 3200);
  addBoard(0x4A, 4000, 6400, 0, 0);
  setup(); t0 = g_us; runUntil(t0 + 1000000ULL);
  g_us += 55000;                                  // e.g. USB host paused us
  runUntil(g_us + 1000000ULL);
  rows = parseSerial(serialOut, bad);
  double minGap = 1e9; for (size_t i = 1; i < rows.size(); i++) minGap = std::fmin(minGap, rows[i].t - rows[i - 1].t);
  printf("  info: smallest gap between consecutive samples = %.3f ms\n", minGap);
  CHECK(minGap > 9.9, "no catch-up burst after the stall");
  CHECK(serialOut.find("skipped") != std::string::npos, "status line reports skipped counter");

  printf("\n%s (%d failure%s)\n", g_fail ? "SOME CHECKS FAILED" : "ALL CHECKS PASSED", g_fail, g_fail == 1 ? "" : "s");
  return g_fail ? 1 : 0;
}
