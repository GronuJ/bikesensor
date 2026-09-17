# Pin verification sheet

Generated from `bikesensor_pcb/bikesensor_pcb.kicad_pcb` (the actual routed netlist), cross-referenced
against the firmware constants in `firmware/bikesensor/bikesensor.ino`.

**Why this file exists.** The schematic uses generic `Conn_01x0N_Socket` symbols — "ESP32_Left",
"MPU6050", "SD_Breakout" are only *Value* strings, not real parts with named pins. So the mapping
from header pin number to ESP32 GPIO exists nowhere the tools can check; it lives in the README
table and the firmware `constexpr`s, and neither ERC nor DRC can validate it. DRC passing clean
means nothing here.

**Update (2026-09-17).** The board now carries 42 silkscreen labels — a short signal name beside
every connected pin on J1–J5, plus `VBAT`, the J7 switch feed, and module names (`ESP32-C3`, `IMU`,
`SD`, `GPS`, `SHIELD`). Abbreviations: `GTX`/`GRX` = GPS TX/RX, `DTX`/`DRX` = debug UART,
`ADC` = battery divider. These are generated from the *routed netlist*, so they describe what the
copper actually does — but they still say nothing about which ESP32 GPIO lands on which pad, so the
bench procedure below remains necessary.

---

## What the board actually connects

Net names are as routed. "firmware" is what `bikesensor.ino` expects that signal to be.

| Conn | Pin | Net | Firmware expects |
| --- | --- | --- | --- |
| **J1** ESP32_Left | 1 | `+5V` | — |
| | 2 | `GND` | — |
| | 3 | `+3V3` | — |
| | 4 | `GPIO_9` | (unused stub) |
| | 5 | `GPIO_8` | (unused stub) |
| | 6 | `I2C_SCL` | GPIO7 |
| | 7 | `I2C_SDA` | GPIO6 |
| | 8 | `SPI_MISO` | GPIO5 |
| **J2** ESP32_Right | 1 | `SPI_SCK` | GPIO4 |
| | 2 | `SPI_MOSI` | GPIO3 |
| | 3 | `SPI_CS` | GPIO2 |
| | 4 | `GPS_TX` | GPIO10 (ESP32 **RX**) |
| | 5 | `BATTERY_ADC` | GPIO0 |
| | 6 | `UART_TX_DEBUG` | (GPIO21?) |
| | 7 | `UART_RX_DEBUG` | (GPIO20?) |
| | 8 | `GPS_RX` | GPIO1 (ESP32 **TX**) |
| **J3** MPU6050 | 1–8 | `+3V3`, `GND`, `I2C_SCL`, `I2C_SDA`, `XDA`, `XCL`, `AD0`, `INT` | |
| **J4** SD_Breakout | 1–6 | `GND`, `+3V3`, `SPI_MISO`, `SPI_MOSI`, `SPI_SCK`, `SPI_CS` | |
| **J5** NEO-6M | 1–4 | `+5V`, `GPS_TX`, `GPS_RX`, `GND` | |

Geometry: J1 at x=50.80, J2 at x=66.04, both 8 pins on 2.54 mm pitch running +y.
Row spacing 15.24 mm (600 mil).

---

## The thing to check first

J1 pins 1–3 are `+5V, GND, +3V3`, and pins 4–5 are labelled `GPIO_9`, `GPIO_8`. Read together,
those labels imply the designer assumed the **left row descends**:

```
J1:  5V   GND   3V3   IO9   IO8   IO7   IO6   IO5
                            SCL   SDA   MISO      <- and firmware wants 7, 6, 5
```

If that is the real pinout of your SuperMini, **J1 is correct** — SCL=7, SDA=6, MISO=5 all line up.

Applying the same descending logic to J2 gives `IO4, IO3, IO2, IO1, IO0, IO21, IO20, IO10`, which
lines up for SCK=4, MOSI=3, CS=2, BATTERY_ADC=0 — but puts:

- `GPS_TX` (J2.4) on **GPIO1**, while firmware reads GPS on **GPIO10**
- `GPS_RX` (J2.8) on **GPIO10**, while firmware transmits on **GPIO1**

> **⚠ Unconfirmed.** This is inferred from the `GPIO_9`/`GPIO_8` labels plus the firmware constants,
> **not** from the design files, because the design files contain no ESP32 pin names. There are
> several ESP32-C3 SuperMini pinout variants in circulation, and I do not know which one this board
> was laid out against, nor which end of each footprint is pin 1 physically.
>
> If it holds, GPS TX/RX are swapped and the GPS would never produce a single NMEA sentence —
> a clean single-fault explanation for a dead build. **Verify with a multimeter before believing it.**

---

## Bench procedure

With the ESP32 module **removed** from its sockets, set a multimeter to continuity and probe from
each module *pad on the board* to the peripheral header:

1. Identify J1 pin 1 physically — it is the pad connected to `+5V`, which also goes to J5.1 and SW1.
   Confirm J1.2 = GND and J1.3 = +3V3. That anchors the orientation.
2. For each row, write down which SuperMini silkscreen label (5V / G / 3V3 / IO0…IO21) sits over
   each pad once the module is seated.
3. Fill the "actual GPIO" column below and compare against "firmware expects" in the table above.

| Net | Firmware expects | Actual GPIO (measure) | Match? |
| --- | --- | --- | --- |
| `I2C_SCL` | GPIO7 | | |
| `I2C_SDA` | GPIO6 | | |
| `SPI_MISO` | GPIO5 | | |
| `SPI_MOSI` | GPIO3 | | |
| `SPI_SCK` | GPIO4 | | |
| `SPI_CS` | GPIO2 | | |
| `GPS_TX` → ESP RX | GPIO10 | | |
| `GPS_RX` → ESP TX | GPIO1 | | |
| `BATTERY_ADC` | GPIO0 | | |

Any mismatch is fixable in firmware alone (change the `constexpr`s) as long as the pin is capable of
the function — with two exceptions:

- **ADC:** `BATTERY_ADC` must land on an **ADC1** channel (GPIO0–GPIO4 on the C3). ADC2 does not work
  while Wi-Fi is active, so if the divider ended up on GPIO5+ it cannot be fixed in software.
- **Strapping:** GPIO2, GPIO8, GPIO9 are strapping pins and must be high/floating at reset.

---

## Other findings (detail in the plan)

- **H2** `SPI_CS` is on GPIO2, a strapping pin. SD modules often hold CS low at power-up, which can
  block boot. Add a 10k pull-up to 3V3, or move CS in the respin.
- **H3** The `R1`/`R2` divider hangs off `/VBAT` *upstream* of SW1, so ~2.1 V sits on GPIO0 of an
  unpowered ESP32 whenever a cell is connected and the switch is off. Leakage path into the ESD
  clamp, plus ~21 µA permanent drain. Move it downstream of the switch.
- **H4** `+3V3` is sourced from the SuperMini's onboard LDO through a header pin, feeding the
  MicroSD (100–200 mA write bursts) with only C1 10 µF + C2 100 nF. Scope this rail during a write
  burst before blaming firmware. Raise bulk to 22–47 µF near the SD socket.
- **H5** NEO-6M is powered from `+5V` with no level shifting or series resistors on the UART. Safe
  only if the breakout has its own regulator and 3.3 V logic (GY-NEO6MV2 does). Add ~1k series
  resistors on both lines regardless.
- **H6** No I2C pull-ups on the carrier (relies on the GY-521's); `AD0`/`INT` unterminated; `J6` is
  8 entirely unconnected pins; `GPIO_8`/`GPIO_9` are unterminated stubs on strapping pins.

## Before the respin

Build a real ESP32-C3 SuperMini schematic symbol with named pins, re-derive the netlist, and diff it
against the firmware constants. Signal names are now on the silkscreen (done 2026-09-17). Once the
symbol is real, this file becomes unnecessary, which is the point.

**Both pre-fabrication blockers are now closed (2026-09-17):**

1. ~~`+3V3` does not reach C1 pin 1.~~ Routed. The bulk capacitor is connected.
2. ~~R3, the CS pull-up (H2), is not in the design.~~ Added: 10 kΩ from `/SPI_CS` to `+3V3`,
   which resolves **H2**.

`production/` was re-plotted and `bikesensor.zip` rebuilt from it — verified to contain the
38.74 × 114.47 mm outline. KiCad DRC reports 0 errors and 0 unconnected pads.

**H3, H4, H5 and H6 are still open** and were never design changes, only cautions:
H3 (divider upstream of the switch) and H5 (no series resistors on the GPS UART) are unchanged in
this revision. H4 is partly mitigated — C1 is now actually connected, but it is still 10 µF, not the
22–47 µF recommended above.
