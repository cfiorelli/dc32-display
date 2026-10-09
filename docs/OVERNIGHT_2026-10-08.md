# Overnight work — night of 2026-10-08

All host-side (no firmware flashed while you were away — safer, and anything host-side is
instantly revertable). Each item is a merged PR, deployed, and tested.

## Shipped (5)
1. **Stability: heartbeat (PR #42).** A background thread pings the badge whenever nothing has
   been sent for ~0.8 s, so a starved main tick (heavy SDR/bench CPU on this PC) can't let the
   badge hit its 3 s host-timeout. Proof: since deploy, 20 slow ticks occurred but **0** of the
   "disconnected" flickers they used to cause.
2. **Lights overhaul (PR #43).** New modes on Ctrl+Alt+G: breathe, comet, fire, police, and a
   **live CPU-load meter** (9 LEDs fill green→red with real PC load).
3. **Command menu type-to-jump (PR #44).** In Ctrl+Alt+M, press a letter to jump to that command.
4. **System monitor view (PR #45), Ctrl+Alt+U.** Clock, per-core CPU bars, RAM, disk, network
   throughput, CPU temp, load, uptime.
5. **Clock screensaver (PR #46), Ctrl+Alt+C.** Big drifting clock (anti LCD image-retention) with
   date and uptime/temp. Optional idle auto-show: set `idle_clock_s` in config (default 0 = off).

## Ready for you to do hands-on (you said you like wires/solder) — I did NOT flash these overnight
These are firmware + a little circuitry; best done with you around to power-cycle if needed.
- **FT-60 audio → badge ADC (the SAO hack).** Feed the radio's speaker audio into GPIO28/29 on the
  6-pin header through a 1-10 µF cap + two 100 kΩ bias resistors + a 10 kΩ series resistor; firmware
  samples the ADC at ~16 kHz and streams it to the PC as a USB audio input. No splitter/sound-card
  needed. I can write + flash the firmware when you're up; you solder the 3-part bias network.
- **IR record → SD → replay.** Firmware raw-IR TX so the badge replays any captured remote (TV, etc.)
  from the IR scope — badge as a universal remote. Needs a real remote to verify.
- **Accelerometer streaming → bubble level + tilt-reactive lights.** Small firmware addition (same I2C
  path as taps); host does a spirit-level view and tilt-driven LED effects.

## Notes
- Badge left at: zoom 1:1 (readable), lights off, mirror view.
- `psutil` confirmed in the daemon venv (powers the CPU lights + system monitor).
- The "slow ticks" are CPU starvation, largely from my overnight builds + the SDR; harmless now
  that the heartbeat keeps the link alive, but the FT-60 audio-via-badge hack would also offload
  nothing — unrelated.
