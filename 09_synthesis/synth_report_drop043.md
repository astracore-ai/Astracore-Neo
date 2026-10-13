# Synthesis report, drop 0.43 (stream A3): the tile clean at 1,250 ps, the check PE at the data PE's stage

Stream A3's timing items on the open-PDK proxy (Yosys 0.69 with slang, OpenSTA 3.1.0; ASAP7 7.5T RVT SS 0.63 V / 100 °C
and Nangate45 typical; the flow of `07_ci/synth/README.md`). The routed blocks, the tile's routed area and the power from
recorded activity are in `pnr_drop043.md` and `power_drop043.md`; the floorplan sketch in `floorplan/README.md`. Sources:

- **CI run 186** (PR #10 at 13c6f21; the RTL and `synth.py` of every later commit of the PR are the same): both libraries, every unit, the sweep -- the "after" columns;
- **CI run 180** (main c7ad80a, drop 0.42 merged): the "before" columns (its synth job's gates failed on the chip top's
  units, an integration slip of the 0.42 merge fixed in this PR, section 5; the tile's units and the sweep are complete);
- **the sandbox** (the same flow and tools; unchanged units reproduce run 180 to the picosecond): the options measured
  beside the chosen ones (the DMA's wait state, the check PE's split points), unit by unit.

Every figure is a proxy for the N5A target: the paths and the shares are the design's, the picoseconds and square
microns the open libraries'. A unit is one module at its silicon parameters with its children as timing models.

## 1. The verdict

| | ASAP7 SS: run 180 → this PR | Nangate45 TT: run 180 → this PR |
| --- | --- | --- |
| every unit outside the PE-limited ones clean from | 1,500 ps → **1,250 ps** | none of the swept periods → none: `bank_bist_wrap` at 1,500 ps (343 → 58 endpoints, −169 → −89 ps; `prog_mem`'s 64 there before are gone) |
| what fails at the next step down | at 1,250 ps: `bank_bist_wrap` 403 (−155), `prog_mem` 64 (−122), `core_seq` 1 (−122) → none; at 1,000 ps now `bank_bist_wrap` 866 (−222), `acc_bank` 780 (−171), `tile_nic` 668 (−219), `requant` 394 (−174), `wbuf_mem` 340 (−92), `prog_mem` 66 (−96), `mbist` 2, `act_feeder` 2, `core_seq` 1 (−18) | at 1,250 ps: `bank_bist_wrap` 936 (−319), `wbuf_mem` 373, `sram_bank` 207, `requant` 118, `acc_bank` 67, `tile_nic` 20 (`prog_mem` 64 and `core_seq` 1 before are clean) |
| the PE's own stage (`mac_pe`) | 1,095 ps | 1,151 ps |
| **the tile's period on the proxy** | 1,500 ps = 667 MHz → **1,250 ps = 800 MHz** | above 1,500 ps (`bank_bist_wrap` 1,589 → 1,509 ps) |
| the PE-limited units' own minimum periods | check PE 1,093 → **1,057**, array 1,421 → **1,061**, `neo_core` 1,331 → **1,180** (the sequencer's compare, section 2), `neo_mac_core` 1,050, `neo_mac_core_v02` 1,262, `neo_tile` 1,299 (`u_nic → u_bank`, unchanged) | check PE 1,451 → **1,154**, array 1,424 → **1,124**, `neo_core` 1,420, `neo_tile` 1,375 |
| the gates | failed (the chip top's units, section 5) → **all passed** | failed → **all passed** |

So on ASAP7 SS the tile's period is now set by the PE's own stage and the 1,250 ps grid step above it: every unit
outside the PE-limited ones is clean at 1,250 ps (the closest `bank_bist_wrap` with 8 ps, `requant` 6, `neo_core`'s
10 inside the PE-limited set), and the check PE and the array are under the data PE's 1,095 ps. The worst path left
outside the PE-limited units is `bank_bist_wrap`'s register path at 1,182 ps, now the MBIST engine's write data into the
bank's encoders (`u_bist → u_bank`). Inside the PE-limited set, the rows with endpoints left at 1,250 ps: `mac_pe`
itself (2, −10 ps: its `x` pin through the I/O budget of 0.2 of the period), `systolic_array` 192 and `neo_mac_core` 6
(−10: the same pin, the array's first column), `neo_mac_core_v02` 27 (−72: the drop-0.20 core, not in the silicon
tile) and `neo_tile` 226 (−109: `u_nic → u_bank` through the NIC's and the bank's timing models at the tile's link
budget) -- outside the verdict as before.

On Nangate45 the second library confirms the direction but not the grid step: the sweep's grid is ASAP7's and both
libraries are synthesized at ASAP7's 500 ps target (section 4 of `synth_report_drop042.md`). The MBIST's registered
check moves `bank_bist_wrap` from 1,589 to 1,509 ps; its worst path there is still the bank's read data into the
engine (`u_bank → u_bist`: Nangate45's bank timing model reads in 1,022 ps against ASAP7's 683), which only a
registered read-data stage would take below 1,500 ps (one more cycle per MBIST check, not needed on the proxy that sets
the period). `prog_mem` (64 endpoints at 1,500 ps before) is clean from 1,250 ps (+51), `core_seq` from 1,000.

## 2. The three units A2a left at 1,250 ps

Run 180's sweep at 1,250 ps: every unit outside the PE-limited ones clean except `bank_bist_wrap` (403 endpoints, −155
ps), `prog_mem` (64, −122) and `core_seq` (1, −122).

| Unit | change | min period ps: run 180 → after | violating at 1,250 ps (WNS) | cells | µm² |
| --- | --- | --- | --- | --- | --- |
| `bank_bist_wrap` | the MBIST's check registered (`mbist.sv`) | 1,345 → **1,182** | **403** (−155) → 0 (+8) | 5,973 → 6,012 | 757.3 → 766.9 |
| `mbist` | (the engine alone) | 1,030 → 1,051 | 0 (+125) → 0 (+139) | 986 → 1,025 | 110.5 → 120.1 |
| `prog_mem` | the corrected word registered (`prog_mem.sv`) | — (input to register) | **64** (−122) → 0 (+84) | 13,939 → 13,880 | 1,933.4 → 1,949.1 |
| `tile_dma` | the next pc to the memory (`tile_dma.sv`) | 559 → 546 | 0 (+553) → 0 (+483) | 563 → 569 | 79.5 → 80.4 |
| `core_seq` | `ct − cfg_ct0` as a register (`core_seq.sv`) | 1,110 → **922** | **1** (−122) → 0 (+162) | 2,978 → 2,993 | 353.6 → 355.4 |
| `c16_core_seq` | (the 16 × 8 core's) | 1,203 → 945 | **10** (−214) → 0 (+149) | 2,612 → 2,687 | 315.4 → 324.4 |
| `neo_core` † | (its worst path was the sequencers' compare) | 1,331 → **1,180** | **3** (−141) → 0 (+10) | 1,855,295 → 1,870,429 | 236,131 → 237,654 |

**`bank_bist_wrap` / `mbist`: the check registered.** The MBIST read the bank through its 16 SECDED decoders (the bank's
timing model: 683 ps clock to `rdata`) and in the same cycle compared each lane against the March background, picked the
first failing lane and formed its word address: 1,345 ps register to register through the wrapper (`u_bank → u_bist`).
`S_CHK` now registers only what failed -- the lanes that mismatched or were corrected (`hit_q`, 16 bits), whether any
mismatched (`mism_q`) and the row (`row_q`) -- and the cycle after records `fail` / `fail_ce` / `fail_addr` from them.
`done` waits for that cycle (`done = S_DONE && !chk_q`), so `fail` is final when `done` is seen: the March sequence is
unchanged and the engine is done one cycle later (`run_mbist.py` B1: 120 → 121 cycles at 64 words, four rows; the
silicon bank's 32,768 rows take some 0.98 M cycles, one of them added). The worst path is now the engine's write data into
the bank's encoders. A new test, B4, plants a stuck bit only the last element's last read can see: `fail` and
`fail_addr` must be set in the first cycle of `done` -- the registered check without the `done` hold-off fails it.

**`prog_mem` / `tile_dma`: the decoded instruction registered, the DMA's program counter one cycle ahead.** The 32:1
read mux of the program memory and its two SECDED decoders (770 ps from `raddr`) ended in the DMA's instruction register
through `prog_mem`'s output -- an input-to-output path of the unit, 64 endpoints. `prog_mem` now registers the corrected
word (`rdata` the word at the previous cycle's address), and the DMA presents the address it will have next (`pc_nx`:
0 at a program start, `pc + 1` in `S_FETCH`, else `pc`), so the registered word is the one at its `pc` in every cycle.
The brief's two options, both measured:

| | (a) the next pc to the memory (this PR) | (b) a wait state per instruction |
| --- | --- | --- |
| DMA (`tile_dma`) | 569 cells, 80.4 µm², 546 ps | 561 cells, 79.8 µm², 570 ps |
| program memory (`prog_mem`) | the same: 13,880 cells, 1,949 µm², clean at 1,250 (84 ps) | the same |
| cycles per DMA instruction | unchanged | +1 (`S_FETCH` → `S_FETCH2` → `S_ISSUE`) |
| end times, cocotb `tile` (17 PASS, 2 skipped) | identical to main: 46,410.02 ns (section 6) | 46,461.02 ns, +102 clocks: a clock per instruction on the path -- M1 / M5 / M17 / M19 +8, M8 +9, M4 +7, M2 / M6 +5, M9 / M13 +4, M7 +3, M3 / M16 +2, M12 +1, M18 +28 over its five programs |

(a) costs 8 cells and 0.6 µm² per DMA engine (two per tile, lockstep) and no cycle; (b) costs a cycle per instruction
on every program -- (a) is the cheaper. The lockstep comparator compares the two engines' `pc_out` as before (now the
next pc, the same in both).

**`core_seq`: `ct − cfg_ct0` kept as a register.** `tile_ok` compared `tiles_ready` with `ct − cfg_ct0` -- a subtraction
and a compare in front of `f_start`, an output to the feeder and the lockstep comparator (1,110 ps). `ct_rel` is now a
register beside `ct` (0 at `go`, +1 when the enumeration steps to the next channel tile) and `f_start` the compare
alone. Cycle for cycle the same sequencer: a k-induction proof of the new against the old (every output equal every
cycle, every input free, `cfg_ct0` held from reset) passes in the sandbox; the formal job's `lockstep_core_seq_fv`
lists `ct_rel` in its register-by-register invariant.

## 3. The check PE at the data PE's stage

`mac_pe_chk` (the ABFT check columns' PE: the 8-bit activation times the 14-bit check weight at 32 rows, 13 at 16) was
1,093 ps register to register, and its path from the `x` pin did not fit even at 1,500 ps (7 endpoints, −194 ps with
the I/O at 0.2 of the period) -- the 8 × 14 product's Booth rows one level deeper than the data PE's 8 × 8, the select
lines fanned across 14-bit rows; the systolic array's worst path is that pin seen from the array, the last data PE's
`x_out` into the check PE: 1,421 ps on the 32 × 32 array (run 180), 1,237 ps on the 16 × 8.
With C1 merged the data PE (NB = 4, the index mux in front of its product) is 1,095 ps: the target.

The brief's two options:

- **a second product stage** (the check column one cycle later, `deskew_out` one tap deeper): at least the carry-save
  register (2 × 22 flops per check PE), and the 8 × 14 tree's path from `x` (1,025 ps to the carry-save output) stays
  above the data PE's stage, so the stage would not close the column without also restructuring the tree;
- **two half products** (this PR, `mac_pe.sv` for WW > XW only; the data PE's always block of drop 0.42 is unchanged and
  its netlist identical): `w = w_hi · 2^WL + w_lo` with `w_lo` the low WL = ⌊WW / 2⌋ bits unsigned and `w_hi` the rest
  signed; the product stage registers `x · w_lo` and `x · w_hi` (two 8 × 7 multipliers at WW = 14, the data PE's size)
  each with its sign bit inverted -- a signed N-bit v is (v ⊕ 2^(N−1)) − 2^(N−1) zero-extended -- and the add stage sums
  `p_in`, the two products without sign extension and one constant. With plain sign extension the add stage was 1,145
  ps (the product register's sign bit driving 32 loads); WL = ⌈WW / 2⌉ put c16's 13-bit check PE at 1,109 ps, ⌊WW / 2⌋
  at 1,057.

| Unit | run 180 → after: min period ps | cells | µm² | violating at 1,250 ps |
| --- | --- | --- | --- | --- |
| `mac_pe_chk` (WW = 14, 32 rows) | 1,093 → **1,057** | 1,576 → 1,694 | 187.1 → 199.0 | **11** (−374) → 0 (+12) |
| `c16_mac_pe_chk` (WW = 13) | 1,251 → **1,057** | 1,523 → 1,634 | 180.8 → 192.6 | **9** (−189) → 0 (+14) |
| `mac_pe` (the data PE, NB = 4: unchanged) | 1,095 → 1,095 | 1,253 → 1,253 | 159.3 → 159.3 | **2** (−10) → **2** (−10) |
| `systolic_array` † (32 × 32, NCHK = 4) | 1,421 → **1,061** | 1,484,838 → 1,499,942 | 187,078 → 188,598 | **576** (−231) → **192** (−10) |
| `c16_systolic_array` (16 × 8) | 1,237 → **1,061** | 257,862 → 264,966 | 31,965 → 32,717 | **160** (−47) → **96** (−10) |
| Nangate45: `mac_pe_chk` / `mac_pe` / `systolic_array` | 1,451 → **1,154** / 1,151 / 1,424 → **1,124** | 1,467 → 1,301 / 1,121 | 2,722 → 2,633 / 2,105 | 14 → 10 / 6 / 1,856 → 832 |

The array's minimum period is now the data PEs' own stage seen across the array (1,061 ps: one PE's `x_out` into its
neighbour's product stage), under the data PE's 1,095 -- the brief's expected result (1,031 ps before C1's index path;
1,095 with it). The violations left at 1,250 ps in the array rows are the data PE's `x`-pin paths at the unit's I/O
budget, the same as `mac_pe`'s own row.

The sum is `p_in + x · w + inj` modulo 2^32 bit for bit and the latency unchanged (no deskew or LATENCY change): an
exhaustive simulation of the new check PE against drop 0.42's (Verilator, both driven alike, every output compared every
cycle) -- every one of the 2^22 (x, w) pairs at WW = 14 loaded and swapped in, then 2 M cycles of random loads, swaps,
valid gaps, fault injections and resets -- finds no difference at NB = 1 and 4, nor at WW = 13, 12 and 9. The ABFT
verdicts are the same on every test: neosim `run_mac_core.py` T1–T7 on the four vector sets (every MAC fault, check-weight
fault, check-PE fault and index flip flagged on exactly its rows), `run_conv_core.py` C1–C11, cocotb `core` 5 / 5 at
9,325.50 ns. The halves are signed variables, not `$signed()` of a part-select: neosim extended the part-select before
the cast; every simulator now agrees. Cost per 32 × 32 core: 128 check PEs, +15,104 cells, +1,523 µm² (0.6 % of the
core's cell area).

## 4. The sweep

Violating endpoints (WNS ps) per unit and period, run 180 → this PR; † PE-limited (outside the verdict). The I/O at 0.2
of the period.

**ASAP7 SS:**

| Unit | Min period ps | 500 ps | 750 ps | 1000 ps | 1250 ps | 1500 ps |
| --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` † | 1,095 → 1,095 | **42** (-595) → **42** (-595) | **16** (-370) → **16** (-370) | **10** (-190) → **10** (-190) | **2** (-10) → **2** (-10) | 0 (170) → 0 (170) |
| `mac_pe_chk` † | 1,093 → 1,057 | **47** (-914) → **57** (-557) | **20** (-734) → **40** (-348) | **16** (-554) → **15** (-168) | **11** (-374) → 0 (12) | **7** (-194) → 0 (192) |
| `systolic_array` † | 1,421 → 1,061 | **35024** (-921) → **35024** (-561) | **33792** (-691) → **33792** (-370) | **33792** (-461) → **33408** (-190) | **576** (-231) → **192** (-10) | **128** (-1) → 0 (170) |
| `neo_mac_core` † | 1,050 → 1,050 | **1208** (-550) → **1208** (-550) | **1129** (-370) → **1127** (-370) | **1035** (-190) → **1035** (-190) | **6** (-10) → **6** (-10) | 0 (170) → 0 (170) |
| `act_feeder` | 869 → 869 | **274** (-418) → **274** (-418) | **84** (-238) → **84** (-238) | **2** (-58) → **2** (-58) | 0 (122) → 0 (122) | 0 (302) → 0 (302) |
| `acc_bank` | 1,131 → 1,131 | **2237** (-631) → **2237** (-631) | **1897** (-401) → **1897** (-401) | **780** (-171) → **780** (-171) | 0 (59) → 0 (59) | 0 (289) → 0 (289) |
| `neo_mac_core_v02` † | 1,262 → 1,262 | **119** (-762) → **119** (-762) | **74** (-532) → **74** (-532) | **35** (-302) → **35** (-302) | **27** (-72) → **27** (-72) | 0 (158) → 0 (158) |
| `wbuf_mem` | 813 → 813 | **453** (-452) → **453** (-452) | **382** (-272) → **382** (-272) | **340** (-92) → **340** (-92) | 0 (88) → 0 (88) | 0 (268) → 0 (268) |
| `requant` | 934 → 934 | **4402** (-534) → **4402** (-534) | **3076** (-354) → **3076** (-354) | **394** (-174) → **394** (-174) | 0 (6) → 0 (6) | 0 (186) → 0 (186) |
| `core_seq` | 1,110 → 922 | **178** (-662) → **191** (-422) | **35** (-482) → **11** (-198) | **20** (-302) → **1** (-18) | **1** (-122) → 0 (162) | 0 (58) → 0 (327) |
| `neo_core` † | 1,331 → 1,180 | **3305** (-831) → **3308** (-680) | **2522** (-601) → **2521** (-450) | **201** (-371) → **200** (-220) | **3** (-141) → 0 (10) | 0 (89) → 0 (240) |
| `sram_bank` | 767 → 767 | **546** (-341) → **546** (-341) | **527** (-161) → **527** (-161) | 0 (19) → 0 (19) | 0 (199) → 0 (199) | 0 (379) → 0 (379) |
| `mbist` | 1,030 → 1,051 | **586** (-530) → **608** (-551) | **585** (-300) → **301** (-321) | **22** (-70) → **2** (-91) | 0 (125) → 0 (139) | 0 (305) → 0 (369) |
| `bank_bist_wrap` | 1,345 → 1,182 | **1584** (-845) → **1583** (-682) | **1582** (-615) → **1567** (-452) | **1018** (-385) → **866** (-222) | **403** (-155) → 0 (8) | 0 (75) → 0 (238) |
| `crc16_beat` | — → — | **16** (-233) → **16** (-233) | 0 (17) → 0 (17) | 0 (267) → 0 (267) | 0 (517) → 0 (517) | 0 (767) → 0 (767) |
| `ecc39_enc` | — → — | 0 (202) → 0 (202) | 0 (452) → 0 (452) | 0 (702) → 0 (702) | 0 (952) → 0 (952) | 0 (1,202) → 0 (1,202) |
| `ecc39_dec` | — → — | 0 (105) → 0 (105) | 0 (355) → 0 (355) | 0 (605) → 0 (605) | 0 (855) → 0 (855) | 0 (1,105) → 0 (1,105) |
| `link_packer` | 795 → 795 | **2710** (-295) → **2710** (-295) | **58** (-65) → **58** (-65) | 0 (165) → 0 (165) | 0 (393) → 0 (393) | 0 (523) → 0 (523) |
| `link_unpacker` | 482 → 482 | **1** (-237) → **1** (-237) | **1** (-107) → **1** (-107) | 0 (23) → 0 (23) | 0 (153) → 0 (153) | 0 (283) → 0 (283) |
| `tile_nic` | 1,179 → 1,179 | **9818** (-679) → **9818** (-679) | **6608** (-449) → **6608** (-449) | **668** (-219) → **668** (-219) | 0 (11) → 0 (11) | 0 (177) → 0 (177) |
| `noc_router` | 735 → 735 | **5440** (-235) → **5440** (-235) | **11** (-5) → **11** (-5) | 0 (175) → 0 (175) | 0 (355) → 0 (355) | 0 (535) → 0 (535) |
| `tile_dma` | 559 → 546 | **12** (-59) → **15** (-46) | 0 (171) → 0 (138) | 0 (373) → 0 (318) | 0 (553) → 0 (483) | 0 (733) → 0 (613) |
| `prog_mem` | — → — | **432** (-512) → **340** (-456) | **126** (-382) → **66** (-276) | **66** (-252) → **66** (-96) | **64** (-122) → 0 (84) | 0 (8) → 0 (264) |
| `host_if` | — → — | **405** (-27) → **405** (-27) | 0 (153) → 0 (153) | 0 (333) → 0 (333) | 0 (513) → 0 (513) | 0 (693) → 0 (693) |
| `esm` | 540 → 540 | **15** (-40) → **15** (-40) | 0 (190) → 0 (190) | 0 (371) → 0 (371) | 0 (513) → 0 (513) | 0 (643) → 0 (643) |
| `neo_tile` † | 1,299 → 1,299 | **9875** (-799) → **9748** (-799) | **8079** (-569) → **7956** (-569) | **4292** (-339) → **4173** (-339) | **222** (-109) → **226** (-109) | 0 (121) → 0 (121) |

**Nangate45 TT:**

| Unit | Min period ps | 500 ps | 750 ps | 1000 ps | 1250 ps | 1500 ps |
| --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` † | 1,151 → 1,151 | **41** (-651) → **41** (-651) | **12** (-421) → **12** (-421) | **10** (-234) → **10** (-234) | **6** (-54) → **6** (-54) | 0 (126) → 0 (126) |
| `mac_pe_chk` † | 1,451 → 1,154 | **47** (-951) → **58** (-654) | **19** (-737) → **36** (-444) | **17** (-557) → **19** (-264) | **14** (-377) → **10** (-84) | **10** (-197) → 0 (96) |
| `systolic_array` † | 1,424 → 1,124 | **33792** (-924) → **33792** (-624) | **33792** (-694) → **33792** (-414) | **33792** (-464) → **33792** (-234) | **1856** (-234) → **832** (-54) | **128** (-4) → 0 (126) |
| `neo_mac_core` † | 1,090 → 1,090 | **1210** (-594) → **1210** (-594) | **1191** (-414) → **1191** (-414) | **1035** (-234) → **1035** (-234) | **26** (-54) → **26** (-54) | 0 (126) → 0 (126) |
| `act_feeder` | 871 → 871 | **792** (-513) → **792** (-513) | **124** (-333) → **124** (-333) | **20** (-153) → **20** (-153) | 0 (27) → 0 (27) | 0 (207) → 0 (207) |
| `acc_bank` | 1,284 → 1,284 | **3926** (-784) → **3926** (-784) | **2171** (-554) → **2171** (-554) | **1475** (-324) → **1475** (-324) | **67** (-94) → **67** (-94) | 0 (136) → 0 (136) |
| `neo_mac_core_v02` † | 1,529 → 1,529 | **1656** (-1,029) → **1656** (-1,029) | **76** (-799) → **76** (-799) | **45** (-569) → **45** (-569) | **32** (-339) → **32** (-339) | **32** (-109) → **32** (-109) |
| `wbuf_mem` | 1,084 → 1,084 | **426** (-638) → **426** (-638) | **378** (-458) → **378** (-458) | **378** (-278) → **378** (-278) | **373** (-98) → **373** (-98) | 0 (82) → 0 (82) |
| `requant` | 1,009 → 1,009 | **4428** (-585) → **4428** (-585) | **3088** (-405) → **3088** (-405) | **2288** (-225) → **2288** (-225) | **118** (-45) → **118** (-45) | 0 (135) → 0 (135) |
| `core_seq` | 1,175 → 865 | **181** (-675) → **197** (-365) | **152** (-445) → **27** (-157) | **21** (-215) → 0 (23) | **1** (-12) → 0 (203) | 0 (168) → 0 (383) |
| `neo_core` † | 1,420 → 1,420 | **4307** (-920) → **4307** (-920) | **2480** (-690) → **2482** (-690) | **2436** (-460) → **2433** (-460) | **2050** (-230) → **2049** (-230) | **16** (-0) → **16** (-0) |
| `sram_bank` | 1,022 → 1,022 | **547** (-585) → **547** (-585) | **530** (-405) → **530** (-405) | **530** (-225) → **530** (-225) | **207** (-45) → **207** (-45) | 0 (135) → 0 (135) |
| `mbist` | 992 → 812 | **586** (-492) → **611** (-343) | **586** (-262) → **590** (-163) | **275** (-37) → 0 (17) | 0 (143) → 0 (197) | 0 (323) → 0 (377) |
| `bank_bist_wrap` | 1,589 → 1,509 | **1600** (-1,089) → **1599** (-1,009) | **1582** (-859) → **1582** (-779) | **1551** (-629) → **1531** (-549) | **1086** (-399) → **936** (-319) | **343** (-169) → **58** (-89) |
| `crc16_beat` | — → — | **16** (-299) → **16** (-299) | **16** (-49) → **16** (-49) | 0 (201) → 0 (201) | 0 (451) → 0 (451) | 0 (701) → 0 (701) |
| `ecc39_enc` | — → — | 0 (175) → 0 (175) | 0 (425) → 0 (425) | 0 (675) → 0 (675) | 0 (925) → 0 (925) | 0 (1,175) → 0 (1,175) |
| `ecc39_dec` | — → — | 0 (55) → 0 (55) | 0 (305) → 0 (305) | 0 (555) → 0 (555) | 0 (805) → 0 (805) | 0 (1,055) → 0 (1,055) |
| `link_packer` | 740 → 740 | **3753** (-240) → **3753** (-240) | **38** (-10) → **38** (-10) | 0 (220) → 0 (220) | 0 (442) → 0 (442) | 0 (598) → 0 (598) |
| `link_unpacker` | 494 → 494 | **10** (-268) → **10** (-268) | **1** (-138) → **1** (-138) | **1** (-8) → **1** (-8) | 0 (122) → 0 (122) | 0 (252) → 0 (252) |
| `tile_nic` | 1,163 → 1,163 | **10688** (-663) → **10688** (-663) | **6549** (-433) → **6549** (-433) | **400** (-229) → **400** (-229) | **20** (-49) → **20** (-49) | 0 (131) → 0 (131) |
| `noc_router` | 746 → 746 | **8920** (-246) → **8920** (-246) | **612** (-21) → **612** (-21) | 0 (159) → 0 (159) | 0 (339) → 0 (339) | 0 (519) → 0 (519) |
| `tile_dma` | 513 → 593 | **7** (-13) → **17** (-93) | 0 (217) → 0 (137) | 0 (405) → 0 (354) | 0 (585) → 0 (513) | 0 (745) → 0 (643) |
| `prog_mem` | — → — | **951** (-570) → **949** (-489) | **96** (-440) → **128** (-309) | **66** (-310) → **66** (-129) | **64** (-180) → 0 (51) | **64** (-50) → 0 (231) |
| `host_if` | — → — | **537** (-110) → **537** (-110) | 0 (70) → 0 (70) | 0 (250) → 0 (250) | 0 (430) → 0 (430) | 0 (610) → 0 (610) |
| `esm` | 538 → 538 | **6** (-38) → **6** (-38) | 0 (176) → 0 (176) | 0 (356) → 0 (356) | 0 (505) → 0 (505) | 0 (635) → 0 (635) |
| `neo_tile` † | 1,375 → 1,375 | **9880** (-875) → **9759** (-875) | **8077** (-645) → **7959** (-645) | **4842** (-415) → **4718** (-415) | **1168** (-185) → **1172** (-185) | 0 (45) → 0 (45) |

## 5. The integration fix of the 0.42 merge

Run 180's synth job failed its gates on the chip top's units: C1 gave `neo_tile` and the silicon tile unit NCHK = 4
while B1's `neo_top` instantiated `neo_tile` at the default 1 and `synth.py`'s chip-top units counted one check column
(`top_neo_tile: PE count 1152 != 1056`; `neo_top: hierarchy: neo_tile instantiated with {... 'NCHK': 1 ...}: no unit
synthesizes it`). `neo_top` now has `NCHK` (default 1: the cocotb top suite's dense tiles as before) passed to every
tile, `synth.py`'s chip top sets 4 as the tile, its PE counts are R × (C + NCHK) (1,152 per tile, 73,728 for `neo_top`),
and the lint stub of `neo_tile` that `make lint`'s `neo_top` pass links against has NCHK / IW. Run 186's gates: all passed on both libraries; `neo_top` synthesizes (135.9 M cells with its tiles, 908 ps, clean from 1,000 ps; the chip top's own units are outside the tile's verdict as in drop 0.42).

## 6. End times

Main (CI run 180, c7ad80a) against this PR (CI run 198, 9106739; the RTL of every later commit is the same): **every
end time of every suite is identical**, test by test -- 86 cocotb tests in the hdl-sim job and the 11 of hdl-mesh8:

| Suite | tests | end-time total, run 180 → run 198 |
| --- | --- | --- |
| `tile` (2 × 2 mesh of 16 × 8 cores) | 17 PASS, 2 skipped | 46,410.02 → 46,410.02 ns |
| `tile8` (M10, two layers on 8 × 8 cores) | 1 PASS | 1,459.50 → 1,459.50 ns |
| `tile32` (2 × 2 silicon tiles: 32 × 32 cores, 2 MB banks, 32 words per link flit) | 17 PASS, 2 skipped | 162,332.02 → 162,332.02 ns |
| `core32` (C1–C5 on the 32 × 32 core, NCHK = 4) | 5 PASS | 19,081.51 → 19,081.51 ns |
| `tile_sp` (M20 + M1 + M6 on NCHK = 4 cores) | 3 PASS | 11,670.00 → 11,670.00 ns |
| the coverage builds (core, tile8, tile, tile32 subset) | 5 + 1 + 17 + 8 PASS | 9,325.50 / 1,459.50 / 46,410.02 / 109,360.51 ns, the same |
| `top` (the chip top, asynchronous and synchronous AXI clocks) | 6 + 6 PASS | 66,044.81 / 56,015.01 ns, the same |
| `mesh8` (8 × 8 silicon tiles) | 11 PASS | per test the same (M1 7,334.00 ... M13 32,931.50 ns) |
| `tb_tile_mesh` (the SystemVerilog M1, sandbox) | PASS | 3,818 clocks, as in drop 0.42 |

The accounting is that nothing in the path of a test changed its cycle count:

- **the DMA**: option (a) issues the next pc to the registered program memory, so an instruction is fetched in the cycle
  it was before -- no cycle (option (b)'s +1 per instruction is section 2's table);
- **the sequencer**: `ct_rel` is the same value `ct − cfg_ct0` was, a cycle-for-cycle equivalence proven (section 2);
- **the check PE**: the same latency, the same sums (section 3);
- **the MBIST**: done one cycle later (`run_mbist.py` B1 120 → 121 cycles); the one cocotb test that runs it, M11, polls
  its status through register reads, and the added cycle falls inside a poll -- M11's end time is unchanged (42.00 ns in
  `tile` and `tile32`).

Coverage (the hdl-sim job's coverage builds): line 98.6 → 98.7 %, branch 97.2 → 97.3 %, toggle 96.7 → 96.6 % (the
MBIST's new check registers: the per-lane hit bits of the lanes no test makes fail stay untoggled), functional 59 / 59 bins as
before; the uncovered line points are the same two `default:` arms.

## 7. Checked

In the sandbox, on the final RTL: `make lint` (the `neo_top` pass re-run on the integration fix with the stub's NCHK:
0 warnings; all ten passes clean in CI, run 198's lint step); neosim `run_mbist.py` ALL PASS (B1 121 cycles, B4), `run_mac_core.py` T1–T7 on v816,
vectors, v168s and v32_4, `run_conv_core.py` C1–C11 (the fault cases C4, C5, C7, C8, C9, C10), `run_tile_test.py` M3;
cocotb `core` 5 / 5 at 9,325.50 ns and `tile` 17 PASS + 2 skipped at 46,410.02 ns, both as on main; `make verilator`'s
light part (crc16 on Verilator and Icarus, `tb_neo_mac_core` on the three vector sets, `tb_neo_core`, `tb_tile_mesh` 3,818
clocks); `make iverilog_core` and `make iverilog_sp` ALL PASS with Icarus 14 (CI's Icarus 12 keeps its standing
`always_comb` X failures in `iverilog_core.txt` / `iverilog.txt`, the same lines as run 180, ungated as before); the
check PE against drop 0.42's by exhaustive simulation (section 3); the sequencer's k-induction equivalence and the
formal job's `lockstep_core_seq` with `ct_rel`; the unit synthesis of every changed unit on ASAP7 (the same flow and
tools as CI, every unchanged unit reproducing run 180 to the picosecond); option (b) of the DMA built and run (cocotb
`tile`, `tile_dma` / `prog_mem` synthesis). In CI: run 198 (9106739) green in every job -- formal, python-regress,
synth (both libraries, the gates passed), hdl-sim, hdl-mesh8; the pnr dispatches of `pnr_drop043.md`.
