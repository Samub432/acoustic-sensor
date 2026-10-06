# Arduino test: hardware checks

Two small sketches to check the electronics **before the first session or after a repair**.
Upload one, open the Serial Monitor at **115200 baud**, check the numbers, then upload `Acoustic_Sensor.ino` again before measuring.

| Sketch | What it does | What it checks |
|---|---|---|
| `test_pin/test_pin.ino` | Prints the raw value of A0 every 20 ms | Front-end bias and the wiring to A0 |
| `test_TX_TR/test_TX_TR.ino` | Plays a continuous 4 kHz tone on pin 9 and prints `min  max  peak-to-peak` of A0 every ≈ 23 ms | That sound gets from the TX disc to the RX disc |

## test_pin: expected result

- A steady value around **505–512** (the 2.5 V bias), moving by a few counts.
- Stuck near **0** or **1023**, or drifting: bias missing or op-amp saturated. Pin 3 of the LM358 must read 2.5 V.

## test_TX_TR: expected result

You will hear a whine from the TX disc: that is normal.

| Condition | Peak-to-peak (third column) |
|---|---|
| Discs in air, apart | Small, around 10 ADC |
| Discs on the water-filled flask with couplant | Clearly higher, tens to hundreds of ADC |
| Any condition | `max` never stuck at a fixed ceiling, `min` never at 0 |

- High value with the discs apart: electrical crosstalk; separate the TX and RX wires.
- No increase when coupled: check pin 9, the 100 Ω resistor and the couplant.

These are go/no-go checks, not calibrations: the exact numbers depend on the discs and on how firmly they are pressed.