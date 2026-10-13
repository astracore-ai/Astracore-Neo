# Power from recorded activity, drop 0.43 (stream A3)

The routed blocks' power (`pnr_drop043.md`) at the flow's default switching activity and with the activity recorded
from the cocotb runs, side by side. **Conditions:** ASAP7 7.5T RVT, SS corner, 0.63 V, 100 °C (the platform's WC NLDM and
power tables, the corner the synthesis job times with); the blocks placed and routed at **1,250 ps (800 MHz)**; OpenSTA's
`report_power` on the final database with OpenRCX parasitics and the propagated clock tree (for `neo_core`, whose run
stopped in placement, on the floorplan database: its cells alone); the memory macros as FakeRAM2.0's liberty models
(their clock and leakage power; FakeRAM2.0 models no data-dependent read / write energy). ASAP7 is a predictive
academic PDK: the milliwatts are the library's at its slow corner, the shares and the ratios are the design's.

## 1. The recordings

| | the core's recording | the tile's recording |
| --- | --- | --- |
| run | cocotb `core` on the 16 × 8 core (NCHK = 4): C1 (a dense 3 × 3 convolution, three channel tiles) and C5 (a 2:4 sparse convolution) | cocotb `tile` on the 2 × 2 mesh of 16 × 8 cores at 32 words per link flit (the silicon links): M1 (fetch, compute, write back), M18 (five convolutions), M19 (the INT8 drain) |
| window | the core busy (go to done): **2,858 clock cycles** | tile (0,0)'s DMA running a program: **10,306 clock cycles** |
| MAC utilization in the window (the array's input valid) | **56.7 %** | **32.8 %** (tile (0,0)'s core) |
| blocks | `c16_neo_core`, `c16_systolic_array`, `mac_pe` (the mean over the 128 data PEs), `requant`, `acc_bank`, `act_feeder`, `wbuf_mem`, `neo_core` | `tile_nic`, `noc_router`, `link_packer`, `link_unpacker`, `bank_bist_wrap`, `mbist`, `prog_mem`, `tile_dma`, `host_if`, `esm` |

Verilator 5.020's VCD of each run was streamed through a named pipe into `07_ci/pnr/activity.py` (every signal bit's
toggle count and high time inside the window), and `07_ci/pnr/power.py` put the activity on each routed block's flops
-- the synthesis flow names every flop after its register bit and the routed block keeps the names -- and inputs, as
OpenSTA `set_power_activity` per pin: the bit of the same name, or for a unit wider than the 16 × 8 runs simulate
(requant's 32 columns, the NIC's 64 sources, the 32 × 32 core) the bit at the index folded into the recorded range.
OpenSTA propagates from those pins through the combinational logic. The activity is per clock cycle and is applied at
the 1,250 ps period; the default is OpenSTA's 0.1 transitions per cycle on the inputs, propagated.

## 2. The table

Power in mW; dynamic = internal + switching. "flops recorded" counts the flops that took a recorded bit (by name or
folded index); the others keep the propagated default.

| block | stage, period | recording (window, MAC utilization) | flops recorded / all | default: dynamic + leakage = total | recorded: dynamic + leakage = total | recorded / default (dynamic) |
| --- | --- | --- | --- | --- | --- | --- |
| `neo_core` | globally placed -- place timed out (exit 124) (power on `2_floorplan.odb`: no wires, no clock tree), 1,250 ps | 2,858 cycles, 56.7 % | 164,440 / 208,992 | 139.600 + 3.0900 = **142.690** | 327.400 + 2.5400 = **329.940** | 2.345 |
| `tile_nic` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 6,035 / 8,083 | 67.700 + 0.4140 = **68.114** | 18.700 + 0.4390 = **19.139** | 0.276 |
| `noc_router` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 16,271 / 16,271 | 28.670 + 0.2330 = **28.903** | 24.250 + 0.2490 = **24.499** | 0.846 |
| `bank_bist_wrap` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 80 / 80 | 61.300 + 2.1000 = **63.400** | 35.499 + 2.1200 = **37.619** | 0.579 |
| `tile_dma` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 104 / 104 | 0.370 + 0.0010 = **0.371** | 0.109 + 0.0013 = **0.110** | 0.295 |
| `prog_mem` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 66 / 2,562 | 7.310 + 0.0328 = **7.343** | 3.719 + 0.0360 = **3.755** | 0.509 |
| `host_if` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 553 / 553 | 0.822 + 0.0056 = **0.828** | 0.688 + 0.0063 = **0.694** | 0.837 |
| `esm` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 44 / 44 | 0.053 + 0.0005 = **0.054** | 0.049 + 0.0007 = **0.050** | 0.927 |
| `link_packer` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 3,811 / 3,811 | 22.360 + 0.0467 = **22.407** | 5.360 + 0.0538 = **5.414** | 0.240 |
| `link_unpacker` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 8 / 8 | 1.196 + 0.0053 = **1.201** | 0.105 + 0.0091 = **0.114** | 0.087 |
| `requant` | routed, 1,250 ps | 2,858 cycles, 56.7 % | 6,724 / 6,756 | 64.700 + 0.1320 = **64.832** | 41.300 + 0.1400 = **41.440** | 0.638 |
| `acc_bank` | routed, 1,250 ps | 2,858 cycles, 56.7 % | 2,285 / 2,285 | 7.360 + 0.1730 = **7.533** | 8.520 + 0.1750 = **8.695** | 1.158 |
| `act_feeder` | routed, 1,250 ps | 2,858 cycles, 56.7 % | 413 / 435 | 7.190 + 0.1410 = **7.331** | 2.901 + 0.1450 = **3.046** | 0.403 |
| `wbuf_mem` | routed, 1,250 ps | 2,858 cycles, 56.7 % | 3 / 3 | 3.860 + 0.1430 = **4.003** | 2.311 + 0.1460 = **2.457** | 0.599 |
| `mbist` | routed, 1,250 ps | 10,306 cycles, 32.8 % | 77 / 77 | 0.566 + 0.0025 = **0.569** | 0.096 + 0.0041 = **0.100** | 0.169 |
| `mac_pe` | routed, 1,250 ps | 2,858 cycles, 56.7 % | 137 / 137 | 0.790 + 0.0016 = **0.792** | 0.388 + 0.0017 = **0.390** | 0.491 |
| `c16_systolic_array` | routed, 1,250 ps | 2,858 cycles, 56.7 % | 25,920 / 25,920 | 239.100 + 0.2890 = **239.389** | 63.700 + 0.3120 = **64.012** | 0.266 |
| `c16_neo_core` | routed, 1,250 ps | 2,858 cycles, 56.7 % | 28,818 / 36,739 | 96.500 + 0.9190 = **97.419** | 88.900 + 0.8610 = **89.761** | 0.921 |

Reading the table:

- **The default activity overestimates almost every block**, by 3–11 times where the recording shows a block mostly idle
  in its window: `link_unpacker` 0.087, `mbist` 0.169, `link_packer` 0.240, `c16_systolic_array` 0.266, `tile_nic`
  0.276, `tile_dma` 0.295 (recorded / default dynamic power). OpenSTA's default puts 0.1 transitions per cycle on every
  input and propagates them; the recorded array sees its operands valid 56.7 % of the window and most of their bits
  quiet between pixels, the links and the DMA wait between messages. It **underestimates one**: `acc_bank` 1.158 -- its
  33-operand row adders toggle more than the default gives them. The routing-dominated `noc_router` (0.846), `host_if`
  (0.837) and `esm` (0.927) are near their defaults: their power is mostly the clock. (`neo_core`'s 2.345 is not
  comparable: its default figure on the floorplan database is low -- its default combinational power about the routed
  16 × 8 core's for 5.7 times the logic -- while its recorded figures scale with the flops, below.)
- **The clock and the macros barely move with the activity.** The clock tree's toggling is fixed by the clock (the
  16 × 8 array: 18.6 of its 64.0 mW recorded); FakeRAM2.0's macro models carry a clock-pin internal power and leakage
  and nothing per access, so the bank's macros give 36.8 mW at the default and 36.5 recorded (16 lanes at 2.15 mW each
  at 800 MHz, 36.5 of the bank's 37.6) -- the figure a memory compiler's models would change most.
- **Leakage at SS 100 °C is small next to the dynamic power** at 800 MHz: 0.31 mW for the 16 × 8 array, 0.86 mW for the
  16 × 8 core, 2.1 mW for the bank's 16 macros.
- **Coverage.** Every flop of `mac_pe`, the 16 × 8 array, `acc_bank`, the links, the MBIST, the DMA, `host_if`, the ESM
  and the router took a recorded bit; `requant` 6,724 of 6,756 (folded from the 16 × 8 core's 8 columns to 32). Three
  figures carry defaults: `prog_mem` (2,496 of its 2,562 flops are the instruction words, which Verilator leaves out of
  the trace: its recorded figure is an upper bound), and the two cores, run before `power.py` mapped flattened arrays
  (f44284a, `07_ci/pnr/README.md`): `c16_neo_core` 28,818 of 36,739 flops recorded -- the skew / deskew delay lines'
  4,642 and the requantizers' 2,864 kept the default -- and `neo_core` 164,440 of 208,992 (20,425 by name, 144,015
  folded from the 16 × 8 recording).
- **The 32 × 32 core's figure is its cells' alone.** Its run stopped in placement, so its report ran on the floorplan
  database: no wires, no clock tree, no repair buffers. Its recorded cells scale from the routed 16 × 8 core by the flop
  count (208,992 / 36,739 = 5.69): the 16 × 8 core's recorded sequential and combinational power, 55.7 mW, gives 317 mW
  against the 32 × 32's 323 (330 less its macros' 6.8) -- which is what the routed estimate in section 3 rests on.

## 3. The figures for the spec's power line

The figures, with their conditions (ASAP7 SS 0.63 V / 100 °C, 800 MHz, the routed blocks, OpenSTA `report_power`):

| Figure | Value | Conditions |
| --- | --- | --- |
| the data PE (`mac_pe`) | 0.390 mW: 0.388 dynamic + 0.0017 leakage (default activity 0.792) | the mean activity of the 16 × 8 core's 128 data PEs over C1 + C5, 56.7 % MAC utilization |
| energy per MAC, the PE alone | **0.86 pJ** | 0.388 mW / (800 MHz × 0.567) |
| the 16 × 8 array (128 data + 64 check PEs) | 64.0 mW: 63.7 dynamic (18.6 of it the clock) + 0.31 leakage (default 239) | every flop recorded, 56.7 % |
| energy per data MAC, the array | **1.10 pJ** | 63.7 mW / (800 MHz × 128 × 0.567): the check columns (NCHK = 4: 50 % more PEs at 16 × 8, 12.5 % at 32 × 32) and the array's clock included |
| the 16 × 8 core (`c16_neo_core`) | 89.8 mW: 88.9 dynamic + 0.86 leakage (default 97.4) | 78 % of the flops recorded; the delay lines and the requantizers at the default |
| energy per data MAC, the 16 × 8 core | **1.53 pJ** | 88.9 mW / (800 MHz × 128 × 0.567): the feeder, the accumulator, the two sequencers, the two requantizers, the arrays' clock power |
| the 32 × 32 core (`neo_core`), its cells | 330 mW: 327.4 dynamic + 2.54 leakage (default 142.7) | the floorplan database (its run stopped in placement): no wires, no clock tree, no repair buffers; 164,440 of the 208,992 flops recorded, folded from the 16 × 8 core's recording |
| the 32 × 32 core, routed (estimate) | **~485 mW**: the 330 + the clock tree the routed 16 × 8 core implies (27.2 mW for its 36,739 flops → ~155 mW for 208,992) | scaled by the flop count, 5.69, which reproduces the floorplan's recorded cells within 2 % (the last note above); the wires' share of the switching not added |
| energy per data MAC, the 32 × 32 core (estimate) | **~1.0 pJ** | ~482 mW dynamic / (800 MHz × 1,024 × 0.567): the larger array carries its feeder, accumulator and requantizers over eight times the data PEs |
| the tile's other units | NIC 19.1, router 24.5, bank 37.6 (36.5 of it the 16 lane macros' clock power), packer 5.4, unpacker 0.1, program memory 3.8, the two DMA engines 0.2, `host_if` 0.7, ESM 0.05 -- **91.5 mW** | the tile's recording: tile (0,0)'s DMA running M1 / M18 / M19, its core at 32.8 % |
| the tile | **≥ 421 mW** with the core's cells alone (330 + 91.5); **~575 mW** with the core's estimated clock tree (485 + 91.5) | the core at its recording (56.7 %), the other units at the tile's (32.8 %); no wires or clock between the blocks |

Against the spec's model (§9: ~0.45 pJ/MAC system level at 0.75 V on N5A, ~56 W typical at 60 % utilization) these are
a ratio check, not a replacement: a predictive library at its slow corner and 0.63 V, 800 MHz instead of 2 GHz, and
FakeRAM2.0's macro models. The array's 1.10 pJ per data MAC at 56.7 % is 2.4 times the model's system-level figure;
the scaling of a 7 nm predictive SS corner to N5A's typical one is not known (no foundry data), which is why the spec
line should quote the figures with these conditions.

## 4. What the figures leave out

- **The macros' data-dependent energy.** FakeRAM2.0's liberty carries a clock-pin internal power and leakage, not a read
  or write energy per access, so the macro rows barely move with the activity; nor does the figure follow the macro's
  size (the 16 × 8 core's three small arrays and the 32 × 32 core's three large ones both give 6.46 mW internal). A
  memory compiler's models replace them.
- **The wires between blocks and the chip-level clock.** Each figure is one block routed on its own: the clock tree
  inside the block, no tree above it, no inter-block wires (the tile's links, the bank-to-NIC rows).
- **IR drop, temperature gradients, the PHYs and the IP** (the floorplan sketch's placeholders).
- **Utilization beyond the recorded windows.** The figures are per recording: the core's at 56.7 %, the tile's units at
  the tile's 32.8 % window; a different workload is a different activity.
