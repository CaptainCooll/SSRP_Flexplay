// Minimal host mock of the Arduino/ESP32 API used by Game_v2.cpp (test only).
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#define ADC_11db 3
extern uint64_t g_us;                     // simulated time
uint32_t micros();
uint32_t millis();
inline void delay(uint32_t ms) { extern void mock_advance(uint32_t); mock_advance(ms * 1000); }
void analogReadResolution(int);
void analogSetPinAttenuation(int, int);
uint32_t analogReadMilliVolts(int pin);
void mock_serial_out(const char* s, size_t n);
int mock_serial_in_available();
int mock_serial_in_read();

struct HardwareSerial_ {
  void setTxBufferSize(size_t) {}
  void begin(uint32_t) {}
  int printf(const char* fmt, ...) {
    char b[400]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    mock_serial_out(b, n > 0 ? (size_t)n : 0); return n;
  }
  void println(const char* s) { mock_serial_out(s, strlen(s)); mock_serial_out("\n", 1); }
  size_t write(const uint8_t* d, size_t n) { mock_serial_out((const char*)d, n); return n; }
  int available() { return mock_serial_in_available(); }
  int read() { return mock_serial_in_read(); }
};
extern HardwareSerial_ Serial;
