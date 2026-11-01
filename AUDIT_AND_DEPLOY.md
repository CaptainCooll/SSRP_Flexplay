# FlexPlay glove: audit, what changed, how to deploy

Project: SSRP (EE, IIT Gandhinagar), due 10 Nov 2026. Files reviewed: `Game.cpp` (ESP32 firmware), `Game_website.html` (FlexPlay page), Lahiri et al. 2013, the course brief and the TremorFree proposal.

## 0. Status in one table

| Part | File | How far it was checked |
|---|---|---|
| Firmware | `Game_v2.cpp` | Compiled and run on a PC against a simulated ESP32 + 3 × ADS1115 (`tests/fw_host_test.cpp`, 5 scenarios, all pass). **Not run on a real ESP32.** |
| Web page | `Game_website_v2.html` | Run in headless Chromium with a virtual glove feeding checksummed lines (`tests/e2e_page.js`, 43 checks pass). **Not tried with real Web Serial or a real glove.** |
| Firmware ↔ page | `tests/crosscheck_fw.js` | 1 800 firmware-generated lines parsed by the page with 0 rejects. |
| Analysis | `analyze_flexplay.py` | Run on synthetic data for all subcommands. Real data will behave differently. |
| Unity | `unity/GloveSerialReader.cs`, `unity/PinchBallGame.cs` | **Never compiled** (no Unity/C# compiler here). Treat as a starting point. |

## 1. What was wrong or risky (original code), by severity

| # | Where | Problem | Why it matters in real use | Severity | Fix in v2 |
|---|---|---|---|---|---|
| 1 | Firmware | `while(!conversionComplete()){}` busy-wait with no timeout | One loose I2C wire or missing ADS freezes the whole ESP32 (watchdog reset or silent hang). Gloves flex wires all day. | High | Own minimal ADS1115 driver, bounded waits, error codes, bus reset, hot-plug re-probe every 1 s |
| 2 | Firmware | No integrity check on lines | A glitched byte becomes a plausible wrong number (e.g. `1800` → `180`) and is treated as real movement | High | `seq,t_ms,…,flags*HH` XOR checksum; page rejects bad lines and counts them |
| 3 | Firmware | No sequence number | Lost lines are invisible; you cannot tell data loss from "no movement" | High | `seq` field, page reports lost / corrupt counts and writes them into every CSV header |
| 4 | Firmware | Sensor fault looks like data (reads 0 or stale) | Game keeps running on garbage, results are wrong but look fine | High | `flags` bits 0–2 per board; page pauses the game and says which board failed |
| 5 | Firmware | 115200 baud, ~55–70-character text lines at 100 Hz (≈ 6–7 kB/s of 11.5 kB/s) | About half the link is used; any burst or stall delays lines and adds timing jitter | Medium | 460800 baud, 1 kB TX buffer, scheduler that resyncs after a stall instead of bursting |
| 6 | Firmware | `nextTick += PERIOD` scheduler with no resync, 8 blocking conversions per tick | After a stall it fires many catch-up ticks back to back (a burst), and the page time-stamps by arrival, so the burst becomes fake timing | Medium | Fixed 10 ms scheduler, pipelined conversions, overrun flag (bit 3) |
| 7 | Firmware | Thumb flex and ball read through the ESP32 ADC | ESP32 ADC is non-linear and noisy (tens of mV), and it is disturbed by Wi-Fi/BT | Medium | 8× oversampling + range flag; optional third ADS1115 at 0x4A recommended (see §3) |
| 8 | Page | Serial opened with the default 255-byte buffer at 115200 | A 50 ms stall of the browser drops bytes silently | High | `bufferSize 65536`, line reassembly with cap |
| 9 | Page | One fixed 2nd-order 5 Hz Butterworth on every channel, applied before everything, raw never kept; no despiking; parser accepts any line with ≥ 11 numbers | Brief needs a raw-vs-processed comparison. A single spike passes straight into the game. A truncated field (`1800` → `180`) is accepted as real | High | Selectable none / moving average / Butterworth 2nd / 4th, optional median-of-3 despiker, separate cut-offs for flex / fingertip / force, delay shown, raw and filtered both kept, strict parser |
| 10 | Page | Game clock = wall clock | A cable glitch or a hidden tab counted against the player and corrupted timings | High | Single game clock that freezes on signal loss; pause overlay; auto-resume |
| 11 | Page | No auto-reconnect | Re-plugging the cable mid-session meant a lost session | Medium | `getPorts()` auto-reconnect, connect/disconnect events, "Reconnect glove" button |
| 12 | Page | Calibration took extremes (max) | One spike or glitch sets the range for the whole session | Medium | Steadiness check on the flat pose; 85th / 90th percentile for fist, grip, pinch |
| 13 | Page | CSV had only derived values (finger fraction, N), no mV, no raw/filtered pair, no device time, no participant id in the file | Brief needs raw and processed signals; arrival-time stamps hide device jitter | High | Raw + filtered mV (10 channels), seq, device time, in three CSVs with a `#` header (filter, cut-offs, lost lines) |
| 14 | Page | Fixed difficulty | Lahiri 2013 is about adaptation from performance | Medium | Easy / Medium / High, move up / same / down with ≥ 70 % of max score rule, +6 / −3 scoring, fatigue proxy, pain rise → one level down |
| 15 | Page | No participant code in files or history | Cannot separate participants or anonymise properly | Medium | Anonymous participant code, consent line, no names stored, warning before closing with un-exported data |
| 16 | Page | Noise could not be examined | Brief: identify noise, FFT / MAV / RMS | High | **Signal Lab** screen: live raw vs filtered scope, FFT, condition-labelled recordings ≥ 5 min exported as CSV |
| 17 | Whole project | The game is a web canvas game | The brief asks for a **Unity VR** game | High (grading) | Unity scripts in `unity/`. Check with the instructor whether the web game is acceptable as the clinical demo, with Unity as the graded game. |

Bug found by the page test and fixed in this version: the first samples after a signal drop were time-stamped about one pause-length too late (resume was resolved on the next animation frame, not on the sample). Resume is now resolved in `handleLine` before stamping.

## 2. Things that cannot be fixed in software (be honest in the report)

* **Mains pickup aliases at 100 Hz.** 50 Hz sits exactly at Nyquist; 100 Hz and 200 Hz fold to DC (a slow beat). A digital low-pass **cannot remove** what has already aliased. Fix it in hardware: twisted or shielded sensor leads, short wires, a common ground, RC low-pass (e.g. 1 kΩ + 100 nF ≈ 1.6 kHz) at each ADC input, and measure the noise floor with the glove on and off the body. The ADS1115 at 860 SPS averages internally, which helps, but do not rely on it.
* **FSR absolute force is not accurate.** Typical ±10–25 %, creep under constant load, depends on contact area. The game uses relative targets (percent of your own calibrated comfortable force). Do not quote Newtons as measurements without the `fsrcal` fit and its error.
* **Filter lag is a trade-off, not a bug.** Butterworth 2nd order at 5 Hz is ≈ 45 ms; 4th order ≈ 83 ms (measured 40 / 80 ms in the page test). More smoothing = more lag. The Signal Lab and `analyze_flexplay.py filters` show this on your own data.
* **Small samples.** With n ≈ 4–12 you only detect large effects. Report effect size and confidence interval, not "significant therefore works". No clinical claims.

## 3. Hardware changes that make it deployable

1. I2C pull-ups 2.2–4.7 kΩ to 3.3 V at the ESP32 (the breakout boards' own pull-ups in parallel may be too weak at 400 kHz with long wires).
2. 100 nF at each ADS1115 supply pin, 470 µF bulk near the ESP32 (USB cable drop and Wi-Fi/BT bursts).
3. Optional third ADS1115 at 0x4A (ADDR to SCL) for the thumb flex and the ball channel, so everything is read by the same 16-bit converter. Without it the code falls back to the ESP32 ADC.
4. Measure the real 3.3 V rail with a multimeter and enter it in Therapist settings (`vcc`). The FSR conversion depends on it.
5. Strain relief where wires leave the glove, and solder (not jumper wires) on anything that bends.
6. Wi-Fi/Bluetooth stay off (the firmware does not use them).
7. Use a short, good USB cable (data, not charge-only). A bad cable gives exactly the "no data / reconnect" symptoms the page reports.

## 4. Workflow

### Before the sensors arrive (do all of this now)
1. Open `Game_website_v2.html` in Chrome from `http://localhost` (`python3 -m http.server`) or as a file. Press **Keyboard simulator**, play all five games, download the three CSVs and read them.
2. `python3 analyze_flexplay.py synth --out demo`, then run `noise`, `filters`, `stats`, `fsrcal` on it. This is your rehearsal and also gives you the figures' layout for the report. Label them synthetic.
3. Compile and flash `Game_v2.cpp` to the bare ESP32 (no sensors). The page should say the boards are missing (flags) and **must not hang**; that proves item 1 of §1.
4. Open the Unity project, add the two scripts, fix compile errors, press Play, use the keyboard simulator.
5. Write the protocol (below) and get instructor approval for: web game vs Unity, anonymous participants, and who the participants are.
6. Assign the four members' parts so the video can show per-member contributions (brief requirement).

### When the sensors arrive
1. **Continuity first** (multimeter): every wire end to end, no shorts to 3.3 V / GND, before powering.
2. Power the ESP32 alone, flash, check `# status` line: `ads0=1 ads1=1 ads2=?`.
3. One sensor at a time: connect, look at the Signal Lab scope, confirm it moves the right way.
4. **Noise floor**: Signal Lab → "Still (noise floor)", glove on a table, then worn but still, then with mains devices nearby, 5 min each. Export. Run `noise`.
5. If the share of 40–50 Hz power is high or there is a slow beat: apply the §3 wiring fixes, record again, keep both files (before/after is a good result).
6. **Filter choice**: record "Slow flexion" and "Fast flexion" 5 min each; run `filters`; choose the filter by your lag limit (e.g. ≤ 100 ms), not by feel.
7. **FSR calibration**: known masses (0, 100, 200, 500, 1000, 2000 g), 3 readings each → `fsrcal`.
8. **Session**: calibrate, play, export. Repeat with a second person.
9. **Experiment**: counterbalanced raw vs filtered (§5), then `stats`.
10. Record the 15-min video from the checklist in §6.

## 5. Test protocol (maps to the rubric)

* **Conditions** (each ≥ 5 min): still, slow flexion, fast flexion, pinch / grip holds, plus a "noise injected" condition (mains device or hand movement of the cable). Label them in the Signal Lab.
* **Raw vs processed**: each participant plays the same Unity task in 2 raw + 2 filtered blocks (`PinchBallGame`), order alternated across participants (RAW first for half). Outputs per trial: response time and mean absolute error. Analysis: `stats` on per-participant means.
* **Metrics named in the brief**: FFT (Signal Lab + `noise`), MAV and RMS (printed by `noise` and `filters`), movement accuracy and response time (Unity trial CSV).
* **Safety**: pain 0–10 before and after, stop when pain rises ≥ 2 (the page drops the level). Not a medical device; healthy volunteers only unless you have approval.

## 6. Video checklist (15 min)
Problem and sensors (2) · glove build and wiring fixes, with the noise before/after (3) · signal chain and why that filter (3) · live demo: raw vs filtered in the game (3) · results and stats with limits stated (2) · each member says what they did (2).

## 7. Risk table

| Risk | Likelihood | Effect | Mitigation | Check |
|---|---|---|---|---|
| Intermittent I2C | High on a worn glove | Dropouts | Pull-ups, short wires, solder, strain relief; firmware recovers, page pauses | Unplug / replug test |
| Mains pickup | Medium | Noisy flex values | Twisted pair, RC at input, measure | `noise` mains share |
| Baseline drift (flex sensors, temperature) | High | Targets shift during session | Re-calibrate between games; percentile calibration; autocorrelation check in `noise` | drift/min column |
| FSR creep and spread | High | Force numbers wrong | Relative targets, per-sensor `k` | `fsrcal` R² |
| Chrome-only Web Serial | Certain | Fails in Firefox/Safari | Use Chrome/Edge on desktop | status line |
| Unity serial on macOS/Linux | Medium | Port name differs | Set `portName` | status line |
| Browser tab throttled / hidden | Medium | Timing | Game clock pauses; test in foreground | pause events |
| Instructor rejects web game | Medium | Marks | Ask early; Unity path is ready | email |
| Participant privacy | Low | Ethics | Codes only, no names, consent line, anonymised CSVs | — |

## 8. Not tested (read before the demo)
* Real ESP32 timing, real ADS1115 I2C behaviour, real Web Serial in Chrome (`navigator.serial` does not exist in headless Chromium; the test fed lines directly into `handleLine`).
* Unity scripts: not compiled. Needs `Api Compatibility Level = .NET Framework`.
* Synthetic data only for the analysis script.
* Firmware timing numbers (max acquisition ≈ 6.9 ms) come from a simulation model of conversion time, not from hardware.
