# Synthesis report — asap7 SS, 500 ps clock (2026-10-20 10:36:50 UTC)

Tools: Yosys 0.69+274 (git sha1 7513f8df8, Release, Clang /usr/bin/clang++ 21.1.8) (slang front end); OpenSTA 3.1.0. Libraries: asap7sc7p5t_AO_RVT_SS_nldm_211120.lib, asap7sc7p5t_INVBUF_RVT_SS_nldm_220122.lib, asap7sc7p5t_OA_RVT_SS_nldm_211120.lib, asap7sc7p5t_SIMPLE_RVT_SS_nldm_211120.lib, asap7sc7p5t_SEQ_RVT_SS_nldm_220123.lib. Run time 30.0 min.

**Period sweep (500, 750, 1000, 1250, 1500 ps):** every unit of the tile outside the PE-limited ones (mac_pe, mac_pe_chk, neo_core, neo_mac_core, neo_mac_core_v02, neo_tile, systolic_array) is clean from **1,500 ps**; the PE's own stage (`mac_pe`'s minimum period) is **1,095 ps**; the tile on this proxy: **1,500 ps = 667 MHz** (the larger of the two).

The largest minimum period outside the PE-limited units: `tile_nic` 1,192 ps (register to register; the sweep also counts the I/O paths at 0.2 of the period). The PE-limited units' own minimum periods: `neo_tile` 1,369, `neo_core` 1,180, `neo_mac_core_v02` 1,262, `neo_mac_core` 1,050, `systolic_array` 1,061, `mac_pe` 1,095, `mac_pe_chk` 1,057 ps.

Macro model for the black-boxed memory leaves (`neo_mem_sp` / `neo_mem_rf`): FakeRAM2.0 7 nm (ORFS asap7/fakeram.cfg): 0.042 um2/bit, 218 ps access, 50 ps setup/hold; area factors {'neo_mem_sp': 1.0, 'neo_mem_rf': 1.5, 'neo_mem_rf_b': 1.0, 'neo_mem_rf_rb': 2.0} (register-file style reads cost bit-cell area). ASAP7 is an academic 7 nm predictive PDK: no foundry data; the figures are a proxy for the N5A target. Every figure here is a proxy for the N5A target: the paths and the shares are the design's, the absolute picoseconds and square microns are the open library's. A unit is one module synthesized at its silicon parameters with its children black-boxed; 'with subtree' sums the children in (instances × unit).

## Gates

| Unit | Synthesized | Latches | Multiple drivers | Hierarchy check | PE count | DV_HOOKS = 0 | ICG cells (own) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` | ✓ | none | none | ✓ | — | — | 0 |
| `mac_pe_chk` | ✓ | none | none | ✓ | — | — | 0 |
| `systolic_array` | ✓ | none | none | ✓ | 1152 = 1152 ✓ | — | 0 |
| `neo_mac_core` | ✓ | none | none | ✓ | — | — | 0 |
| `act_feeder` | ✓ | none | none | ✓ | — | — | 0 |
| `acc_bank` | ✓ | none | none | ✓ | — | — | 0 |
| `neo_mac_core_v02` | ✓ | none | none | ✓ | — | — | 0 |
| `wbuf_mem` | ✓ | none | none | ✓ | — | ✓ (4 in, 0 out) | 0 |
| `requant` | ✓ | none | none | ✓ | — | — | 1 |
| `core_seq` | ✓ | none | none | ✓ | — | — | 0 |
| `vec_unit` | ✓ | none | none | ✓ | — | — | 0 |
| `neo_core` | ✓ | none | none | ✓ | 1152 = 1152 ✓ | ✓ (4 in, 0 out) | 1 |
| `sram_bank` | ✓ | none | none | ✓ | — | ✓ (6 in, 1 out) | 0 |
| `mbist` | ✓ | none | none | ✓ | — | — | 0 |
| `bank_bist_wrap` | ✓ | none | none | ✓ | — | ✓ (6 in, 1 out) | 0 |
| `crc16_beat` | ✓ | none | none | ✓ | — | — | 0 |
| `ecc39_enc` | ✓ | none | none | ✓ | — | — | 0 |
| `ecc39_dec` | ✓ | none | none | ✓ | — | — | 0 |
| `link_packer` | ✓ | none | none | ✓ | — | — | 0 |
| `link_unpacker` | ✓ | none | none | ✓ | — | — | 0 |
| `tile_nic` | ✓ | none | none | ✓ | — | ✓ (1 in, 0 out) | 2 |
| `noc_router` | ✓ | none | none | ✓ | — | ✓ (4 in, 0 out) | 5 |
| `tile_dma` | ✓ | none | none | ✓ | — | ✓ (2 in, 0 out) | 1 |
| `tile_telem` | ✓ | none | none | ✓ | — | — | 0 |
| `prog_mem` | ✓ | none | none | ✓ | — | ✓ (3 in, 0 out) | 0 |
| `host_if` | ✓ | none | none | ✓ | — | ✓ (3 in, 0 out) | 0 |
| `esm` | ✓ | none | none | ✓ | — | — | 0 |
| `neo_tile` | ✓ | none | none | ✓ | 1152 = 1152 ✓ | ✓ (7 in, 4 out) — tree ✓ | 1 |
| `c16_mac_pe` | ✓ | none | none | ✓ | — | — | 0 |
| `c16_mac_pe_chk` | ✓ | none | none | ✓ | — | — | 0 |
| `c16_systolic_array` | ✓ | none | none | ✓ | 192 = 192 ✓ | — | 0 |
| `c16_neo_mac_core` | ✓ | none | none | ✓ | — | — | 0 |
| `c16_act_feeder` | ✓ | none | none | ✓ | — | — | 0 |
| `c16_acc_bank` | ✓ | none | none | ✓ | — | — | 0 |
| `c16_neo_mac_core_v02` | ✓ | none | none | ✓ | — | — | 0 |
| `c16_wbuf_mem` | ✓ | none | none | ✓ | — | ✓ (4 in, 0 out) | 0 |
| `c16_requant` | ✓ | none | none | ✓ | — | — | 1 |
| `c16_core_seq` | ✓ | none | none | ✓ | — | — | 0 |
| `c16_vec_unit` | ✓ | none | none | ✓ | — | — | 0 |
| `c16_neo_core` | ✓ | none | none | ✓ | 192 = 192 ✓ | ✓ (4 in, 0 out) | 1 |
| `top_link_packer` | ✓ | none | none | ✓ | — | — | 0 |
| `top_link_unpacker` | ✓ | none | none | ✓ | — | — | 0 |
| `top_tile_nic` | ✓ | none | none | ✓ | — | ✓ (1 in, 0 out) | 2 |
| `top_noc_router` | ✓ | none | none | ✓ | — | ✓ (4 in, 0 out) | 5 |
| `top_tile_dma` | ✓ | none | none | ✓ | — | ✓ (2 in, 0 out) | 1 |
| `top_neo_tile` | ✓ | none | none | ✓ | 1152 = 1152 ✓ | ✓ (7 in, 4 out) | 1 |
| `noc_axi_bridge` | ✓ | none | none | ✓ | — | — | 0 |
| `noc_axi_bridge_mem` | ✓ | none | none | ✓ | — | — | 0 |
| `top_ctrl` | ✓ | none | none | ✓ | — | — | 0 |
| `mesh_ctrl` | ✓ | none | none | ✓ | — | — | 0 |
| `neo_top` | ✓ | none | none | ✓ | 73728 = 73728 ✓ | — | 0 |

## Units

Own cells = the unit's library cells; cells, flops and area with its subtree (one instance of the unit); macro area from the model; clock-gated flops (drop 0.43) = the flops of the subtree whose clock pin an ICG cell drives, a child's all when its clk is a gated clock, and the ICG cells of the subtree. Timing is the unit's own logic with its child units as extracted timing models (a child's inside is the child's row): WNS against the 500 ps clock; minimum period from OpenSTA's `report_clock_min_period` (register-to-register paths of the clock, a child's clock-to-output and setup arcs included); violating endpoints of the unit's timing endpoints; the Yosys time of the unit alone.

| Unit | Own cells | Cells | Flops | Clock-gated flops | ICG cells | Cell area µm² | Macro area µm² | Total area µm² | WNS ps | Min period ps (Fmax) | Violating endpoints | Yosys s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` | 1,253 | 1,253 | 137 | 0 (0.0 %) | 0 | 159 | 0 | 159 | -595 | 1,095 (913 MHz) | 42 of 213 | 1 |
| `mac_pe_chk` | 1,694 | 1,694 | 134 | 0 (0.0 %) | 0 | 199 | 0 | 199 | -557 | 1,057 (946 MHz) | 57 of 190 | 1 |
| `systolic_array` | 38 | 1,499,942 | 157,440 | 0 (0.0 %) | 0 | 188,598 | 0 | 188,598 | -561 | 1,061 (942 MHz) | 35,024 of 86,145 | 5 |
| `neo_mac_core` | 73,840 | 1,573,782 | 191,398 | 0 (0.0 %) | 0 | 203,581 | 0 | 203,581 | -550 | 1,050 (952 MHz) | 1,208 of 36,484 | 12 |
| `act_feeder` | 8,761 | 8,761 | 449 | 0 (0.0 %) | 0 | 987 | 22,045 | 23,032 | -408 | 849 (1,178 MHz) | 284 of 1,511 | 8 |
| `acc_bank` | 18,242 | 18,242 | 2,306 | 0 (0.0 %) | 0 | 2,475 | 22,734 | 25,209 | -629 | 1,129 (886 MHz) | 2,238 of 4,444 | 13 |
| `neo_mac_core_v02` | 1,920 | 1,602,705 | 195,113 | 0 (0.0 %) | 0 | 207,448 | 44,779 | 252,228 | -762 | 1,262 (793 MHz) | 120 of 5,787 | 2 |
| `wbuf_mem` | 6,541 | 6,541 | 3 | 0 (0.0 %) | 0 | 810 | 20,151 | 20,961 | -420 | 810 (1,234 MHz) | 446 of 1,329 | 13 |
| `requant` | 111,546 | 111,546 | 6,756 | 6,756 (100.0 %) | 1 | 12,245 | 0 | 12,245 | -393 | 893 (1,120 MHz) | 4,365 of 7,016 | 159 |
| `core_seq` | 2,993 | 2,993 | 198 | 0 (0.0 %) | 0 | 355 | 0 | 355 | -422 | 922 (1,085 MHz) | 191 of 292 | 5 |
| `vec_unit` | 288,843 | 288,843 | 22,056 | 0 (0.0 %) | 0 | 34,811 | 18,611 | 53,423 | -582 | 1,082 (924 MHz) | 22,212 of 25,331 | 330 |
| `neo_core` | 2,794 | 2,129,961 | 231,099 | 208,625 (90.3 %) | 3 | 268,545 | 83,542 | 352,087 | -680 | 1,180 (847 MHz) | 3,423 of 8,335 | 3 |
| `sram_bank` | 3,745 | 3,745 | 3 | 0 (0.0 %) | 0 | 534 | 859,765 | 860,299 | -334 | 764 (1,310 MHz) | 546 of 1,205 | 11 |
| `mbist` | 1,025 | 1,025 | 77 | 0 (0.0 %) | 0 | 120 | 0 | 120 | -551 | 1,051 (951 MHz) | 608 of 644 | 10 |
| `bank_bist_wrap` | 1,245 | 6,015 | 80 | 0 (0.0 %) | 0 | 767 | 859,765 | 860,533 | -688 | 1,188 (842 MHz) | 1,583 of 1,629 | 1 |
| `crc16_beat` | 3,430 | 3,430 | 0 | 0 (—) | 0 | 502 | 0 | 502 | -233 | — | 16 of 16 | 10 |
| `ecc39_enc` | 66 | 66 | 0 | 0 (—) | 0 | 11 | 0 | 11 | 202 | — | 0 of 7 | 1 |
| `ecc39_dec` | 163 | 163 | 0 | 0 (—) | 0 | 24 | 0 | 24 | 105 | — | 0 of 34 | 1 |
| `link_packer` | 24,029 | 24,029 | 3,811 | 0 (0.0 %) | 0 | 3,286 | 0 | 3,286 | -295 | 795 (1,258 MHz) | 2,710 of 4,890 | 18 |
| `link_unpacker` | 2,876 | 2,876 | 8 | 0 (0.0 %) | 0 | 336 | 0 | 336 | -237 | 482 (2,075 MHz) | 1 of 575 | 3 |
| `tile_nic` | 146,377 | 146,377 | 17,755 | 8,734 (49.2 %) | 2 | 19,873 | 27,126 | 46,999 | -692 | 1,192 (839 MHz) | 19,228 of 23,436 | 208 |
| `noc_router` | 95,582 | 95,582 | 16,331 | 5,465 (33.5 %) | 5 | 13,841 | 0 | 13,841 | -339 | 839 (1,191 MHz) | 5,494 of 21,737 | 100 |
| `tile_dma` | 558 | 558 | 104 | 104 (100.0 %) | 1 | 80 | 0 | 80 | -47 | 547 (1,828 MHz) | 9 of 192 | 1 |
| `tile_telem` | 4,331 | 4,331 | 768 | 0 (0.0 %) | 0 | 629 | 0 | 629 | -111 | 611 (1,637 MHz) | 381 of 800 | 6 |
| `prog_mem` | 14,082 | 14,082 | 2,562 | 0 (0.0 %) | 0 | 1,960 | 0 | 1,960 | -438 | — | 370 of 2,628 | 9 |
| `host_if` | 3,495 | 3,495 | 616 | 0 (0.0 %) | 0 | 479 | 0 | 479 | -79 | 504 (1,982 MHz) | 522 of 1,199 | 3 |
| `esm` | 305 | 305 | 44 | 0 (0.0 %) | 0 | 43 | 0 | 43 | -40 | 540 (1,852 MHz) | 15 of 64 | 1 |
| `neo_tile` | 1,458 | 2,429,627 | 273,284 | 262,418 (96.0 %) | 13 | 310,051 | 970,433 | 1,280,484 | -869 | 1,369 (731 MHz) | 10,369 of 16,971 | 2 |
| `c16_mac_pe` | 1,253 | 1,253 | 137 | 0 (0.0 %) | 0 | 159 | 0 | 159 | -595 | 1,095 (913 MHz) | 42 of 213 | 1 |
| `c16_mac_pe_chk` | 1,634 | 1,634 | 131 | 0 (0.0 %) | 0 | 193 | 0 | 193 | -557 | 1,057 (946 MHz) | 56 of 186 | 1 |
| `c16_systolic_array` | 6 | 264,966 | 25,920 | 0 (0.0 %) | 0 | 32,717 | 0 | 32,717 | -561 | 1,061 (942 MHz) | 4,784 of 13,441 | 1 |
| `c16_neo_mac_core` | 13,448 | 278,414 | 31,568 | 0 (0.0 %) | 0 | 35,336 | 0 | 35,336 | -550 | 1,050 (952 MHz) | 573 of 6,618 | 3 |
| `c16_act_feeder` | 6,594 | 6,594 | 374 | 0 (0.0 %) | 0 | 761 | 1,378 | 2,138 | -452 | 789 (1,267 MHz) | 242 of 915 | 6 |
| `c16_acc_bank` | 5,187 | 5,187 | 763 | 0 (0.0 %) | 0 | 729 | 775 | 1,504 | -596 | 1,096 (912 MHz) | 576 of 1,359 | 4 |
| `c16_neo_mac_core_v02` | 560 | 290,755 | 32,985 | 0 (0.0 %) | 0 | 36,951 | 2,153 | 39,104 | -762 | 1,262 (793 MHz) | 98 of 2,021 | 1 |
| `c16_wbuf_mem` | 2,466 | 2,466 | 3 | 0 (0.0 %) | 0 | 308 | 4,198 | 4,506 | -400 | 811 (1,233 MHz) | 159 of 492 | 5 |
| `c16_requant` | 27,929 | 27,929 | 1,692 | 1,692 (100.0 %) | 1 | 3,072 | 0 | 3,072 | -369 | 845 (1,183 MHz) | 1,093 of 1,760 | 25 |
| `c16_core_seq` | 2,687 | 2,687 | 195 | 0 (0.0 %) | 0 | 324 | 0 | 324 | -445 | 945 (1,058 MHz) | 187 of 285 | 5 |
| `c16_vec_unit` | 82,601 | 82,601 | 6,508 | 0 (0.0 %) | 0 | 9,995 | 3,714 | 13,709 | -585 | 1,085 (922 MHz) | 5,913 of 7,568 | 84 |
| `c16_neo_core` | 840 | 437,894 | 43,288 | 36,369 (84.0 %) | 3 | 54,136 | 10,065 | 64,200 | -669 | 1,169 (856 MHz) | 1,034 of 3,017 | 1 |
| `top_link_packer` | 24,085 | 24,085 | 3,817 | 0 (0.0 %) | 0 | 3,291 | 0 | 3,291 | -366 | 866 (1,155 MHz) | 2,698 of 4,898 | 17 |
| `top_link_unpacker` | 2,880 | 2,880 | 8 | 0 (0.0 %) | 0 | 337 | 0 | 337 | -237 | 487 (2,052 MHz) | 1 of 577 | 3 |
| `top_tile_nic` | 147,513 | 147,513 | 18,174 | 9,153 (50.4 %) | 2 | 20,098 | 27,643 | 47,741 | -740 | 1,240 (807 MHz) | 20,781 of 23,860 | 203 |
| `top_noc_router` | 92,649 | 92,649 | 16,361 | 5,475 (33.5 %) | 5 | 13,488 | 0 | 13,488 | -369 | 869 (1,150 MHz) | 5,509 of 21,777 | 98 |
| `top_tile_dma` | 566 | 566 | 105 | 105 (100.0 %) | 1 | 81 | 0 | 81 | -47 | 547 (1,828 MHz) | 10 of 194 | 3 |
| `top_neo_tile` | 1,459 | 2,427,907 | 273,741 | 262,855 (96.0 %) | 13 | 309,931 | 970,950 | 1,280,880 | -1,829 | 2,329 (429 MHz) | 10,589 of 17,066 | 3 |
| `noc_axi_bridge` | 73,284 | 73,284 | 7,824 | 0 (0.0 %) | 0 | 9,626 | 2,131 | 11,758 | -959 | 1,359 (736 MHz) | 8,919 of 10,114 | 149 |
| `noc_axi_bridge_mem` | 59,320 | 59,320 | 6,131 | 0 (0.0 %) | 0 | 7,916 | 2,131 | 10,047 | -900 | 1,291 (775 MHz) | 7,243 of 7,883 | 127 |
| `top_ctrl` | 13,516 | 13,516 | 2,366 | 0 (0.0 %) | 0 | 1,944 | 2,067 | 4,011 | -274 | 774 (1,292 MHz) | 2,242 of 2,947 | 21 |
| `mesh_ctrl` | 45,575 | 45,575 | 1,947 | 0 (0.0 %) | 0 | 4,685 | 0 | 4,685 | -196 | 696 (1,437 MHz) | 566 of 2,800 | 34 |
| `neo_top` | 397,210 | 157,287,785 | 17,810,073 | 16,866,520 (94.7 %) | 872 | 20,098,645 | 62,159,888 | 82,258,533 | -681 | 1,181 (846 MHz) | 291,669 of 329,770 | 302 |

## Worst paths

| Unit | Start | End | Arrival ps | Slack ps |
| --- | --- | --- | --- | --- |
| `mac_pe` | `g_idx.sel_q[2]_reg` | `prod_q[14]_reg` | 1,019 | -595 |
| `mac_pe` | `g_idx.sel_q[2]_reg` | `prod_q[13]_reg` | 1,005 | -580 |
| `mac_pe_chk` | `g_half.w_hi[1]_reg` | `g_half.ph_q[12]_reg` | 982 | -557 |
| `mac_pe_chk` | `g_half.w_hi[1]_reg` | `g_half.ph_q[14]_reg` | 979 | -554 |
| `systolic_array` | `g_row[0].g_col[9].g_data.u_pe` | `g_row[0].g_col[10].g_data.u_pe` | 111 | -561 |
| `systolic_array` | `g_row[0].g_col[10].g_data.u_pe` | `g_row[0].g_col[11].g_data.u_pe` | 111 | -561 |
| `neo_mac_core` | `x_in[12]` | `u_array` | 100 | -550 |
| `neo_mac_core` | `u_skew.g_pairs.g_pair[14].g_next.r_even[4]_reg` | `u_array` | 101 | -550 |
| `act_feeder` | `cfg_ct[2]` | `s1_d[10]_reg` | 833 | -408 |
| `act_feeder` | `cfg_ct[2]` | `s1[10]_reg` | 805 | -379 |
| `acc_bank` | `fwd1_reg` | `u_mem` | 1,039 | -629 |
| `acc_bank` | `fwd1_reg` | `u_mem` | 1,028 | -618 |
| `neo_mac_core_v02` | `u_feeder` | `u_core` | 315 | -762 |
| `neo_mac_core_v02` | `u_feeder` | `u_core` | 315 | -761 |
| `wbuf_mem` | `u_mem` | `rdata[106]` | 780 | -420 |
| `wbuf_mem` | `u_mem` | `rdata[90]` | 780 | -420 |
| `requant` | `ra1[1046]_reg` | `sum2[1075]_reg` | 818 | -392 |
| `requant` | `ra1[206]_reg` | `sum2[241]_reg` | 816 | -391 |
| `core_seq` | `ext_cnt[0]_reg` | `red_last_reg` | 844 | -422 |
| `core_seq` | `state_dbg[2]_reg` | `ext_cnt[31]_reg` | 686 | -260 |
| `vec_unit` | `gsum[2158]_reg` | `gsum[1143]_reg` | 1,023 | -582 |
| `vec_unit` | `gsum[2010]_reg` | `gsum[1107]_reg` | 1,016 | -575 |
| `neo_core` | `u_dp` | `u_seq` | 402 | -680 |
| `neo_core` | `u_dp` | `u_seq_dup` | 402 | -680 |
| `sram_bank` | `u_mem` | `rdata[185]` | 694 | -334 |
| `sram_bank` | `u_mem` | `rdata[182]` | 693 | -333 |
| `mbist` | `elem[1]_reg` | `state[1]_reg` | 1,000 | -551 |
| `mbist` | `elem[1]_reg` | `state[0]_reg` | 904 | -478 |
| `bank_bist_wrap` | `u_bist` | `u_bank` | 656 | -688 |
| `bank_bist_wrap` | `u_bist` | `u_bank` | 660 | -686 |
| `crc16_beat` | `words[409]` | `crc_out[11]` | 733 | -233 |
| `crc16_beat` | `words[409]` | `crc_out[9]` | 733 | -233 |
| `ecc39_enc` | `d[14]` | `op` | 298 | 202 |
| `ecc39_enc` | `d[14]` | `p[2]` | 242 | 258 |
| `ecc39_dec` | `p[2]` | `d_out[20]` | 395 | 105 |
| `ecc39_dec` | `p[2]` | `d_out[24]` | 395 | 105 |
| `link_packer` | `g_merge.ib[524]_reg` | `g_merge.pend_thr[15]_reg` | 722 | -295 |
| `link_packer` | `g_merge.ib[524]_reg` | `g_merge.pend_thr[12]_reg` | 722 | -295 |
| `link_unpacker` | `link_flit[1029]` | `core_flit[564]` | 597 | -237 |
| `link_unpacker` | `base[4]_reg` | `core_flit[10]` | 351 | 9 |
| `tile_nic` | `lb_csel_reg` | `q_pcm[29]_reg` | 1,118 | -692 |
| `tile_nic` | `lb_csel_reg` | `q_pcm[25]_reg` | 1,101 | -675 |
| `noc_router` | `rd_a[3]_reg` | `out_flit[3761]_reg` | 762 | -340 |
| `noc_router` | `rd_a[3]_reg` | `out_flit[3762]_reg` | 762 | -340 |
| `tile_dma` | `tiles_freed[1]_reg` | `state[3]_reg` | 496 | -47 |
| `tile_dma` | `tiles_freed[0]_reg` | `tiles_freed[8]_reg` | 465 | -39 |
| `tile_telem` | `cnt[260]_reg` | `cnt[286]_reg` | 538 | -111 |
| `tile_telem` | `cnt[64]_reg` | `cnt[77]_reg` | 530 | -104 |
| `prog_mem` | `raddr[0]` | `rdata[49]_reg` | 858 | -438 |
| `prog_mem` | `raddr[0]` | `rdata[30]_reg` | 857 | -435 |
| `host_if` | `h_addr[2]` | `cfg_m[8]_reg` | 504 | -79 |
| `host_if` | `h_addr[2]` | `cfg[199]_reg` | 502 | -77 |
| `esm` | `wd_cnt[0]_reg` | `wd_cnt[20]_reg` | 458 | -40 |
| `esm` | `wd_cnt[0]_reg` | `wd_cnt[22]_reg` | 458 | -40 |
| `neo_tile` | `u_nic` | `u_core` | 862 | -869 |
| `neo_tile` | `u_nic` | `u_core` | 860 | -856 |
| `c16_mac_pe` | `g_idx.sel_q[2]_reg` | `prod_q[14]_reg` | 1,019 | -595 |
| `c16_mac_pe` | `g_idx.sel_q[2]_reg` | `prod_q[13]_reg` | 1,005 | -580 |
| `c16_mac_pe_chk` | `g_half.w_hi[1]_reg` | `g_half.ph_q[12]_reg` | 982 | -557 |
| `c16_mac_pe_chk` | `g_half.w_hi[1]_reg` | `g_half.ph_q[14]_reg` | 979 | -554 |
| `c16_systolic_array` | `g_row[0].g_col[0].g_data.u_pe` | `g_row[0].g_col[1].g_data.u_pe` | 111 | -561 |
| `c16_systolic_array` | `g_row[0].g_col[1].g_data.u_pe` | `g_row[0].g_col[2].g_data.u_pe` | 111 | -561 |
| `c16_neo_mac_core` | `x_in[12]` | `u_array` | 100 | -550 |
| `c16_neo_mac_core` | `u_skew.g_pairs.g_pair[6].g_next.r_even[4]_reg` | `u_array` | 101 | -550 |
| `c16_act_feeder` | `cfg_ct[0]` | `s1[7]_reg` | 878 | -452 |
| `c16_act_feeder` | `cfg_ct[2]` | `s1_d[7]_reg` | 845 | -419 |
| `c16_acc_bank` | `fwd1_reg` | `u_mem` | 1,006 | -596 |
| `c16_acc_bank` | `fwd1_reg` | `u_mem` | 998 | -588 |
| `c16_neo_mac_core_v02` | `u_feeder` | `u_core` | 315 | -762 |
| `c16_neo_mac_core_v02` | `u_feeder` | `u_core` | 315 | -761 |
| `c16_wbuf_mem` | `u_mem` | `rdata[67]` | 760 | -400 |
| `c16_wbuf_mem` | `u_mem` | `rdata[51]` | 760 | -400 |
| `c16_requant` | `in_acc[95]` | `ra1[130]_reg` | 793 | -369 |
| `c16_requant` | `in_acc[127]` | `ra1[179]_reg` | 792 | -369 |
| `c16_core_seq` | `ext_cnt[0]_reg` | `red_last_reg` | 871 | -445 |
| `c16_core_seq` | `f_kx[5]_reg` | `ct_rel[6]_reg` | 666 | -239 |
| `c16_vec_unit` | `lc_sa[96]_reg` | `bsq[0][65]_reg` | 1,037 | -585 |
| `c16_vec_unit` | `lc_sa[96]_reg` | `bsq[0][63]_reg` | 1,007 | -567 |
| `c16_neo_core` | `u_dp` | `u_seq` | 377 | -669 |
| `c16_neo_core` | `u_dp` | `u_seq_dup` | 377 | -669 |
| `top_link_packer` | `g_merge.ib[521]_reg` | `g_merge.pend_body[1024]_reg` | 792 | -366 |
| `top_link_packer` | `g_merge.ib[521]_reg` | `g_merge.pend_body[1031]_reg` | 792 | -366 |
| `top_link_unpacker` | `link_flit[1029]` | `core_flit[566]` | 597 | -237 |
| `top_link_unpacker` | `base[4]_reg` | `core_flit[10]` | 351 | 9 |
| `top_tile_nic` | `lb_csel_reg` | `ps_has_chk_reg` | 1,164 | -740 |
| `top_tile_nic` | `lb_csel_reg` | `q_pcm[16]_reg` | 1,163 | -736 |
| `top_noc_router` | `rd_a[2]_reg` | `out_flit[1086]_reg` | 795 | -369 |
| `top_noc_router` | `rd_a[2]_reg` | `out_flit[1177]_reg` | 795 | -369 |
| `top_tile_dma` | `tiles_freed[1]_reg` | `state[3]_reg` | 496 | -47 |
| `top_tile_dma` | `tiles_freed[0]_reg` | `tiles_freed[8]_reg` | 465 | -39 |
| `top_neo_tile` | `u_host` | `u_core` | 203 | -1,829 |
| `top_neo_tile` | `u_host` | `u_core` | 166 | -1,825 |
| `noc_axi_bridge` | `rx_flit[521]` | `crc_acc[493]_reg` | 1,382 | -959 |
| `noc_axi_bridge` | `rx_flit[521]` | `crc_acc[1005]_reg` | 1,382 | -959 |
| `noc_axi_bridge_mem` | `rx_flit[521]` | `crc_acc[484]_reg` | 1,326 | -900 |
| `noc_axi_bridge_mem` | `rx_flit[521]` | `crc_acc[612]_reg` | 1,326 | -900 |
| `top_ctrl` | `axi_cm_reg` | `s_rdata[9]_reg` | 723 | -274 |
| `top_ctrl` | `axi_cm_reg` | `s_rdata[5]_reg` | 722 | -273 |
| `mesh_ctrl` | `pwr_state[92]_reg` | `g_ls[2].sig_a[7]_reg` | 624 | -196 |
| `mesh_ctrl` | `pwr_state[92]_reg` | `g_ls[2].sig_b[3]_reg` | 624 | -196 |
| `neo_top` | `u_mctrl` | `g_y[0].g_x[0].g_tile.u_t` | 684 | -681 |
| `neo_top` | `u_mctrl` | `g_y[0].g_x[1].g_tile.u_t` | 684 | -681 |

## Period sweep

Violating endpoints of all (WNS in ps) per unit and clock period, from one OpenSTA session per unit reading each period's constraints in turn (the children's timing models do not depend on the period; the I/O at 0.2 of the period, the uncertainty 0.08, the tile's links 0.4); the minimum period is the unit's register-to-register stage, the same at every period. PE-limited units are marked †; the verdict above is over the other units of the tile.

| Unit | Min period ps | 500 ps | 750 ps | 1000 ps | 1250 ps | 1500 ps |
| --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` † | 1,095 | **42** of 213 (-595) | **16** of 213 (-370) | **10** of 213 (-190) | **2** of 213 (-10) | 0 of 213 (170) |
| `mac_pe_chk` † | 1,057 | **57** of 190 (-557) | **40** of 190 (-348) | **15** of 190 (-168) | 0 of 190 (12) | 0 of 190 (192) |
| `systolic_array` † | 1,061 | **35,024** of 86,145 (-561) | **33,792** of 86,145 (-370) | **33,408** of 86,145 (-190) | **192** of 86,145 (-10) | 0 of 86,145 (170) |
| `neo_mac_core` † | 1,050 | **1,208** of 36,484 (-550) | **1,127** of 36,484 (-370) | **1,035** of 36,484 (-190) | **6** of 36,484 (-10) | 0 of 36,484 (170) |
| `act_feeder` | 849 | **284** of 1,511 (-408) | **73** of 1,511 (-228) | **2** of 1,511 (-48) | 0 of 1,511 (132) | 0 of 1,511 (312) |
| `acc_bank` | 1,129 | **2,238** of 4,444 (-629) | **1,912** of 4,444 (-399) | **619** of 4,444 (-169) | 0 of 4,444 (61) | 0 of 4,444 (291) |
| `neo_mac_core_v02` † | 1,262 | **120** of 5,787 (-762) | **73** of 5,787 (-532) | **34** of 5,787 (-302) | **28** of 5,787 (-72) | 0 of 5,787 (158) |
| `wbuf_mem` | 810 | **446** of 1,329 (-420) | **382** of 1,329 (-240) | **282** of 1,329 (-60) | 0 of 1,329 (120) | 0 of 1,329 (300) |
| `requant` | 893 | **4,365** of 7,016 (-393) | **2,829** of 7,016 (-190) | **30** of 7,016 (-10) | 0 of 7,016 (170) | 0 of 7,016 (350) |
| `core_seq` | 922 | **191** of 292 (-422) | **11** of 292 (-198) | **1** of 292 (-18) | 0 of 292 (162) | 0 of 292 (327) |
| `vec_unit` | 1,082 | **22,212** of 25,331 (-582) | **12,572** of 25,331 (-352) | **377** of 25,331 (-122) | 0 of 25,331 (108) | 0 of 25,331 (338) |
| `neo_core` † | 1,180 | **3,423** of 8,335 (-680) | **2,528** of 8,335 (-450) | **1,992** of 8,335 (-220) | 0 of 8,335 (10) | 0 of 8,335 (240) |
| `sram_bank` | 764 | **546** of 1,205 (-334) | **525** of 1,205 (-154) | 0 of 1,205 (26) | 0 of 1,205 (206) | 0 of 1,205 (386) |
| `mbist` | 1,051 | **608** of 644 (-551) | **301** of 644 (-321) | **2** of 644 (-91) | 0 of 644 (139) | 0 of 644 (369) |
| `bank_bist_wrap` | 1,188 | **1,583** of 1,629 (-688) | **1,566** of 1,629 (-458) | **909** of 1,629 (-228) | 0 of 1,629 (2) | 0 of 1,629 (232) |
| `crc16_beat` | — | **16** of 16 (-233) | 0 of 16 (17) | 0 of 16 (267) | 0 of 16 (517) | 0 of 16 (767) |
| `ecc39_enc` | — | 0 of 7 (202) | 0 of 7 (452) | 0 of 7 (702) | 0 of 7 (952) | 0 of 7 (1,202) |
| `ecc39_dec` | — | 0 of 34 (105) | 0 of 34 (355) | 0 of 34 (605) | 0 of 34 (855) | 0 of 34 (1,105) |
| `link_packer` | 795 | **2,710** of 4,890 (-295) | **58** of 4,890 (-65) | 0 of 4,890 (165) | 0 of 4,890 (393) | 0 of 4,890 (523) |
| `link_unpacker` | 482 | **1** of 575 (-237) | **1** of 575 (-107) | 0 of 575 (23) | 0 of 575 (153) | 0 of 575 (283) |
| `tile_nic` | 1,192 | **19,228** of 23,436 (-692) | **15,659** of 23,436 (-462) | **843** of 23,436 (-232) | **1** of 23,436 (-2) | 0 of 23,436 (209) |
| `noc_router` | 839 | **5,494** of 21,737 (-339) | **5,415** of 21,737 (-109) | 0 of 21,737 (101) | 0 of 21,737 (281) | 0 of 21,737 (461) |
| `tile_dma` | 547 | **9** of 192 (-47) | 0 of 192 (169) | 0 of 192 (349) | 0 of 192 (517) | 0 of 192 (647) |
| `tile_telem` | 611 | **381** of 800 (-111) | 0 of 800 (35) | 0 of 800 (165) | 0 of 800 (295) | 0 of 800 (425) |
| `prog_mem` | — | **370** of 2,628 (-438) | **96** of 2,628 (-258) | **66** of 2,628 (-78) | 0 of 2,628 (102) | 0 of 2,628 (282) |
| `host_if` | 504 | **522** of 1,199 (-79) | 0 of 1,199 (101) | 0 of 1,199 (281) | 0 of 1,199 (461) | 0 of 1,199 (641) |
| `esm` | 540 | **15** of 64 (-40) | 0 of 64 (190) | 0 of 64 (371) | 0 of 64 (513) | 0 of 64 (643) |
| `neo_tile` † | 1,369 | **10,369** of 16,971 (-869) | **9,011** of 16,971 (-639) | **6,004** of 16,971 (-409) | **575** of 16,971 (-179) | 0 of 16,971 (51) |
| `c16_mac_pe` | 1,095 | **42** of 213 (-595) | **16** of 213 (-370) | **10** of 213 (-190) | **2** of 213 (-10) | 0 of 213 (170) |
| `c16_mac_pe_chk` | 1,057 | **56** of 186 (-557) | **41** of 186 (-346) | **14** of 186 (-166) | 0 of 186 (14) | 0 of 186 (194) |
| `c16_systolic_array` | 1,061 | **4,784** of 13,441 (-561) | **4,608** of 13,441 (-370) | **4,352** of 13,441 (-190) | **96** of 13,441 (-10) | 0 of 13,441 (170) |
| `c16_neo_mac_core` | 1,050 | **573** of 6,618 (-550) | **556** of 6,618 (-370) | **525** of 6,618 (-190) | **6** of 6,618 (-10) | 0 of 6,618 (170) |
| `c16_act_feeder` | 789 | **242** of 915 (-452) | **53** of 915 (-272) | **3** of 915 (-92) | 0 of 915 (88) | 0 of 915 (268) |
| `c16_acc_bank` | 1,096 | **576** of 1,359 (-596) | **463** of 1,359 (-366) | **118** of 1,359 (-136) | 0 of 1,359 (94) | 0 of 1,359 (324) |
| `c16_neo_mac_core_v02` | 1,262 | **98** of 2,021 (-762) | **66** of 2,021 (-532) | **36** of 2,021 (-302) | **27** of 2,021 (-72) | 0 of 2,021 (158) |
| `c16_wbuf_mem` | 811 | **159** of 492 (-400) | **134** of 492 (-220) | **118** of 492 (-40) | 0 of 492 (140) | 0 of 492 (320) |
| `c16_requant` | 845 | **1,093** of 1,760 (-369) | **695** of 1,760 (-189) | **7** of 1,760 (-9) | 0 of 1,760 (171) | 0 of 1,760 (351) |
| `c16_core_seq` | 945 | **187** of 285 (-445) | **9** of 285 (-215) | **1** of 285 (-31) | 0 of 285 (149) | 0 of 285 (329) |
| `c16_vec_unit` | 1,085 | **5,913** of 7,568 (-585) | **3,488** of 7,568 (-355) | **114** of 7,568 (-125) | 0 of 7,568 (105) | 0 of 7,568 (335) |
| `c16_neo_core` | 1,169 | **1,034** of 3,017 (-669) | **704** of 3,017 (-439) | **514** of 3,017 (-209) | 0 of 3,017 (21) | 0 of 3,017 (251) |
| `top_link_packer` | 866 | **2,698** of 4,898 (-366) | **59** of 4,898 (-136) | 0 of 4,898 (94) | 0 of 4,898 (324) | 0 of 4,898 (527) |
| `top_link_unpacker` | 487 | **1** of 577 (-237) | **1** of 577 (-107) | 0 of 577 (23) | 0 of 577 (153) | 0 of 577 (283) |
| `top_tile_nic` | 1,240 | **20,781** of 23,860 (-740) | **15,263** of 23,860 (-510) | **1,208** of 23,860 (-283) | **130** of 23,860 (-103) | 0 of 23,860 (77) |
| `top_noc_router` | 869 | **5,509** of 21,777 (-369) | **5,426** of 21,777 (-139) | 0 of 21,777 (86) | 0 of 21,777 (266) | 0 of 21,777 (446) |
| `top_tile_dma` | 547 | **10** of 194 (-47) | 0 of 194 (170) | 0 of 194 (350) | 0 of 194 (519) | 0 of 194 (649) |
| `top_neo_tile` | 2,329 | **10,589** of 17,066 (-1,829) | **7,832** of 17,066 (-1,599) | **3,279** of 17,066 (-1,369) | **721** of 17,066 (-1,139) | **57** of 17,066 (-909) |
| `noc_axi_bridge` | 1,359 | **8,919** of 10,114 (-959) | **6,726** of 10,114 (-779) | **6,012** of 10,114 (-599) | **4,225** of 10,114 (-419) | **1,339** of 10,114 (-239) |
| `noc_axi_bridge_mem` | 1,291 | **7,243** of 7,883 (-900) | **4,547** of 7,883 (-720) | **3,712** of 7,883 (-540) | **2,618** of 7,883 (-360) | **1,157** of 7,883 (-180) |
| `top_ctrl` | 774 | **2,242** of 2,947 (-274) | **65** of 2,947 (-44) | 0 of 2,947 (186) | 0 of 2,947 (398) | 0 of 2,947 (528) |
| `mesh_ctrl` | 696 | **566** of 2,800 (-196) | **55** of 2,800 (-7) | 0 of 2,800 (173) | 0 of 2,800 (353) | 0 of 2,800 (533) |
| `neo_top` | 1,181 | **291,669** of 329,770 (-681) | **1,006** of 329,770 (-451) | **256** of 329,770 (-221) | 0 of 329,770 (9) | 0 of 329,770 (239) |

## The tile: area by unit

Total 1,280,484 µm² = 1.280 mm² (cells 310,051 µm², macros 970,433 µm²); 2,429,627 cells, 273,284 flops, 1152 PEs.

| Unit | Instances in the tile | Own cells (one) | Area µm² (one, with subtree) | Area µm² (all instances, own logic + own macros only) | Share of the tile |
| --- | --- | --- | --- | --- | --- |
| `sram_bank` | 1 | 3,745 | 860,299 | 860,299 | 67.2 % |
| `mac_pe` | 1,024 | 1,253 | 159 | 163,124 | 12.7 % |
| `vec_unit` | 1 | 288,843 | 53,423 | 53,423 | 4.2 % |
| `tile_nic` | 1 | 146,377 | 46,999 | 46,999 | 3.7 % |
| `mac_pe_chk` | 128 | 1,694 | 199 | 25,470 | 2.0 % |
| `acc_bank` | 1 | 18,242 | 25,209 | 25,209 | 2.0 % |
| `requant` | 2 | 111,546 | 12,245 | 24,490 | 1.9 % |
| `act_feeder` | 1 | 8,761 | 23,032 | 23,032 | 1.8 % |
| `wbuf_mem` | 1 | 6,541 | 20,961 | 20,961 | 1.6 % |
| `neo_mac_core` | 1 | 73,840 | 203,581 | 14,983 | 1.2 % |
| `noc_router` | 1 | 95,582 | 13,841 | 13,841 | 1.1 % |
| `link_packer` | 1 | 24,029 | 3,286 | 3,286 | 0.3 % |
| `prog_mem` | 1 | 14,082 | 1,960 | 1,960 | 0.2 % |
| `core_seq` | 2 | 2,993 | 355 | 711 | 0.1 % |
| `tile_telem` | 1 | 4,331 | 629 | 629 | 0.0 % |
| `host_if` | 1 | 3,495 | 479 | 479 | 0.0 % |
| `neo_mac_core_v02` | 1 | 1,920 | 252,228 | 406 | 0.0 % |
| `link_unpacker` | 1 | 2,876 | 336 | 336 | 0.0 % |
| `neo_core` | 1 | 2,794 | 352,087 | 274 | 0.0 % |
| `tile_dma` | 2 | 558 | 80 | 159 | 0.0 % |
| `neo_tile` | 1 | 1,458 | 1,280,484 | 131 | 0.0 % |
| `mbist` | 1 | 1,025 | 120 | 120 | 0.0 % |
| `bank_bist_wrap` | 1 | 1,245 | 860,533 | 114 | 0.0 % |
| `esm` | 1 | 305 | 43 | 43 | 0.0 % |
| `systolic_array` | 1 | 38 | 188,598 | 3 | 0.0 % |

| Macro | Instances in the tile | Word × depth | Bits (all) | Area µm² (all) | Ports used | Model factor |
| --- | --- | --- | --- | --- | --- | --- |
| `neo_mem_sp_w624_d32768_g16` | 1 | 624 × 32768 | 20,447,232 | 859,765 | 1W1R (registered read) | 1.0 |
| `neo_mem_rf_w1068_d512_g1_rb1` | 1 | 1068 × 512 | 546,816 | 22,993 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w1056_d512_g1_rb1` | 1 | 1056 × 512 | 540,672 | 22,734 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w512_d1024_g2_rb1` | 1 | 512 × 1024 | 524,288 | 22,045 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w936_d512_g2_rb1` | 1 | 936 × 512 | 479,232 | 20,151 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w33_d256_g1_rb1` | 33 | 33 × 256 | 278,784 | 11,722 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w512_d256_g2_rb1` | 1 | 512 × 256 | 131,072 | 5,511 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w1024_d64_g32_rb0` | 1 | 1024 × 64 | 65,536 | 4,133 | 1W + asynchronous read (port A) | 1.5 |
| `neo_mem_rf_w64_d512_g2_rb1` | 1 | 64 × 512 | 32,768 | 1,378 | 1W1R (registered read, port B) | 1.0 |

Endpoint slack histogram of `neo_tile` (its own registers and its children's inputs; bins of a tenth of the period; 16,971 endpoints, 10,369 violating):

| Slack | Endpoints |
| --- | --- |
| -900..-850 ps | 3 |
| -850..-800 ps | 77 |
| -800..-750 ps | 186 |
| -750..-700 ps | 240 |
| -700..-650 ps | 326 |
| -650..-600 ps | 352 |
| -600..-550 ps | 496 |
| -550..-500 ps | 455 |
| -500..-450 ps | 369 |
| -450..-400 ps | 246 |
| -400..-350 ps | 318 |
| -350..-300 ps | 2,485 |
| -300..-250 ps | 2,154 |
| -250..-200 ps | 1,355 |
| -200..-150 ps | 412 |
| -150..-100 ps | 610 |
| -100..-50 ps | 194 |
| -50..0 ps | 91 |
| 0..50 ps | 574 |
| 50..100 ps | 717 |
| 100..150 ps | 467 |
| 150..200 ps | 4,342 |
| 200..250 ps | 39 |
| 250..300 ps | 214 |
| 300..350 ps | 10 |
| 350..400 ps | 4 |
| 400..450 ps | 5 |
| 450..500 ps | 15 |
| 500..550 ps | 2 |
| 550..600 ps | 3 |
| 600..650 ps | 1 |
| 650..700 ps | 8 |
| 700..750 ps | 7 |
| 750..800 ps | 5 |
| 800..850 ps | 5 |
| 850..900 ps | 3 |
| 950..1000 ps | 1 |
| 1000..1050 ps | 5 |
| 1050..1100 ps | 8 |
| 1100..1150 ps | 14 |
| 1150..1200 ps | 33 |
| 1200..1250 ps | 15 |
| 1250..1300 ps | 21 |
| 1300..1350 ps | 16 |
| 1350..1400 ps | 12 |
| 1400..1450 ps | 4 |
| 1500..1550 ps | 1 |
| 1550..1600 ps | 3 |
| 1650..1700 ps | 2 |
| 1700..1750 ps | 29 |
| 1750..1800 ps | 17 |

Endpoint slack histogram of `neo_core` (its own registers and its children's inputs; bins of a tenth of the period; 8,335 endpoints, 3,423 violating):

| Slack | Endpoints |
| --- | --- |
| -700..-650 ps | 2 |
| -650..-600 ps | 10 |
| -600..-550 ps | 69 |
| -550..-500 ps | 376 |
| -500..-450 ps | 1,595 |
| -450..-400 ps | 4 |
| -400..-350 ps | 286 |
| -350..-300 ps | 129 |
| -300..-250 ps | 16 |
| -250..-200 ps | 31 |
| -200..-150 ps | 93 |
| -150..-100 ps | 270 |
| -100..-50 ps | 302 |
| -50..0 ps | 240 |
| 0..50 ps | 355 |
| 50..100 ps | 532 |
| 100..150 ps | 1,214 |
| 150..200 ps | 16 |
| 200..250 ps | 523 |
| 250..300 ps | 813 |
| 300..350 ps | 1,096 |
| 350..400 ps | 4 |
| 400..450 ps | 6 |
| 450..500 ps | 15 |
| 500..550 ps | 19 |
| 550..600 ps | 3 |
| 600..650 ps | 2 |
| 650..700 ps | 4 |
| 700..750 ps | 8 |
| 750..800 ps | 4 |
| 800..850 ps | 9 |
| 850..900 ps | 1 |
| 900..950 ps | 2 |
| 950..1000 ps | 2 |
| 1000..1050 ps | 8 |
| 1050..1100 ps | 9 |
| 1100..1150 ps | 12 |
| 1150..1200 ps | 39 |
| 1200..1250 ps | 33 |
| 1250..1300 ps | 23 |
| 1300..1350 ps | 25 |
| 1350..1400 ps | 23 |
| 1400..1450 ps | 12 |
| 1500..1550 ps | 1 |
| 1550..1600 ps | 2 |
| 1600..1650 ps | 1 |
| 1700..1750 ps | 18 |
| 1750..1800 ps | 76 |
| 1800..1850 ps | 2 |

