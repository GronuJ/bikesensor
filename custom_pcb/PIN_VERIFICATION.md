# Pin verification sheet

Generated from `bikesensor_pcb/bikesensor_pcb.kicad_pcb` (the actual routed netlist), cross-referenced
against the real ESP32-C3 SuperMini pinout and the firmware constants in
`firmware/bikesensor/bikesensor.ino`.

**Why this file exists.** The schematic uses generic `Conn_01x0N_Socket` symbols — "ESP32_Left",
"MPU6050", "SD_Breakout" are only *Value* strings, not real parts with named pins. So the mapping
from header pin number to ESP32 GPIO exists nowhere the tools can check, and neither ERC nor DRC can
validate it. DRC passing clean means nothing here.

---

## Result (2026-09-22): J1/J2 were routed against the wrong pinout

The SuperMini's two rows, counted from the USB-C end, are:

```
row A:  5V   GND  3V3  IO4  IO3  IO2  IO1  IO0
row B:  IO5  IO6  IO7  IO8  IO9  IO10 IO20 IO21
```

Sources: the pad/pin tables in the [mrtnvgr/KiCad_ESP32-C3-SuperMini](https://github.com/mrtnvgr/KiCad_ESP32-C3-SuperMini)
footprint and symbol (pad 8 = 5V and pad 9 = IO5 share the same end, rows 15.24 mm apart), and the
[components101 pin table](https://components101.com/development-boards/esp32c3-mini-development-board-datasheet-pinout).
The same labels are printed on the module itself.

J1.1–J1.3 are `+5V, GND, +3V3`, so J1 is row A and J1.1 is the USB end. J2.1 sits directly across from
J1.1, so it is IO5 — this holds whether the module is seated face up or face down, because 5V and IO5
are at the same end of the module. The board's `GPIO_9`/`GPIO_8` net names came from a pinout that
does not exist; the copper actually does this:

| Conn.pin | Net (silkscreen) | SuperMini pin | Hand-wired prototype used | Carrier firmware |
| --- | --- | --- | --- | --- |
| J1.1 | `+5V` | 5V | | |
| J1.2 | `GND` | GND | | |
| J1.3 | `+3V3` | 3V3 | | |
| J1.4 | `GPIO_9` (stub) | **IO4** | SPI SCK | free |
| J1.5 | `GPIO_8` (stub) | **IO3** | SPI MOSI | battery ADC, *after the bodge below* |
| J1.6 | `I2C_SCL` | **IO2** | SPI CS | I2C SCL |
| J1.7 | `I2C_SDA` | **IO1** | GPS (ESP TX) | I2C SDA |
| J1.8 | `SPI_MISO` | **IO0** | battery ADC | SPI MISO |
| J2.1 | `SPI_SCK` | **IO5** | SPI MISO | SPI SCK |
| J2.2 | `SPI_MOSI` | **IO6** | I2C SDA | SPI MOSI |
| J2.3 | `SPI_CS` | **IO7** | I2C SCL | SPI CS |
| J2.4 | `GPS_TX` | **IO8** (onboard LED) | LED | GPS UART, direction auto-detected |
| J2.5 | `BATTERY_ADC` | **IO9** (BOOT) | — | **none: IO9 has no ADC** |
| J2.6 | `UART_TX_DEBUG` | IO10 | GPS (ESP RX) | free |
| J2.7 | `UART_RX_DEBUG` | IO20 | | free |
| J2.8 | `GPS_RX` | IO21 | | GPS UART, direction auto-detected |

Not one signal lands where the original firmware expected it. The hand-wired prototype followed the
firmware, which is why it worked (the 2026-09-16 bench notes — divider on GPIO0, MISO on GPIO5, SDA
on GPIO6 — describe that hand wiring, not this PCB).

**What firmware absorbs.** The C3 routes SPI, I2C and UART through its GPIO matrix to any pin, so the
default firmware build (`pio run -d firmware`) now uses the "Carrier firmware" column. The original
map lives on as `pio run -d firmware -e handwired`. The GPS UART tries both carrier pins at boot and
uses whichever one carries NMEA, so the GPS breakout's header order (VCC-TX-RX-GND vs VCC-RX-TX-GND
both exist) does not matter.

**What firmware cannot absorb: the battery sense.** `BATTERY_ADC` lands on IO9, which has no ADC
channel, and IO9 is the BOOT strapping pin: it must read high at reset or the chip enters download
mode instead of running. With R1/R2 fitted, the divider (VBAT/2 through 50 kΩ) fights the ~45 kΩ
internal pull-up; that works out to about 2.5 V at a 3.3 V cell against a 2.48 V input-high threshold,
so a low battery could stop the logger booting.

### The one bodge (do it while assembling)

1. Before soldering J2, **clip off leg 5** of that 8-pin female header (or push its contact out). The
   module's IO9 then touches a contact that goes nowhere.
2. On the bottom side, solder a short wire from the **J2.5 pad** (still carrying `BATTERY_ADC` from
   R1/R2/C3) to the **J1.5 pad** (IO3, ADC1 channel 3; the `GPIO_8` stub has nothing else on it).

Result: battery sense on IO3, IO9 left to its internal pull-up and the BOOT button. The firmware's
carrier build already reads the battery on GPIO3.

If you would rather skip it: still clip leg 5, and leave R1 unfitted. The logger works;
`battery_pct` in the CSV is meaningless (IO3 floats).

### Seat the module the right way round

The module's **5V pin goes into J1.1** (square pad, next to the `+5V`/`GND`/`3V3` silkscreen). Rotated
180°, the module's 5V pin would sit on `GPS_RX` and its IO pins on the supply rails. Before powering
up, check that the labels printed on the module match row A/row B above. Jost confirmed on
2026-09-22 that the module he ordered (AliExpress "ESP32 C3 SuperMini Development Board") has this
pin order, so the mapping above applies to it.

### Strapping pins at reset, as actually wired

| Pin | Needs at reset | On the carrier | OK? |
| --- | --- | --- | --- |
| IO2 | high | I2C SCL, pulled up by the GY-521's own pull-ups | yes |
| IO8 | high only for download mode | GPS UART line, idles high; onboard LED pulls it up too | yes |
| IO9 | high | internal pull-up + BOOT button, once leg 5 is clipped | yes, after the bodge |

---

## Cautions, re-evaluated against the real mapping

- **H2 — closed.** `SPI_CS` is on IO7, not the strapping pin GPIO2. Fit R3 anyway: it keeps the card
  deselected while the ESP32 boots.
- **H3 — accepted for this revision.** The R1/R2 divider still hangs off `/VBAT` upstream of SW1.
  Cost: ~21 µA permanent drain (years on any LiPo) and up to ~36 µA through R1 into the unpowered
  chip's pin clamp, far inside what the clamp tolerates. Move it downstream of SW1 in the respin.
  Leaving R1 unfitted removes it entirely.
- **H4 — fit a bigger C1.** Use 47 µF (≥ 6.3 V, 5 mm diameter, 2.0 mm lead pitch) instead of 10 µF. The
  3V3 rail comes from the SuperMini's LDO, rated for about 250 mA external load, and SD writes burst
  to 100–200 mA.
- **H5 — closed if the GPS is a GY-NEO6MV2.** That breakout has its own 3.3 V regulator (MIC5205), so
  its UART runs at 3.3 V even though it is fed 5 V. The 1 kΩ series resistors are optional. A bare
  NEO-6M or a 5 V-logic breakout would not be safe.
- **H6 — closed.** The GY-521 supplies the I2C pull-ups and pulls AD0 low (address 0x68); INT is unused.
  The `GPIO_8`/`GPIO_9` "stubs" are really IO3/IO4, not strapping pins. J6 is mechanical only.

### New, found in this pass

- **Which MicroSD breakout?** J4 feeds it `+3V3`. The common 6-pin module with an AMS1117 regulator and
  a 74LVC125 level shifter wants 5 V on VCC; at 3.3 V in, the card gets roughly 2.2–2.5 V and often
  fails to mount. If yours has a 3-pin SOT-223 regulator on it, bridge that regulator's input to its
  output, or feed J4.2 from `+5V` instead. A breakout without a regulator is fine as wired.
- **The onboard LED is gone.** GPIO8 is a GPS UART line on this board, so the carrier firmware never
  drives it. The LED will flicker once a second if the GPS happens to transmit on that pin.
- **Flash with SW1 off.** USB then powers the module and GPS through the module's 5V pin without
  back-feeding the battery shield's boost output.

---

## Bill of materials

Derived from the footprints in `bikesensor_pcb.kicad_pcb`, not from memory.

| Qty | Ref | Part | Footprint / note |
| --- | --- | --- | --- |
| 1 | — | ESP32-C3 SuperMini | seats across J1/J2, rows 15.24 mm (600 mil) apart, **5V pin in J1.1** |
| 1 | — | GY-521 (MPU-6050) | J3. **Supplies the I2C pull-ups** — the carrier has none |
| 1 | — | GY-NEO6MV2 GPS | J5. Must have its own regulator and 3.3 V logic; it is fed from 5 V with no level shifting |
| 1 | — | MicroSD SPI breakout | J4. Fed 3.3 V — see "Which MicroSD breakout?" above |
| 1 | — | Wemos D1 Mini TP5400 battery shield + LiPo | J6/J7/J8, rows 22.86 mm (900 mil) apart |
| 5 | J1 J2 J3 J6 J7 | 1x08 female header, 2.54 mm | **J2: clip leg 5** (the bodge). J6 is 8 electrically dead pins |
| 1 | J4 | 1x06 female header, 2.54 mm | |
| 1 | J5 | 1x04 female header, 2.54 mm | |
| 1 | J8 | 1x01 female header, 2.54 mm | VBAT sense |
| 2 | R1 R2 | 100 kΩ axial | `R_Axial_DIN0207`, 7.62 mm pitch |
| 1 | R3 | 10 kΩ axial | 7.62 mm pitch. CS pull-up |
| 1 | C1 | 47 µF electrolytic, ≥ 6.3 V | `CP_Radial_D5.0mm_P2.00mm`. 10 µF fits but is marginal — see **H4** |
| 2 | C2 C3 | 100 nF ceramic disc | `C_Disc_D5.0mm`, 5.00 mm pitch |
| 1 | SW1 | **C&K OS102011MS2Q** | See below — not a generic part |
| — | — | ~3 cm hookup wire | The J2.5 → J1.5 bodge |
| 2 | — | 1 kΩ axial *(optional)* | Series resistors on the GPS UART, **H5**. No footprint; fit inline on the wires |

**Use sockets, not direct soldering.** Being able to unseat the ESP32 is what allowed the prototype's
fault to be localised in software when no multimeter was available.

### SW1 is not substitutable

The footprint is **2.0 mm pitch**, three signal pins, with two mounting pegs 8.2 mm apart. A generic
2.54 mm slide switch **will not fit**. Buy the C&K part (or a pin-compatible C&K OS-series device)
from a distributor that carries C&K — Mouser, Digi-Key, Farnell or RS.

If you cannot get it, the board still works: solder a wire jumper between SW1 pads 1 and 2 to
hard-wire the 5 V rail on, or run flying leads to any SPDT switch off-board. You lose only the
power cut-off, not any function.

### PCB

2-layer, **38.74 x 114.47 mm**, 1.6 mm, HASL is fine — everything is through-hole with no fine
pitch. Note the board is longer than 100 mm, so it falls outside the cheapest fixed-price tier at
most prototype fabs. Upload `production/bikesensor.zip`.

---

## Before the respin

Replace the two generic sockets with a real SuperMini symbol and footprint (the mrtnvgr library above
has both), re-derive the netlist, and diff it against the firmware constants. Keeping the carrier
firmware map avoids re-routing most nets; the minimum respin change is moving `BATTERY_ADC` from
J2.5 (IO9) to J1.5 or J1.4 (IO3/IO4) and putting the divider downstream of SW1 (H3). Once the symbol
is real, this file becomes unnecessary, which is the point.

Closed on 2026-09-17: `+3V3` now reaches C1 pin 1, and R3 is in the design.
