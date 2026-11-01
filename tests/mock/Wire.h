#pragma once
#include <stdint.h>
#include <stddef.h>
// Host mock of TwoWire with a behavioural ADS1115 model (test only).
struct TwoWire {
  void begin(int, int, uint32_t);
  void end();
  void setTimeOut(uint16_t) {}
  void beginTransmission(uint8_t a);
  size_t write(uint8_t b);
  uint8_t endTransmission();
  size_t requestFrom(int addr, int n);
  int read();
};
extern TwoWire Wire;
