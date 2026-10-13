# Synthesis report, drop 0.42 (stream A2a): two libraries, the period sweep, the packer / NIC / router paths

Stream A2a's results on the open-PDK proxy (Yosys 0.69 with slang, OpenSTA 3.1.0; ASAP7 7.5T RVT SS 0.63 V / 100 °C and
Nangate45 typical; the flow of `07_ci/synth/README.md`). Sources:

- **CI run 172** (PR #8 at fa7bdf8): both libraries, every unit of the silicon tile, the sweep — the unit and sweep
  tables below;
- **CI run 170** (PR #8 at a9f67f5, before the unpacker's last change): the sweep row that blocked the verdict, and the
  simulation end times of section 9;
- **CI run 162** (main 5eb76ec): the ASAP7 baseline (drop 0.41 merged).

Every figure is a proxy for the N5A target: the paths and the shares are the design's, the picoseconds and square
microns the open libraries'. A unit is one module at its silicon parameters with its children as timing models.

## 1. The flow

**Nangate45, the second library, end to end.** `make synth SYNTH_PDK=nangate45 SYNTH_CORNER=TT` maps the same units onto
`NangateOpenCellLibrary_typical` (one corner), writes the SDC in the library's unit (ns), runs OpenSTA per unit and the
sweep, and its report goes beside ASAP7's (`ci_logs/synth/nangate45/`); `make synth_compare` puts the two side by side
(`synth_report_libraries.md`). The CI job runs both, 7.6 min + 6.3 min on the 4-core runner (run 172). The report's tool
line names both libraries.

**Nangate45's macro model, from ORFS's FakeRAM45 macros** (`tools.sh` now installs both):

| Macro | Words × bits | Area µm² | µm² / bit | Clock to output |
| --- | --- | --- | --- | --- |
| `fakeram45_2048x39` | 2,048 × 39 = 79,872 | 45,479 | 0.569 | 0.435 ns |
| `fakeram45_512x64` | 512 × 64 = 32,768 | 17,302 | 0.528 | 0.305 ns |
| the model | | Σ area / Σ bits | **0.557** | **0.435 ns** (the slower), 0.05 ns setup / hold |

The FakeRAM macros are behavioural area / timing stand-ins (CACTI-style), not a compiler's; the model prices every leaf
per bit at their pooled density and times it at the slower access. ASAP7's model is unchanged (FakeRAM2.0's 7 nm bit
cell, 0.042 µm² / bit, 218 ps).

**Macros priced by the read ports they use.** Drops 0.40-0.41 priced every `neo_mem_rf` with RB = 1 at 2 × the bit cell
(two read ports); since 0.41 three of them use port B only (port A tied off), and since 0.42 the NIC's drain FIFO too:

| Leaf (ASAP7) | Ports used | Factor | Macro area µm² (run 162 → 172) |
| --- | --- | --- | --- |
| `act_feeder.u_mem` 1,024 × 512 | registered port B | 2 → 1 | 44,091 → 22,045 |
| `acc_bank.u_mem` 512 × 1,056 | registered port B | 2 → 1 | 45,468 → 22,734 |
| `wbuf_mem.u_mem` 512 × 702 | registered port B | 2 → 1 | 30,226 → 15,113 |
| `tile_nic.u_dfifo` 512 × 1,065 → 1,068 | port A → registered port B (drop 0.42) | 1.5 → 1 | 34,392 → 22,970 |
| `tile_nic.u_prow` 64 × 1,024 | asynchronous port A | 1.5 | 4,129 |

The tile's macro area: 1,018,076 → 946,784 µm² (−7.0 %), the tile 1.243 → 1.170 mm² on ASAP7.

**The period sweep and its verdict** (`--sweep default`, `make synth`'s default): after the timing runs, OpenSTA again on
every unit at 500, 750, 1,000, 1,250 and 1,500 ps (one session per unit; the clock, the 0.2-period I/O delays and the
0.08-period uncertainty of each period in turn; the children's models do not depend on the period). The verdict, over
the silicon tile's units: the smallest swept period at which every unit outside the PE-limited ones (`mac_pe`,
`mac_pe_chk`, `systolic_array`, `neo_mac_core` and their parents `neo_mac_core_v02`, `neo_core`, `neo_tile`) has no
violating endpoint, the PE's own stage, and the tile's period (the larger); the report's first lines give it with the
largest minimum period outside the PE-limited units and the PE-limited units' own minimum periods. Minimum periods come
from OpenSTA's fmax (`period_min` has two decimals in the library's unit: 10 ps steps on ns). Checked on a copy of drop
0.40's synthesis (`--sta-only --sweep default`, every unit in 153 s).

## 2. The units on both libraries

Minimum period (register to register, a child's arcs included), cells and total area (cells + macro model, with the
subtree). ASAP7: run 162 → run 172 where they differ (bold); Nangate45: run 172.

| Unit | ASAP7 min period ps | ASAP7 cells | ASAP7 total area µm² | Nangate45 min period ps | Nangate45 cells | Nangate45 total area µm² |
| --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` | 1,031 | 1,124 | 138 | 1,128 | 1,001 | 1,838 |
| `mac_pe_chk` | 1,093 | 1,576 | 187 | 1,385 | 1,335 | 2,602 |
| `systolic_array` | 1,411 | 1,201,443 | 147,705 | 1,367 | 1,067,779 | 1,964,922 |
| `neo_mac_core` | 952 | 1,255,538 | 158,753 | 1,048 | 1,094,919 | 2,105,381 |
| `act_feeder` | 898 | 8,175 | 45,017 → **22,971** | 897 | 7,027 | 304,520 |
| `acc_bank` | 1,131 | 18,131 | 47,919 → **25,185** | 1,284 | 16,256 | 334,505 |
| `neo_mac_core_v02` | 1,172 | 1,283,764 | 252,095 → **207,315** | 1,464 | 1,119,162 | 2,749,514 |
| `wbuf_mem` | 791 | 4,765 | 30,814 → **15,701** | 1,117 | 3,838 | 206,316 |
| `requant` | 934 | 127,456 | 14,327 | 1,009 | 91,145 | 207,042 |
| `core_seq` | 1,110 | 2,978 | 354 | 1,175 | 2,930 | 4,995 |
| `neo_core` | 1,331 | 1,549,875 | 312,338 → **252,446** | 1,420 | 1,311,646 | 3,380,640 |
| `sram_bank` | 767 | 3,742 | 860,298 | 1,022 | 3,818 | 11,401,775 |
| `mbist` | 1,030 | 986 | 111 | 992 | 936 | 1,334 |
| `bank_bist_wrap` | 1,345 | 5,973 | 860,523 | 1,589 | 5,377 | 11,404,518 |
| `crc16_beat` | — (combinational) | 3,430 | 502 | — | 3,579 | 5,407 |
| `ecc39_enc` | — | 66 | 11 | — | 66 | 121 |
| `ecc39_dec` | — | 163 | 24 | — | 163 | 352 |
| `link_packer` | 571 → **795** | 21,573 → **24,029** | 2,920 → **3,286** | 740 | 18,500 | 36,769 |
| `link_unpacker` | 756 → **482** | 2,906 → **2,876** | 330 → **336** | 494 | 2,879 | 3,363 |
| `tile_nic` | 2,324 → **1,172** | 104,987 → **77,587** | 50,605 → **37,629** | 1,149 | 67,947 | 478,859 |
| `noc_router` | 735 | 89,608 | 13,290 | 746 | 72,417 | 156,789 |
| `tile_dma` | 558 | 563 | 80 | 513 | 477 | 1,068 |
| `prog_mem` | — | 13,939 | 1,933 | — | 9,078 | 22,055 |
| `host_if` | — | 3,051 | 426 | — | 1,812 | 4,989 |
| `esm` | 540 | 305 | 43 | 538 | 334 | 582 |
| `neo_tile` | 3,145 → **1,319** | 1,793,461 → **1,768,487** | 1,242,585 → **1,170,089** | 1,389 | 1,491,079 | 15,490,894 |

The 16×8 core's units (`c16_*`) are in the CI reports.

The tile's worst path moved from the unpacker into the NIC's receive logic (run 162: `u_unpack` → `u_nic`, 2,645 ps over
at 500 ps — the beat through the NIC's combinational `wr_ptr[src]` mux into the bank's lane mask) to the NIC's bank write
port into the bank (run 172: `u_nic` → `u_bank`, 819 ps over: the lane masks' compares after `st_md1` / `st_md2`, the bank's
ECC encoders and the macro's setup): **the tile 3,145 → 1,319 ps** register to register on ASAP7.

## 3. The period sweep

Violating endpoints (WNS ps) per unit and period; † PE-limited (outside the verdict). The I/O at 0.2 of the period.

**ASAP7 SS** (run 172; ‡ run 170's `link_unpacker` row, before fa7bdf8, in the note):

| Unit | Min period ps | 500 ps | 750 ps | 1000 ps | 1250 ps | 1500 ps |
| --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` † | 1,031 | **41** of 157 (-531) | **12** of 157 (-301) | **8** of 157 (-97) | 0 of 157 (83) | 0 of 157 (263) |
| `mac_pe_chk` † | 1,093 | **47** of 181 (-914) | **20** of 181 (-734) | **16** of 181 (-554) | **11** of 181 (-374) | **7** of 181 (-194) |
| `systolic_array` † | 1,411 | **9,503** of 54,049 (-911) | **8,448** of 54,049 (-681) | **512** of 54,049 (-451) | **64** of 54,049 (-221) | 0 of 54,049 (9) |
| `neo_mac_core` † | 952 | **380** of 26,802 (-457) | **318** of 26,802 (-277) | **8** of 26,802 (-97) | 0 of 26,802 (83) | 0 of 26,802 (263) |
| `act_feeder` | 898 | **270** of 1,241 (-421) | **101** of 1,241 (-241) | **2** of 1,241 (-61) | 0 of 1,241 (119) | 0 of 1,241 (299) |
| `acc_bank` | 1,131 | **2,237** of 4,422 (-631) | **1,897** of 4,422 (-401) | **780** of 4,422 (-171) | 0 of 4,422 (59) | 0 of 4,422 (289) |
| `neo_mac_core_v02` † | 1,172 | **64** of 5,421 (-672) | **30** of 5,421 (-442) | **11** of 5,421 (-212) | 0 of 5,421 (18) | 0 of 5,421 (248) |
| `wbuf_mem` | 791 | **322** of 959 (-409) | **275** of 959 (-229) | **197** of 959 (-49) | 0 of 959 (131) | 0 of 959 (311) |
| `requant` | 934 | **4,402** of 7,014 (-534) | **3,076** of 7,014 (-354) | **394** of 7,014 (-174) | 0 of 7,014 (6) | 0 of 7,014 (186) |
| `core_seq` | 1,110 | **178** of 276 (-662) | **35** of 276 (-482) | **20** of 276 (-302) | **1** of 276 (-122) | 0 of 276 (58) |
| `neo_core` † | 1,331 | **2,991** of 6,386 (-831) | **2,393** of 6,386 (-601) | **201** of 6,386 (-371) | **3** of 6,386 (-141) | 0 of 6,386 (89) |
| `sram_bank` | 767 | **546** of 1,205 (-341) | **527** of 1,205 (-161) | 0 of 1,205 (19) | 0 of 1,205 (199) | 0 of 1,205 (379) |
| `mbist` | 1,030 | **586** of 611 (-530) | **585** of 611 (-300) | **22** of 611 (-70) | 0 of 611 (125) | 0 of 611 (305) |
| `bank_bist_wrap` | 1,345 | **1,584** of 1,629 (-845) | **1,582** of 1,629 (-615) | **1,018** of 1,629 (-385) | **403** of 1,629 (-155) | 0 of 1,629 (75) |
| `crc16_beat` | — | **16** of 16 (-233) | 0 of 16 (17) | 0 of 16 (267) | 0 of 16 (517) | 0 of 16 (767) |
| `ecc39_enc` | — | 0 of 7 (202) | 0 of 7 (452) | 0 of 7 (702) | 0 of 7 (952) | 0 of 7 (1,202) |
| `ecc39_dec` | — | 0 of 34 (105) | 0 of 34 (355) | 0 of 34 (605) | 0 of 34 (855) | 0 of 34 (1,105) |
| `link_packer` | 795 | **2,710** of 4,890 (-295) | **58** of 4,890 (-65) | 0 of 4,890 (165) | 0 of 4,890 (393) | 0 of 4,890 (523) |
| `link_unpacker` ‡ | 482 | **1** of 575 (-237) | **1** of 575 (-107) | 0 of 575 (23) | 0 of 575 (153) | 0 of 575 (283) |
| `tile_nic` | 1,172 | **9,482** of 13,389 (-672) | **6,381** of 13,389 (-442) | **822** of 13,389 (-212) | 0 of 13,389 (3) | 0 of 13,389 (133) |
| `noc_router` | 735 | **5,440** of 21,667 (-235) | **11** of 21,667 (-5) | 0 of 21,667 (175) | 0 of 21,667 (355) | 0 of 21,667 (535) |
| `tile_dma` | 559 | **12** of 189 (-59) | 0 of 189 (171) | 0 of 189 (373) | 0 of 189 (553) | 0 of 189 (733) |
| `prog_mem` | — | **432** of 2,564 (-512) | **126** of 2,564 (-382) | **66** of 2,564 (-252) | **64** of 2,564 (-122) | 0 of 2,564 (8) |
| `host_if` | — | **405** of 1,073 (-27) | 0 of 1,073 (153) | 0 of 1,073 (333) | 0 of 1,073 (513) | 0 of 1,073 (693) |
| `esm` | 540 | **15** of 64 (-40) | 0 of 64 (190) | 0 of 64 (371) | 0 of 64 (513) | 0 of 64 (643) |
| `neo_tile` † | 1,319 | **9,667** of 16,122 (-819) | **7,859** of 16,122 (-589) | **4,076** of 16,122 (-359) | **302** of 16,122 (-129) | 0 of 16,122 (101) |

‡ run 170: 434 / 422 / 3 / 1 / 1 violating (-563 / -433 / -303 / -173 / -43): the beat's parity bit from the count field.

**Nangate45 TT** (run 172; ‡ as above):

| Unit | Min period ps | 500 ps | 750 ps | 1000 ps | 1250 ps | 1500 ps |
| --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` † | 1,128 | **42** of 157 (-628) | **12** of 157 (-398) | **10** of 157 (-191) | **3** of 157 (-11) | 0 of 157 (169) |
| `mac_pe_chk` † | 1,385 | **47** of 181 (-885) | **21** of 181 (-683) | **17** of 181 (-503) | **13** of 181 (-323) | **9** of 181 (-143) |
| `systolic_array` † | 1,367 | **8,448** of 54,049 (-867) | **8,448** of 54,049 (-637) | **7,456** of 54,049 (-407) | **288** of 54,049 (-177) | 0 of 54,049 (53) |
| `neo_mac_core` † | 1,048 | **381** of 26,802 (-551) | **377** of 26,802 (-371) | **225** of 26,802 (-191) | **1** of 26,802 (-11) | 0 of 26,802 (169) |
| `act_feeder` | 897 | **535** of 1,241 (-513) | **101** of 1,241 (-333) | **20** of 1,241 (-153) | 0 of 1,241 (27) | 0 of 1,241 (207) |
| `acc_bank` | 1,284 | **3,926** of 4,422 (-784) | **2,171** of 4,422 (-554) | **1,475** of 4,422 (-324) | **67** of 4,422 (-94) | 0 of 4,422 (136) |
| `neo_mac_core_v02` † | 1,464 | **1,372** of 5,421 (-964) | **34** of 5,421 (-734) | **21** of 5,421 (-504) | **8** of 5,421 (-274) | **6** of 5,421 (-44) |
| `wbuf_mem` | 1,117 | **307** of 959 (-661) | **272** of 959 (-481) | **272** of 959 (-301) | **269** of 959 (-121) | 0 of 959 (59) |
| `requant` | 1,009 | **4,428** of 7,014 (-585) | **3,088** of 7,014 (-405) | **2,288** of 7,014 (-225) | **118** of 7,014 (-45) | 0 of 7,014 (135) |
| `core_seq` | 1,175 | **181** of 276 (-675) | **152** of 276 (-445) | **21** of 276 (-215) | **1** of 276 (-12) | 0 of 276 (168) |
| `neo_core` † | 1,420 | **3,993** of 6,386 (-920) | **2,376** of 6,386 (-690) | **2,331** of 6,386 (-460) | **2,050** of 6,386 (-230) | **16** of 6,386 (-0) |
| `sram_bank` | 1,022 | **547** of 1,205 (-585) | **530** of 1,205 (-405) | **530** of 1,205 (-225) | **207** of 1,205 (-45) | 0 of 1,205 (135) |
| `mbist` | 992 | **586** of 611 (-492) | **586** of 611 (-262) | **275** of 611 (-37) | 0 of 611 (143) | 0 of 611 (323) |
| `bank_bist_wrap` | 1,589 | **1,600** of 1,629 (-1,089) | **1,582** of 1,629 (-859) | **1,551** of 1,629 (-629) | **1,086** of 1,629 (-399) | **343** of 1,629 (-169) |
| `crc16_beat` | — | **16** of 16 (-299) | **16** of 16 (-49) | 0 of 16 (201) | 0 of 16 (451) | 0 of 16 (701) |
| `ecc39_enc` | — | 0 of 7 (175) | 0 of 7 (425) | 0 of 7 (675) | 0 of 7 (925) | 0 of 7 (1,175) |
| `ecc39_dec` | — | 0 of 34 (55) | 0 of 34 (305) | 0 of 34 (555) | 0 of 34 (805) | 0 of 34 (1,055) |
| `link_packer` | 740 | **3,753** of 4,890 (-240) | **38** of 4,890 (-10) | 0 of 4,890 (220) | 0 of 4,890 (442) | 0 of 4,890 (598) |
| `link_unpacker` ‡ | 494 | **10** of 575 (-268) | **1** of 575 (-138) | **1** of 575 (-8) | 0 of 575 (122) | 0 of 575 (252) |
| `tile_nic` | 1,149 | **10,328** of 13,389 (-649) | **6,254** of 13,389 (-419) | **291** of 13,389 (-223) | **6** of 13,389 (-43) | 0 of 13,389 (137) |
| `noc_router` | 746 | **8,920** of 21,667 (-246) | **612** of 21,667 (-21) | 0 of 21,667 (159) | 0 of 21,667 (339) | 0 of 21,667 (519) |
| `tile_dma` | 513 | **7** of 189 (-13) | 0 of 189 (217) | 0 of 189 (405) | 0 of 189 (585) | 0 of 189 (745) |
| `prog_mem` | — | **951** of 2,564 (-570) | **96** of 2,564 (-440) | **66** of 2,564 (-310) | **64** of 2,564 (-180) | **64** of 2,564 (-50) |
| `host_if` | — | **537** of 1,073 (-110) | 0 of 1,073 (70) | 0 of 1,073 (250) | 0 of 1,073 (430) | 0 of 1,073 (610) |
| `esm` | 538 | **6** of 64 (-38) | 0 of 64 (176) | 0 of 64 (356) | 0 of 64 (505) | 0 of 64 (635) |
| `neo_tile` † | 1,389 | **9,666** of 16,122 (-888) | **7,863** of 16,122 (-658) | **4,698** of 16,122 (-428) | **1,208** of 16,122 (-198) | 0 of 16,122 (32) |

‡ run 170: 498 / 463 / 6 / 1 / 1 violating (-594 / -464 / -334 / -204 / -74).

## 4. The verdicts

| | ASAP7 SS | Nangate45 TT |
| --- | --- | --- |
| every unit outside the PE-limited ones clean from | **1,500 ps** (run 172; run 170: none — `link_unpacker` at 1,500) | **none of the swept periods**: `bank_bist_wrap` (343 endpoints, -169 ps) and `prog_mem` (64, -50) at 1,500 ps |
| what still fails at the next step down | 1,250 ps: `bank_bist_wrap` 403 (-155), `prog_mem` 64 (-122), `core_seq` 1 (-122) | — |
| the PE's own stage (`mac_pe`) | 1,031 ps | 1,128 ps |
| the tile's period on the proxy | **1,500 ps = 667 MHz** (the grid's step above 1,345 ps, `bank_bist_wrap`'s register path) | above 1,500 ps (`bank_bist_wrap` 1,589 ps) |
| the tile's own minimum period (PE-limited, outside the verdict) | 1,319 ps, clean at 1,500 | 1,389 ps, clean at 1,500 (run 170: 1,523 ps through the unpacker) |

So on ASAP7 the NIC, the packer, the unpacker and the router are no longer what sets the period: at 1,250 ps every one of
them is clean (the NIC with 3 ps), and what is left are three units outside A2a's brief — the bank's BIST wrapper (the
bank's ETM into the MBIST's compare: 1,345 ps register to register), the program memory's read (input to output, 64
endpoints) and the sequencer (one endpoint, `f_ct` → `f_start`). Below the verdict, the PE-limited units: the PE stage
1,031 ps, the check PE 1,093 ps (its sweep row still violates at 1,500 ps through its I/O paths), the array 1,411 ps.

On Nangate45 the same units are slower relative to the PE: the typical corner's cells are not uniformly faster than ASAP7
SS's, and both libraries are synthesized at the same 500 ps target, which is far beyond Nangate45's reach. That target
matters more than the library: `mac_pe` on Nangate45 is 1,128 ps synthesized at 500 ps, 1,148 ps at 750 ps and **762 ps at
1,000 ps**; `mac_pe_chk` 1,385 / 801 / 821 ps; on ASAP7 `mac_pe` 1,031 / 1,051 / 1,045 ps and `mac_pe_chk` 1,093 / 1,107 /
**778 ps** (sandbox, the same flow; ABC's delay-driven mapping at an unreachable target is not monotonic). The verdicts
here are at one target for both libraries, as the flow defines it; a per-library target, or a sweep of the target
itself (one synthesis per point, not one STA), is the step that would make Nangate45's verdict a fair second opinion.

## 5. The NIC

**Silicon NIC, CI (run 162 → 172):** ASAP7 104,987 → 77,587 cells, 50,605 → 37,629 µm² (the drain FIFO priced at its
port), **2,324 → 1,172 ps** register to register; at 1,250 ps clean (WNS +3 ps), at 1,000 ps 822 endpoints (-212 ps).
Nangate45: 1,149 ps, clean at 1,500 (6 endpoints, -43 ps, at 1,250).

**The worst path now:** `q_flit` → `st_crc` (arrival 1,098 ps on ASAP7): the receive CRC — `crc16_beat` (the matrix form of
drop 0.41: the masked partial parities, the T vector, the count's inverse matrix: ~19 gate levels) on the beat registered
at the port, then the per-type update and the source-state load. The other paths at 1,000 ps on the 16-source probe: the
transmit CRC (the same tree on `txc_*`, ~1.05 ns), the transmit flit's parity from the bank's read data (input to
output), and ABC's buffer ladders on the 2,048-pin selects (`b_rvalid` into the served row, the lane rotation's select):
a high-fanout net becomes a chain of BUFx2 ten loads at a time (12 levels, 0.5 ns, on `b_rvalid` in one run), because
the adders mapped onto the library's half adders before ABC are boxes ABC times as nothing — the physical flow's
rebuffering (A2b) is where those go. The next step for the CRC is a split form from `gen_crc16_beat.py` — the T vector
formed with the beat at the port, the count's matrix in the cycle — with its own proof.

**What changed** (commit 16c4c0d, `tile_nic.sv`'s header lists it):

| Path (drop 0.41) | Change |
| --- | --- |
| `rx_flit` → `wr_ptr[src]` (64:1) → the WRDATA lane mask, 2.32 ns; the CRC behind `crc_acc[src]` | the beat registered at the port (`q_*`) with its source's receive state (`st_*`), updated in place; another source's beat loads its own while the outgoing state goes back through one write-back register (`wb3_*`) |
| the fetch decision: `rx_woff` → staged + words → compares → minus the words taken → compares → `rx_ready`, 2.28 ns | no arithmetic in the loop: staged + words left a thermometer code (`fx_tt`), the staged count too (`sg_t`), the beat position one-hot (`fx_bo`); compares are bits, "minus c wpe" a shift, the words' selects rotations by a register; t of a beat's first cycle formed as the stage takes it (`q_tt`) |
| every load enable behind `rx_fire` (q_ready of any type) | a beat consumed per type (`fire_*`) |
| the WRDATA lane offset's derived values (adders and compares on `st_off`) | the offset one-hot (`st_rot`, turned by the beat's count `q_nwo`), every derived value an OR of its bits; the crossing test an AND-OR with the count's thermometer |
| the PSUM column mask and word select (`col0 + words`, `j - col0`) | the column mask the count's thermometer shifted by `col0`, formed with the beat; one lane rotation for WRDATA and PSUM |
| the drain FIFO's asynchronous head read; the drain beat's count / mask by adders; up / down FIFO counts | the head on the leaf's registered port B (the macro's read); count, mask and last flag constants of the beat index; the counts as pushed − popped |
| the transmit beat and its CRC in one cycle | the CRC a cycle behind the beat (`txc_*`) |
| the serve's position as `serve_addr + serve_i` | row, words left, lanes left, the lane's one-hot and mask, the last-beat flag in registers |
| the flit's parity: one XOR tree over 560 bits behind the words' select | from word parities: the bank row's under the lane mask (served beat), the beat parity stored with the drain FIFO row at the push (512 × 1,068 now) |
| the partition compare from the `partition` input in the cycle | registered with the beat (the configuration a beat is checked against is the one at its arrival) |

**The 16-source probe** (`tile_nic` at NSRC = 16, the silicon widths otherwise; the silicon NIC's Yosys run is CI's):
main 86,923 cells, 9,626 µm², 2,352 ps → 63,784 cells, 8,600 µm², **1,133 ps**; clean from 1,250 ps (652 endpoints at 1,000,
-178 ps). The steps: the port register and the source state alone made it worse, 2,770 ps (the fetch decision's and the
serve's adders mapped onto the library's half / full adders), the values those adders formed precomputed in registers
1,713 ps (the decision still used compares), the decision's next values side by side 1,488, then the thermometer /
one-hot loop and the one-hot lane offset 1,212, the per-type fires, the FIFO counts and the CRC split 1,113 — the CRC's
words part formed at the port put the count field's input-to-register path at 1.3 ns and went back — and the parity
from word parities.

**Latency:** every received flit one cycle later (the port register), a drained row one cycle later on an empty FIFO,
the CRC flit one cycle later per message; the receive and transmit rates are unchanged. A fetch round trip: +3 cycles at
one word per link flit, +4 at 32 (traced, section 9).

## 6. The packer and the unpacker

**`link_packer`** (a798001): the merge decision (same / fits) compares register against register — an input register
(`iv` / `ib`, 565 flops) holds the beat it decides on; `core_ready = !iv || i_ready`, so the throughput is A1's and the
latency +1 cycle (two against the drop 0.40 packer, which `run_nic` I14 checks bit for bit behind a model of the
register). The unit: ASAP7 571 → 795 ps register to register (`ib` → `pend_thr`), 2,151 / 59 / 0 → 2,710 / 58 / 0
violating at 500 / 750 / 1,000 ps; Nangate45 685 ps (main, the same flow in the sandbox) → 740 ps, 3,198 / 60 / 0 →
3,753 / 38 / 0. Clean from 1,000 ps before and after at the unit: the unit's 0.2-period input budget hides what the tile
saw — `core_flit` is the NIC's `tx_flit`, with the NIC's transmit logic in the same cycle — and that is gone. The input
register is in the merging packer only (WPF ≥ BW, the silicon links); the one-word-per-flit splitter of the 16 × 8 test
builds is unchanged. Formal: `link_pack.sby` in two tasks, both full proofs (run 172: stream 175.0 s, parity 10.9 s;
seven mutations each UNKNOWN in 7-18 s).

**`link_unpacker`** (a798001, fa7bdf8): the word select on its own copy of the beat index (`idx_w`), the beat header and
last flag of every beat index formed from the flit and selected by `idx`, and (fa7bdf8) the beat's count and parity
without arithmetic — with BW a power of two, beat b is the last iff the count field's bits above the lane bits are at most
b, and its count is then the low lane bits. ASAP7 756 → 482 ps register to register, clean from 1,000 ps (run 170, before
fa7bdf8: one endpoint, the beat's parity bit, violating at every period through 1,500 ps); Nangate45 494 ps, clean from
1,250 ps (one endpoint at 1,000, -8 ps).

## 7. The router: no change

`noc_router` (A1's one-hot rotating arbitration and the parity / route stored at FIFO entry) is clean from 1,000 ps on
both libraries (ASAP7: 11 endpoints at 750 ps, -5 ps; Nangate45: 612 at 750, -21 ps), its register-to-register period 735
/ 746 ps, its worst path the read pointer into the head-valid flags (660 ps arrival) — the parity check is no longer on
it. Both verdicts are above 1,000 ps, so the RTL stays as A1 left it.

## 8. The duplicated requant

**What it costs** (the core has two `requant`, `u_rq` and `u_rq_dup`, and a 256-bit comparator; FMEDA row
"Requantization tables and datapath", the R10 measure): one copy is 127,456 cells / 14,327 µm² on ASAP7 (2.4 % of the
tile's area for the pair, 1.2 % for the duplicate; 7.2 % of the tile's cells per copy, 14 % for the pair) and 91,145
cells / 207,042 µm² on Nangate45 (the pair 2.7 % of the tile's area). Its period is not the issue (934 / 1,009 ps, clean
at 1,250 / 1,500 ps).

**A residue-checked single copy** (design note; `rq_residue_probe.sv` in the stream's scratch, synthesized as a unit
through the same flow): keep one requant and check its arithmetic modulo 7 — per column, the residues of `mult` and of
the rounding / zero-point constant `c` stored beside the tables (formed at the table write), the residue of `acc` and the
predicted residue of `s = acc · mult + c` in stage 1 (carried to stage 2 with the carry-save rows), the residue of the
stage-2 sum (the one carry-propagate adder's output) compared with it; stage 3 (shift, fits, saturate, ReLU) has no
residue form and is duplicated with its control (shift, zero point, acc's sign) carried through stages 1-2, the
duplicate's output compared with the requant's. Cost: **39,344 cells / 4,371 µm² on ASAP7, 28,329 / 60,039 µm² on
Nangate45** — 31 % / 29 % of one requant: it would save ~88 k cells and ~10,000 µm² per core on ASAP7 (0.85 % of the tile's
area, 5 % of its cells) and ~147,000 µm² on Nangate45. Timing: the 49-bit sum's residue is a 21-digit mod-7 tree (2.6 ns in
the probe's single stage), so the compare takes one more pipeline stage — err a cycle later, no throughput cost.

**Coverage against R10** (DC 0.99 claimed, proven with the duplicate in C8, table parity in C10):
- a single fault in the multiply / carry-save tree / carry-propagate adder changes the sum by ± 2^k (a wrong sum or carry
  bit; a wrong carry ripples into ± 2^k), and 2^k mod 7 ∈ {1, 2, 4} is never 0: every single-bit arithmetic error is
  detected — a modulus 3 would miss nothing single either, but misses ± 3 · 2^k from a sum-and-carry pair;
- multi-bit errors escape when they are multiples of 7: two flips ± 2^a ∓ 2^b with a ≡ b (mod 3), about 1 in 7 random
  multi-bit errors — the transient part of the row's rate with multi-bit upsets in the datapath, which the duplicate
  catches; with those a few per cent of the transient rate, the row stays near 0.99, to be shown by fault injection;
- the input `rd_data` is shared by both copies today and by the residue generator and the multiplier then: neither scheme
  sees it, ABFT does (unchanged);
- stage 3 and the tables: duplicated / parity-checked as today; a fault in the residue tables or checker raises the flag
  (safe) or masks (latent, as the comparator's today).
The C8 hook (bit 12 of column 0 into the primary) is a single-bit error: the residue design flags it. **Recommendation:**
keep the duplicate for 0.42 — R10's 0.99 is proven with it — and carry the residue form as the area item for the
physical-design drops, gated on a fault-injection campaign of the multi-bit transient escapes.

## 9. End times

The tile suites' end times, main (CI run 162) → this branch (CI run 170; fa7bdf8 after it changes how the unpacker forms
a beat's count and parity, not when: the sandbox's `tile` run on the final RTL gives the same times): cocotb `tile` (2 × 2
mesh of 16 × 8 cores, one word per link flit; 17 PASS, M14 skipped), `tile32` (2 × 2 of the
silicon tile: 32 × 32 cores, 32 words per link flit, 2 MB banks; 17 PASS, M14 skipped) and `mesh8` (8 × 8 silicon tiles;
11 PASS). Clocks where the test reports them, else the test's simulation time (0.5 ns per clock); Δ in clocks.

| Test | `tile` | Δ | `tile32` | Δ | `mesh8` | Δ |
| --- | --- | --- | --- | --- | --- | --- |
| M1 fetch / compute / writeback | 3,841 → 3,861 clk | +20 | 4,621 → 4,644 clk | +23 | 4,621 → 4,644 clk | +23 |
| M2 K-split with RDY | 3,346 → 3,358 clk | +12 | 2,368 → 2,374 clk | +6 | 2,368 → 2,374 clk | +6 |
| M3 end-to-end CRC | 963.50 → 965.00 ns | +3 | 4,358.50 → 4,360.50 ns | +4 | 4,418.50 → 4,420.50 ns | +4 |
| M4 region refill | 3,677 → 3,694 clk | +17 | 4,579 → 4,598 clk | +19 | — | |
| M5 bank ECC in flight | 2,987.00 → 2,997.00 ns | +20 | 7,265.00 → 7,276.50 ns | +23 | 7,325.00 → 7,336.50 ns | +23 |
| M6 compiler program | 3,346 → 3,358 clk | +12 | 2,379 → 2,386 clk | +7 | 2,379 → 2,386 clk | +7 |
| M7 M-chunks, partial fetch | 3,598.50 → 3,604.50 ns | +12 | 7,520.00 → 7,524.00 ns | +8 | 7,580.00 → 7,584.00 ns | +8 |
| M8 registers, self-test, watchdog | 3,051.00 → 3,058.00 ns | +14 | 8,324.00 → 8,331.50 ns | +15 | — | |
| M9 lost flit, timeout | 5,074.00 → 5,075.50 ns | +3 | 37,140.50 → 37,142.50 ns | +4 | 37,260.50 → 37,262.50 ns | +4 |
| M13 partitions | 4,139.50 → 4,141.00 ns | +3 | 32,809.00 → 32,811.50 ns | +5 | 32,929.00 → 32,931.50 ns | +5 |
| M14 bank contention (14 maximal fetches at one bank) | skipped | | skipped | | first 294 → 299, last 3,699 → 3,704 clk; 0.065 clk / word both | +5 / +5 |
| M16 link parity (one fetch) | 165 → 168 clk | +3 | 43 → 47 clk | +4 | 43 → 47 clk | +4 |
| M17 bank ECC double | 3,841 → 3,861 clk | +20 | 4,621 → 4,644 clk | +23 | — | |
| M18 descriptor sweep (5 cases) | 1,541 / 395 / 2,415 / 3,257 / 2,987 → 1,567 / 403 / 2,429 / 3,265 / 2,995 | +26 / +8 / +14 / +8 / +8 | 1,099 / 276 / 2,994 / 4,202 / 4,045 → 1,130 / 281 / 3,007 / 4,213 / 4,054 | +31 / +5 / +13 / +11 / +9 | — | |
| M19 INT8 drain | 3,592 → 3,612 clk | +20 | 4,499 → 4,524 clk | +25 | — | |
| suite total | 46,298.52 → 46,410.02 ns | +223 | 162,214.52 → 162,332.02 ns | +235 | 117,778.01 → 117,822.51 ns | +89 |

The other suites of the job: M10 (`tile8`, two layers on 8 × 8 cores) 808 / 1,049 → 816 / 1,057 clocks (+8 / +8);
`tb_tile_mesh` (the SystemVerilog M1) 3,804 → 3,818 clocks (+14); the core suites (`core`, `core32`) unchanged.

**Accounting, traced.** Main and this branch built side by side as the 2 × 2 mesh of 16 × 8 cores, at one and at 32
words per link flit, with a monitor bound into every tile (each NIC ↔ packer / unpacker handshake with its flit type,
the DMA state, the drain / done / prog_done edges, stamped from reset; a scratch harness of the stream, not in the
repository). One activation fetch of 144 words from bank (1,1) into tile (0,0), in cycles after the DMA issues it:

| Milestone | 1 word / flit: main → A2a | 32 words / flit: main → A2a |
| --- | --- | --- |
| the request into the requester's router | 1 → 1 | 2 → **3** |
| the request into the server's NIC | 7 → 7 | 8 → 9 |
| the server's first bank read | 9 → **10** | 10 → **12** |
| the first response beat into the requester's NIC | 16 → 17 | 21 → 23 |
| the last response beat out of the server's NIC | 153 → 154 | 19 → 21 |
| the CRC flit out of the server's NIC | 154 → **156** | 21 → 23 |
| the CRC flit out of the server's packer | 154 → 156 | 22 → **26** |
| the CRC flit into the requester's NIC | 160 → 162 | 38 → 41 |
| the fetch complete (the DMA moves on) | 162 → **165 (+3)** | 40 → **44 (+4)** |

- **One word per link flit (`tile`): +3 per fetch round trip** — the server's port register (+1: the request is decoded,
  the bank read and the whole response stream start a cycle later), the CRC flit two cycles behind the last beat instead
  of one (+1: the CRC accumulated a cycle behind the beat, `txc_*`), the requester's port register on the CRC flit (+1:
  the fetch completes a cycle after the flit arrives, registered). The one-word splitter has no input register.
- **32 words per link flit (`tile32`, `mesh8`): +4** — the requester's packer register on the request (+1), the server's
  port register (+1), the server's packer (+1): the CRC flit waits in its input register while the last pending flit
  leaves, two cycles more than on main between the server's NIC and its link port, of which one reaches the requester
  (the response flits ahead of it set the pace through the routers); the CRC's own cycle behind the beat costs nothing
  here (main's NIC already waited a cycle for the pending flit at that point); the requester's port register (+1).
- **A drain's end** (`drain_busy` falling, which the tests wait for): +2 at one word per flit — the rows a cycle later
  behind the FIFO's registered head, the CRC flit a cycle behind the last — and +1 at 32 words per flit (9-word rows: the
  rows; the CRC's cycle lands in the packer's wait, as on the fetch path). There the last flit and the CRC flit now leave
  the tile's packer two cycles after `drain_busy` falls (main: with it) — `drain_busy` says the NIC has handed the drain
  off, as before, not that it has left the tile.
- **The tests, `tile`:** M3, M9, M13, M16 one completing round trip each, +3. M1 / M5 / M17 / M19 — three activation
  and three weight fetches, sequential (a FETCH waits for completion), then the drain: 6 × 3 + 2 = **+20**, traced (the
  core's GO 3,640 → 3,658, its done +18, the drain's end +20); M4 five fetches before GO (the third activation tile
  refills behind the core): +17; M8 two runs of two fetches: +14; M18's five convolutions, 8 / 2 / 4 / 2 / 2 fetches:
  +26 / +8 / +14 / +8 / +8 — every single-tile convolution is its fetches × 3 + 2. M2, M6 and M7 put two or three tiles'
  fetches on one bank: a fetch's added cycles land on the one served behind it, and the order of service can change
  (traced at 32 words per flit: the M2 owner's two fetches +3 and −5, the test +3).
- **The 16 × 8 build at 32 words per flit** (the whole suite, traced, as the bridge to the silicon builds): M3 / M9 /
  M16 +4, M13 +5, and every single-tile convolution its fetches × 4 + 1 (M1 / M5 / M17 / M19 +25, M4 +21, M8 +17, M18
  +33 / +9 / +17 / +9 / +9).
- **`tile32` / `mesh8`:** the single-fetch tests match the 16 × 8 build at the same link width exactly (M3 / M9 / M16 +4,
  M13 +5), and every single-tile convolution is its fetches × 4 plus a residual that follows the drain where the 16 × 8
  build has +1 throughout: −1 after 36 INT32 rows of 33 words (M1 / M5 / M17 six fetches +23, M4 five +19, M8 four +15,
  M18's stride-1 cases eight and three — the 25-run weight set takes two fetches at the DMA's 12-bit length field —
  +31 / +11), −3 after 9 such rows (M18's stride-2 cases, two / four / three fetches: +5 / +13 / +9), +1 for INT8 rows of
  8 words (M19 +25). The 33-word rows (32 columns and the check column, three beats) exist only in the silicon build,
  which is CI-only and was not traced. M2 / M6 / M7 (one bank shared): +6 / +7 / +8.
- **The rates do not change:** mesh8's M14 (fourteen maximal fetches, 4,088 words each, queued at one bank) moves its
  first and last completion by +5 and keeps 0.065 clocks per word — the cost is per message, not per word.

## 10. Checked

In the sandbox, on the final RTL: lint clean (`make lint`, nothing waived); `run_nic.py` ALL PASS (I1-I15; new: I1c
three interleaved sources, I15 every transmitted flit even parity, with a planted beat-parity bug caught); neosim M3 and
`run_noc.py` PASS; cocotb `tile` 17 PASS + M14 skipped at 46,410.02 ns; `make verilator`'s light part (crc16 on Verilator
and Icarus, `tb_neo_mac_core`, `tb_neo_core`, `tb_tile_mesh`, cocotb `tile`) PASS — its 32 × 32 and coverage builds left
to CI; link_pack's two formal tasks full proofs (stream 241.6 s, parity 16.3 s); unit synthesis of `link_packer`,
`link_unpacker` and `crc16_beat` on both libraries with the sweep; the NIC probe; the traced latency builds of section 9.
In CI: run 170 (a9f67f5) green in every job — coverage: functional 53 / 53 bins as on main, line 98.6 % (main 98.3 %),
the NIC's two uncovered line points the same two as main's (`if (drain_open)`, the transmit state's default); run 172
(fa7bdf8): formal 10 / 10 PASS, python-regress and synth green.
