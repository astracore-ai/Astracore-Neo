# Synthesis report — nangate45 TT, 500 ps clock (2026-10-20 11:25:59 UTC)

Tools: Yosys 0.69+274 (git sha1 7513f8df8, Release, Clang /usr/bin/clang++ 21.1.8) (slang front end); OpenSTA 3.1.0. Libraries: NangateOpenCellLibrary_typical.lib. Run time 28.3 min.

**Period sweep (500, 750, 1000, 1250, 1500 ps):** no swept period has every unit outside the PE-limited ones clean; still violating at 1500 ps: bank_bist_wrap.

The largest minimum period outside the PE-limited units: `bank_bist_wrap` 1,518 ps (register to register; the sweep also counts the I/O paths at 0.2 of the period). The PE-limited units' own minimum periods: `neo_tile` 1,409, `neo_core` 1,396, `neo_mac_core_v02` 1,529, `neo_mac_core` 1,090, `systolic_array` 1,124, `mac_pe` 1,151, `mac_pe_chk` 1,154 ps.

Macro model for the black-boxed memory leaves (`neo_mem_sp` / `neo_mem_rf`): ORFS FakeRAM45 macros fakeram45_2048x39 (0.569 um2/bit, 0.435 ns access), fakeram45_512x64 (0.528 um2/bit, 0.305 ns access): 0.557 um2/bit (the sum of their areas over the sum of their bits), 0.435 ns access (the slowest), 0.05 ns setup/hold; area factors {'neo_mem_sp': 1.0, 'neo_mem_rf': 1.5, 'neo_mem_rf_b': 1.0, 'neo_mem_rf_rb': 2.0} (register-file style reads cost bit-cell area). Nangate45 (FreePDK45) is a 45 nm open library: a second data point, not a 2 GHz node. Every figure here is a proxy for the N5A target: the paths and the shares are the design's, the absolute picoseconds and square microns are the open library's. A unit is one module synthesized at its silicon parameters with its children black-boxed; 'with subtree' sums the children in (instances × unit).

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
| `mac_pe` | 1,121 | 1,121 | 137 | 0 (0.0 %) | 0 | 2,105 | 0 | 2,105 | -651 | 1,151 (869 MHz) | 41 of 213 | 1 |
| `mac_pe_chk` | 1,301 | 1,301 | 134 | 0 (0.0 %) | 0 | 2,633 | 0 | 2,633 | -654 | 1,154 (867 MHz) | 58 of 190 | 1 |
| `systolic_array` | 38 | 1,314,470 | 157,440 | 0 (0.0 %) | 0 | 2,492,218 | 0 | 2,492,218 | -624 | 1,124 (889 MHz) | 33,792 of 86,145 | 5 |
| `neo_mac_core` | 37,919 | 1,352,389 | 191,398 | 0 (0.0 %) | 0 | 2,682,349 | 0 | 2,682,349 | -594 | 1,090 (917 MHz) | 1,210 of 36,484 | 9 |
| `act_feeder` | 7,414 | 7,414 | 449 | 0 (0.0 %) | 0 | 12,801 | 292,214 | 305,015 | -513 | 968 (1,033 MHz) | 809 of 1,511 | 7 |
| `acc_bank` | 16,297 | 16,297 | 2,306 | 0 (0.0 %) | 0 | 33,303 | 301,345 | 334,648 | -784 | 1,284 (779 MHz) | 4,028 of 4,444 | 13 |
| `neo_mac_core_v02` | 960 | 1,377,060 | 195,113 | 0 (0.0 %) | 0 | 2,733,561 | 593,559 | 3,327,119 | -1,029 | 1,529 (654 MHz) | 1,656 of 5,787 | 1 |
| `wbuf_mem` | 5,229 | 5,229 | 3 | 0 (0.0 %) | 0 | 8,193 | 267,101 | 275,295 | -641 | 1,092 (916 MHz) | 428 of 1,329 | 12 |
| `requant` | 74,678 | 74,678 | 6,756 | 6,756 (100.0 %) | 1 | 163,718 | 0 | 163,718 | -480 | 980 (1,020 MHz) | 4,399 of 7,016 | 168 |
| `core_seq` | 2,992 | 2,992 | 198 | 0 (0.0 %) | 0 | 5,106 | 0 | 5,106 | -365 | 865 (1,157 MHz) | 197 of 292 | 4 |
| `vec_unit` | 236,323 | 236,323 | 22,056 | 0 (0.0 %) | 0 | 474,510 | 246,698 | 721,208 | -642 | 1,142 (875 MHz) | 20,992 of 25,331 | 328 |
| `neo_core` | 1,654 | 1,775,606 | 231,099 | 208,625 (90.3 %) | 3 | 3,556,774 | 1,107,358 | 4,664,132 | -896 | 1,396 (717 MHz) | 5,352 of 8,335 | 3 |
| `sram_bank` | 3,830 | 3,830 | 3 | 0 (0.0 %) | 0 | 5,504 | 11,396,329 | 11,401,833 | -590 | 1,045 (957 MHz) | 545 of 1,205 | 11 |
| `mbist` | 940 | 940 | 77 | 0 (0.0 %) | 0 | 1,537 | 0 | 1,537 | -343 | 812 (1,231 MHz) | 611 of 644 | 10 |
| `bank_bist_wrap` | 623 | 5,393 | 80 | 0 (0.0 %) | 0 | 8,450 | 11,396,329 | 11,404,779 | -1,018 | 1,518 (659 MHz) | 1,599 of 1,629 | 0 |
| `crc16_beat` | 3,579 | 3,579 | 0 | 0 (—) | 0 | 5,407 | 0 | 5,407 | -299 | — | 16 of 16 | 10 |
| `ecc39_enc` | 66 | 66 | 0 | 0 (—) | 0 | 132 | 0 | 132 | 176 | — | 0 of 7 | 0 |
| `ecc39_dec` | 163 | 163 | 0 | 0 (—) | 0 | 274 | 0 | 274 | 91 | — | 0 of 34 | 1 |
| `link_packer` | 18,500 | 18,500 | 3,811 | 0 (0.0 %) | 0 | 36,769 | 0 | 36,769 | -240 | 740 (1,351 MHz) | 3,753 of 4,890 | 17 |
| `link_unpacker` | 2,879 | 2,879 | 8 | 0 (0.0 %) | 0 | 3,363 | 0 | 3,363 | -268 | 494 (2,024 MHz) | 10 of 575 | 3 |
| `tile_nic` | 126,543 | 126,543 | 17,755 | 8,734 (49.2 %) | 2 | 241,189 | 359,560 | 600,748 | -775 | 1,275 (784 MHz) | 20,068 of 23,436 | 236 |
| `noc_router` | 76,558 | 76,558 | 16,331 | 5,465 (33.5 %) | 5 | 160,226 | 0 | 160,226 | -349 | 849 (1,178 MHz) | 5,513 of 21,737 | 109 |
| `tile_dma` | 506 | 506 | 104 | 104 (100.0 %) | 1 | 1,074 | 0 | 1,074 | -123 | 623 (1,606 MHz) | 30 of 192 | 1 |
| `tile_telem` | 3,647 | 3,647 | 768 | 0 (0.0 %) | 0 | 7,775 | 0 | 7,775 | -60 | 549 (1,823 MHz) | 209 of 800 | 5 |
| `prog_mem` | 8,902 | 8,902 | 2,562 | 0 (0.0 %) | 0 | 22,126 | 0 | 22,126 | -489 | — | 915 of 2,628 | 8 |
| `host_if` | 2,112 | 2,112 | 616 | 0 (0.0 %) | 0 | 5,608 | 0 | 5,608 | -63 | — | 503 of 1,199 | 3 |
| `esm` | 334 | 334 | 44 | 0 (0.0 %) | 0 | 582 | 0 | 582 | -38 | 538 (1,859 MHz) | 6 of 64 | 1 |
| `neo_tile` | 1,448 | 2,022,934 | 273,284 | 262,418 (96.0 %) | 13 | 4,046,569 | 12,863,246 | 16,909,815 | -909 | 1,409 (710 MHz) | 10,379 of 16,971 | 3 |
| `c16_mac_pe` | 1,121 | 1,121 | 137 | 0 (0.0 %) | 0 | 2,105 | 0 | 2,105 | -651 | 1,151 (869 MHz) | 41 of 213 | 1 |
| `c16_mac_pe_chk` | 1,354 | 1,354 | 131 | 0 (0.0 %) | 0 | 2,671 | 0 | 2,671 | -660 | 1,160 (862 MHz) | 57 of 186 | 2 |
| `c16_systolic_array` | 6 | 230,150 | 25,920 | 0 (0.0 %) | 0 | 440,315 | 0 | 440,315 | -625 | 1,125 (889 MHz) | 4,608 of 13,441 | 1 |
| `c16_neo_mac_core` | 7,355 | 237,505 | 31,568 | 0 (0.0 %) | 0 | 473,825 | 0 | 473,825 | -594 | 1,090 (917 MHz) | 574 of 6,618 | 2 |
| `c16_act_feeder` | 5,983 | 5,983 | 374 | 0 (0.0 %) | 0 | 10,173 | 18,263 | 28,437 | -508 | 960 (1,041 MHz) | 501 of 915 | 7 |
| `c16_acc_bank` | 4,584 | 4,584 | 763 | 0 (0.0 %) | 0 | 9,701 | 10,273 | 19,974 | -662 | 1,162 (861 MHz) | 1,024 of 1,359 | 4 |
| `c16_neo_mac_core_v02` | 280 | 248,352 | 32,985 | 0 (0.0 %) | 0 | 495,189 | 28,536 | 523,725 | -1,029 | 1,529 (654 MHz) | 609 of 2,021 | 0 |
| `c16_wbuf_mem` | 2,009 | 2,009 | 3 | 0 (0.0 %) | 0 | 3,200 | 55,646 | 58,846 | -605 | 1,039 (962 MHz) | 158 of 492 | 5 |
| `c16_requant` | 19,018 | 19,018 | 1,692 | 1,692 (100.0 %) | 1 | 41,349 | 0 | 41,349 | -478 | 971 (1,030 MHz) | 1,093 of 1,760 | 24 |
| `c16_core_seq` | 2,704 | 2,704 | 195 | 0 (0.0 %) | 0 | 4,611 | 0 | 4,611 | -334 | 834 (1,199 MHz) | 194 of 285 | 5 |
| `c16_vec_unit` | 67,616 | 67,616 | 6,508 | 0 (0.0 %) | 0 | 135,607 | 49,225 | 184,832 | -642 | 1,142 (875 MHz) | 6,177 of 7,568 | 83 |
| `c16_neo_core` | 540 | 361,961 | 43,288 | 36,369 (84.0 %) | 3 | 726,880 | 133,408 | 860,288 | -896 | 1,396 (717 MHz) | 1,553 of 3,017 | 1 |
| `top_link_packer` | 18,382 | 18,382 | 3,817 | 0 (0.0 %) | 0 | 36,857 | 0 | 36,857 | -191 | 691 (1,447 MHz) | 3,755 of 4,898 | 16 |
| `top_link_unpacker` | 2,880 | 2,880 | 8 | 0 (0.0 %) | 0 | 3,365 | 0 | 3,365 | -269 | 499 (2,006 MHz) | 10 of 577 | 3 |
| `top_tile_nic` | 128,376 | 128,376 | 18,174 | 9,153 (50.4 %) | 2 | 243,934 | 366,408 | 610,343 | -753 | 1,253 (798 MHz) | 20,132 of 23,860 | 223 |
| `top_noc_router` | 83,571 | 83,571 | 16,361 | 5,475 (33.5 %) | 5 | 163,775 | 0 | 163,775 | -322 | 822 (1,216 MHz) | 6,124 of 21,777 | 101 |
| `top_tile_dma` | 531 | 531 | 105 | 105 (100.0 %) | 1 | 1,100 | 0 | 1,100 | -19 | 519 (1,927 MHz) | 3 of 194 | 1 |
| `top_neo_tile` | 1,452 | 2,031,717 | 273,741 | 262,855 (96.0 %) | 13 | 4,053,011 | 12,870,095 | 16,923,106 | -1,950 | 2,450 (408 MHz) | 10,586 of 17,066 | 2 |
| `noc_axi_bridge` | 65,787 | 65,787 | 7,824 | 0 (0.0 %) | 0 | 113,788 | 28,251 | 142,039 | -981 | 1,481 (675 MHz) | 8,929 of 10,114 | 166 |
| `noc_axi_bridge_mem` | 51,685 | 51,685 | 6,131 | 0 (0.0 %) | 0 | 91,621 | 28,251 | 119,872 | -884 | 1,319 (758 MHz) | 7,241 of 7,883 | 141 |
| `top_ctrl` | 11,122 | 11,122 | 2,366 | 0 (0.0 %) | 0 | 24,309 | 27,395 | 51,704 | -582 | 1,082 (924 MHz) | 2,207 of 2,947 | 22 |
| `mesh_ctrl` | 32,855 | 32,855 | 1,947 | 0 (0.0 %) | 0 | 56,766 | 0 | 56,766 | -292 | 716 (1,397 MHz) | 768 of 2,800 | 36 |
| `neo_top` | 236,744 | 131,576,855 | 17,810,073 | 16,866,520 (94.7 %) | 872 | 262,485,774 | 823,939,494 | 1,086,425,269 | -392 | 892 (1,121 MHz) | 291,022 of 329,770 | 277 |

## Worst paths

| Unit | Start | End | Arrival ps | Slack ps |
| --- | --- | --- | --- | --- |
| `mac_pe` | `g_idx.sel_q[3]_reg` | `prod_q[12]_reg` | 1,100 | -700 |
| `mac_pe` | `g_idx.sel_q[3]_reg` | `prod_q[13]_reg` | 1,100 | -600 |
| `mac_pe_chk` | `g_half.w_lo[3]_reg` | `prod_q[13]_reg` | 1,100 | -700 |
| `mac_pe_chk` | `g_half.w_lo[3]_reg` | `prod_q[14]_reg` | 1,100 | -700 |
| `systolic_array` | `g_row[0].g_col[31].g_data.u_pe` | `g_row[0].g_col[32].g_check.u_pe` | 100 | -600 |
| `systolic_array` | `g_row[0].g_col[31].g_data.u_pe` | `g_row[0].g_col[33].g_check.u_pe` | 100 | -600 |
| `neo_mac_core` | `x_in[26]` | `u_array` | 100 | -600 |
| `neo_mac_core` | `x_in[10]` | `u_array` | 100 | -600 |
| `act_feeder` | `cfg_ky[3]` | `s2[10]_reg` | 900 | -500 |
| `act_feeder` | `cfg_ky[1]` | `s2_d[10]_reg` | 900 | -500 |
| `acc_bank` | `u_mem` | `u_abft.r_q[127]_reg` | 1,200 | -800 |
| `acc_bank` | `u_mem` | `u_abft.r_q[63]_reg` | 1,200 | -800 |
| `neo_mac_core_v02` | `u_feeder` | `u_core` | 500 | -1,000 |
| `neo_mac_core_v02` | `u_feeder` | `u_core` | 500 | -1,000 |
| `wbuf_mem` | `u_mem` | `rdata[42]` | 1,000 | -600 |
| `wbuf_mem` | `u_mem` | `rdata[35]` | 1,000 | -600 |
| `requant` | `shft[18][1]_reg` | `ra1[667]_reg` | 900 | -500 |
| `requant` | `tbl_fault_inject` | `ra1[1534]_reg` | 900 | -500 |
| `core_seq` | `ext_cnt[0]_reg` | `red_last_reg` | 800 | -400 |
| `core_seq` | `f_kx[1]_reg` | `f_kx[14]_reg` | 700 | -300 |
| `vec_unit` | `g_lut[29].u_lutv` | `g_lane[29].u_ip.ra[15]_reg` | 1,100 | -600 |
| `vec_unit` | `g_lut[30].u_lutv` | `g_lane[30].u_ip.ra[15]_reg` | 1,100 | -600 |
| `neo_core` | `u_dp` | `u_rq` | 600 | -900 |
| `neo_core` | `u_dp` | `u_rq` | 500 | -900 |
| `sram_bank` | `u_mem` | `rdata[247]` | 900 | -600 |
| `sram_bank` | `u_mem` | `rdata[249]` | 900 | -600 |
| `mbist` | `elem[1]_reg` | `wdata[0]` | 700 | -300 |
| `mbist` | `elem[1]_reg` | `wdata[100]` | 700 | -300 |
| `bank_bist_wrap` | `u_bank` | `u_bist` | 1,000 | -1,000 |
| `bank_bist_wrap` | `u_bank` | `u_bist` | 1,000 | -1,000 |
| `crc16_beat` | `words[168]` | `crc_out[10]` | 800 | -300 |
| `crc16_beat` | `words[168]` | `crc_out[3]` | 800 | -300 |
| `ecc39_enc` | `d[17]` | `op` | 300 | 200 |
| `ecc39_enc` | `d[17]` | `p[1]` | 200 | 300 |
| `ecc39_dec` | `d[17]` | `d_out[14]` | 400 | 100 |
| `ecc39_dec` | `d[17]` | `d_out[18]` | 400 | 100 |
| `link_packer` | `g_merge.ib[534]_reg` | `g_merge.pend_thr[10]_reg` | 700 | -200 |
| `link_packer` | `g_merge.ib[534]_reg` | `g_merge.pend_thr[11]_reg` | 700 | -200 |
| `link_unpacker` | `link_flit[165]` | `core_flit[564]` | 600 | -300 |
| `link_unpacker` | `idx[4]_reg` | `core_flit[519]` | 400 | 0 |
| `tile_nic` | `lb_csel_reg` | `q_pcm[30]_reg` | 1,200 | -800 |
| `tile_nic` | `lb_csel_reg` | `q_pcm[22]_reg` | 1,200 | -800 |
| `noc_router` | `rd_a[3]_reg` | `out_flit[1115]_reg` | 800 | -300 |
| `noc_router` | `rd_a[3]_reg` | `out_flit[1116]_reg` | 800 | -300 |
| `tile_dma` | `tiles_freed[3]_reg` | `state[1]_reg` | 500 | -100 |
| `tile_dma` | `tiles_freed[3]_reg` | `state[2]_reg` | 500 | -100 |
| `tile_telem` | `rsel[0]` | `rdata[11]` | 400 | -100 |
| `tile_telem` | `rsel[0]` | `rdata[0]` | 400 | 0 |
| `prog_mem` | `raddr[0]` | `rdata[51]_reg` | 900 | -500 |
| `prog_mem` | `raddr[0]` | `rdata[53]_reg` | 900 | -500 |
| `host_if` | `h_addr[1]` | `h_rdata[2]_reg` | 500 | -100 |
| `host_if` | `h_addr[1]` | `h_rdata[3]_reg` | 500 | -100 |
| `esm` | `wd_cnt[1]_reg` | `wd_cnt[18]_reg` | 500 | 0 |
| `esm` | `wd_cnt[2]_reg` | `wd_cnt[4]_reg` | 400 | 0 |
| `neo_tile` | `u_bank` | `u_nic` | 1,000 | -900 |
| `neo_tile` | `u_bank` | `u_pack` | 1,300 | -900 |
| `c16_mac_pe` | `g_idx.sel_q[3]_reg` | `prod_q[12]_reg` | 1,100 | -700 |
| `c16_mac_pe` | `g_idx.sel_q[3]_reg` | `prod_q[13]_reg` | 1,100 | -600 |
| `c16_mac_pe_chk` | `g_half.w_hi[3]_reg` | `g_half.ph_q[11]_reg` | 1,100 | -700 |
| `c16_mac_pe_chk` | `g_half.w_hi[3]_reg` | `g_half.ph_q[12]_reg` | 1,100 | -600 |
| `c16_systolic_array` | `g_row[0].g_col[7].g_data.u_pe` | `g_row[0].g_col[10].g_check.u_pe` | 100 | -600 |
| `c16_systolic_array` | `g_row[0].g_col[7].g_data.u_pe` | `g_row[0].g_col[11].g_check.u_pe` | 100 | -600 |
| `c16_neo_mac_core` | `x_in[26]` | `u_array` | 100 | -600 |
| `c16_neo_mac_core` | `x_in[10]` | `u_array` | 100 | -600 |
| `c16_act_feeder` | `u_mem` | `ctrl_err` | 900 | -500 |
| `c16_act_feeder` | `u_mem` | `ctrl_err_sticky_reg` | 900 | -500 |
| `c16_acc_bank` | `u_mem` | `u_mem` | 1,100 | -700 |
| `c16_acc_bank` | `u_mem` | `u_mem` | 1,100 | -700 |
| `c16_neo_mac_core_v02` | `u_feeder` | `u_core` | 500 | -1,000 |
| `c16_neo_mac_core_v02` | `u_feeder` | `u_core` | 500 | -1,000 |
| `c16_wbuf_mem` | `u_mem` | `rdata[24]` | 1,000 | -600 |
| `c16_wbuf_mem` | `u_mem` | `rdata[35]` | 1,000 | -600 |
| `c16_requant` | `tbl_fault_inject` | `ra1[358]_reg` | 900 | -500 |
| `c16_requant` | `tbl_fault_inject` | `ra1[359]_reg` | 900 | -500 |
| `c16_core_seq` | `f_kx[5]_reg` | `wcnt[12]_reg` | 800 | -300 |
| `c16_core_seq` | `rcnt[9]_reg` | `ct_rel[15]_reg` | 700 | -300 |
| `c16_vec_unit` | `g_lut[0].u_lutv` | `g_lane[0].u_ip.ra[15]_reg` | 1,100 | -600 |
| `c16_vec_unit` | `g_lut[1].u_lutv` | `g_lane[1].u_ip.ra[15]_reg` | 1,100 | -600 |
| `c16_neo_core` | `u_dp` | `u_rq` | 600 | -900 |
| `c16_neo_core` | `u_dp` | `u_rq` | 500 | -900 |
| `top_link_packer` | `g_merge.pend_cnt[0]_reg` | `g_merge.pbuf0[675]_reg` | 600 | -200 |
| `top_link_packer` | `g_merge.pend_cnt[0]_reg` | `g_merge.pbuf0[679]_reg` | 600 | -200 |
| `top_link_unpacker` | `link_flit[40]` | `core_flit[566]` | 600 | -300 |
| `top_link_unpacker` | `idx[4]_reg` | `core_flit[519]` | 400 | 0 |
| `top_tile_nic` | `lb_csel_reg` | `q_pcm[16]_reg` | 1,200 | -800 |
| `top_tile_nic` | `lb_csel_reg` | `ps_has_chk_reg` | 1,200 | -700 |
| `top_noc_router` | `rd_a[3]_reg` | `out_flit[3485]_reg` | 700 | -300 |
| `top_noc_router` | `rd_a[3]_reg` | `out_flit[3477]_reg` | 700 | -300 |
| `top_tile_dma` | `state[3]_reg` | `state[1]_reg` | 400 | 0 |
| `top_tile_dma` | `state[3]_reg` | `state[2]_reg` | 400 | 0 |
| `top_neo_tile` | `u_host` | `u_core` | 200 | -2,000 |
| `top_neo_tile` | `u_host` | `u_core` | 100 | -1,900 |
| `noc_axi_bridge` | `g_host.wq_addr[11]_reg` | `tx_crc[1]_reg` | 1,400 | -1,000 |
| `noc_axi_bridge` | `g_host.wq_addr[11]_reg` | `tx_crc[8]_reg` | 1,400 | -1,000 |
| `noc_axi_bridge_mem` | `rx_flit[521]` | `crc_acc[193]_reg` | 1,300 | -900 |
| `noc_axi_bridge_mem` | `rx_flit[521]` | `crc_acc[401]_reg` | 1,300 | -900 |
| `top_ctrl` | `axi_cm_reg` | `s_rdata[19]_reg` | 1,000 | -600 |
| `top_ctrl` | `axi_cm_reg` | `s_rdata[5]_reg` | 1,000 | -600 |
| `mesh_ctrl` | `b2i[1]_reg` | `rq_rsp[0]` | 700 | -300 |
| `mesh_ctrl` | `b2i[1]_reg` | `rq_rsp[10]` | 700 | -300 |
| `neo_top` | `u_mctrl` | `g_y[0].g_x[0].g_tile.u_t` | 100 | -400 |
| `neo_top` | `u_mctrl` | `g_y[0].g_x[1].g_tile.u_t` | 100 | -400 |

## Period sweep

Violating endpoints of all (WNS in ps) per unit and clock period, from one OpenSTA session per unit reading each period's constraints in turn (the children's timing models do not depend on the period; the I/O at 0.2 of the period, the uncertainty 0.08, the tile's links 0.4); the minimum period is the unit's register-to-register stage, the same at every period. PE-limited units are marked †; the verdict above is over the other units of the tile.

| Unit | Min period ps | 500 ps | 750 ps | 1000 ps | 1250 ps | 1500 ps |
| --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` † | 1,151 | **41** of 213 (-651) | **12** of 213 (-421) | **10** of 213 (-234) | **6** of 213 (-54) | 0 of 213 (126) |
| `mac_pe_chk` † | 1,154 | **58** of 190 (-654) | **36** of 190 (-444) | **19** of 190 (-264) | **10** of 190 (-84) | 0 of 190 (96) |
| `systolic_array` † | 1,124 | **33,792** of 86,145 (-624) | **33,792** of 86,145 (-414) | **33,792** of 86,145 (-234) | **832** of 86,145 (-54) | 0 of 86,145 (126) |
| `neo_mac_core` † | 1,090 | **1,210** of 36,484 (-594) | **1,191** of 36,484 (-414) | **1,035** of 36,484 (-234) | **26** of 36,484 (-54) | 0 of 36,484 (126) |
| `act_feeder` | 968 | **809** of 1,511 (-513) | **94** of 1,511 (-333) | **22** of 1,511 (-153) | 0 of 1,511 (27) | 0 of 1,511 (207) |
| `acc_bank` | 1,284 | **4,028** of 4,444 (-784) | **2,172** of 4,444 (-554) | **1,462** of 4,444 (-324) | **121** of 4,444 (-94) | 0 of 4,444 (136) |
| `neo_mac_core_v02` † | 1,529 | **1,656** of 5,787 (-1,029) | **77** of 5,787 (-799) | **45** of 5,787 (-569) | **32** of 5,787 (-339) | **32** of 5,787 (-109) |
| `wbuf_mem` | 1,092 | **428** of 1,329 (-641) | **378** of 1,329 (-461) | **378** of 1,329 (-281) | **374** of 1,329 (-101) | 0 of 1,329 (79) |
| `requant` | 980 | **4,399** of 7,016 (-480) | **3,072** of 7,016 (-298) | **2,079** of 7,016 (-118) | 0 of 7,016 (62) | 0 of 7,016 (242) |
| `core_seq` | 865 | **197** of 292 (-365) | **27** of 292 (-157) | 0 of 292 (23) | 0 of 292 (203) | 0 of 292 (383) |
| `vec_unit` | 1,142 | **20,992** of 25,331 (-642) | **14,123** of 25,331 (-412) | **4,738** of 25,331 (-182) | 0 of 25,331 (48) | 0 of 25,331 (278) |
| `neo_core` † | 1,396 | **5,352** of 8,335 (-896) | **2,617** of 8,335 (-666) | **2,434** of 8,335 (-436) | **2,048** of 8,335 (-206) | 0 of 8,335 (24) |
| `sram_bank` | 1,045 | **545** of 1,205 (-590) | **530** of 1,205 (-410) | **530** of 1,205 (-230) | **204** of 1,205 (-50) | 0 of 1,205 (130) |
| `mbist` | 812 | **611** of 644 (-343) | **590** of 644 (-163) | 0 of 644 (17) | 0 of 644 (197) | 0 of 644 (377) |
| `bank_bist_wrap` | 1,518 | **1,599** of 1,629 (-1,018) | **1,582** of 1,629 (-788) | **1,529** of 1,629 (-558) | **877** of 1,629 (-328) | **62** of 1,629 (-98) |
| `crc16_beat` | — | **16** of 16 (-299) | **16** of 16 (-49) | 0 of 16 (201) | 0 of 16 (451) | 0 of 16 (701) |
| `ecc39_enc` | — | 0 of 7 (176) | 0 of 7 (426) | 0 of 7 (676) | 0 of 7 (926) | 0 of 7 (1,176) |
| `ecc39_dec` | — | 0 of 34 (91) | 0 of 34 (341) | 0 of 34 (591) | 0 of 34 (841) | 0 of 34 (1,091) |
| `link_packer` | 740 | **3,753** of 4,890 (-240) | **38** of 4,890 (-10) | 0 of 4,890 (220) | 0 of 4,890 (442) | 0 of 4,890 (598) |
| `link_unpacker` | 494 | **10** of 575 (-268) | **1** of 575 (-138) | **1** of 575 (-8) | 0 of 575 (122) | 0 of 575 (252) |
| `tile_nic` | 1,275 | **20,068** of 23,436 (-775) | **15,909** of 23,436 (-545) | **444** of 23,436 (-315) | **54** of 23,436 (-85) | 0 of 23,436 (140) |
| `noc_router` | 849 | **5,513** of 21,737 (-349) | **5,419** of 21,737 (-119) | 0 of 21,737 (111) | 0 of 21,737 (293) | 0 of 21,737 (473) |
| `tile_dma` | 623 | **30** of 192 (-123) | 0 of 192 (107) | 0 of 192 (302) | 0 of 192 (482) | 0 of 192 (662) |
| `tile_telem` | 549 | **209** of 800 (-60) | 0 of 800 (70) | 0 of 800 (200) | 0 of 800 (330) | 0 of 800 (460) |
| `prog_mem` | — | **915** of 2,628 (-489) | **66** of 2,628 (-309) | **66** of 2,628 (-129) | 0 of 2,628 (51) | 0 of 2,628 (231) |
| `host_if` | — | **503** of 1,199 (-63) | 0 of 1,199 (117) | 0 of 1,199 (297) | 0 of 1,199 (477) | 0 of 1,199 (657) |
| `esm` | 538 | **6** of 64 (-38) | 0 of 64 (176) | 0 of 64 (356) | 0 of 64 (505) | 0 of 64 (635) |
| `neo_tile` † | 1,409 | **10,379** of 16,971 (-909) | **8,424** of 16,971 (-679) | **4,642** of 16,971 (-449) | **1,101** of 16,971 (-219) | 0 of 16,971 (11) |
| `c16_mac_pe` | 1,151 | **41** of 213 (-651) | **12** of 213 (-421) | **10** of 213 (-234) | **6** of 213 (-54) | 0 of 213 (126) |
| `c16_mac_pe_chk` | 1,160 | **57** of 186 (-660) | **29** of 186 (-445) | **18** of 186 (-265) | **7** of 186 (-85) | 0 of 186 (95) |
| `c16_systolic_array` | 1,125 | **4,608** of 13,441 (-625) | **4,608** of 13,441 (-414) | **4,608** of 13,441 (-234) | **416** of 13,441 (-54) | 0 of 13,441 (126) |
| `c16_neo_mac_core` | 1,090 | **574** of 6,618 (-594) | **559** of 6,618 (-414) | **513** of 6,618 (-234) | **26** of 6,618 (-54) | 0 of 6,618 (126) |
| `c16_act_feeder` | 960 | **501** of 915 (-508) | **89** of 915 (-328) | **9** of 915 (-148) | 0 of 915 (32) | 0 of 915 (212) |
| `c16_acc_bank` | 1,162 | **1,024** of 1,359 (-662) | **620** of 1,359 (-432) | **347** of 1,359 (-202) | 0 of 1,359 (28) | 0 of 1,359 (258) |
| `c16_neo_mac_core_v02` | 1,529 | **609** of 2,021 (-1,029) | **75** of 2,021 (-799) | **43** of 2,021 (-569) | **32** of 2,021 (-339) | **32** of 2,021 (-109) |
| `c16_wbuf_mem` | 1,039 | **158** of 492 (-605) | **134** of 492 (-425) | **133** of 492 (-245) | **122** of 492 (-65) | 0 of 492 (115) |
| `c16_requant` | 971 | **1,093** of 1,760 (-478) | **718** of 1,760 (-298) | **520** of 1,760 (-118) | 0 of 1,760 (62) | 0 of 1,760 (242) |
| `c16_core_seq` | 834 | **194** of 285 (-334) | **140** of 285 (-104) | 0 of 285 (85) | 0 of 285 (265) | 0 of 285 (445) |
| `c16_vec_unit` | 1,142 | **6,177** of 7,568 (-642) | **3,737** of 7,568 (-412) | **934** of 7,568 (-182) | 0 of 7,568 (48) | 0 of 7,568 (278) |
| `c16_neo_core` | 1,396 | **1,553** of 3,017 (-896) | **744** of 3,017 (-666) | **650** of 3,017 (-436) | **512** of 3,017 (-206) | 0 of 3,017 (24) |
| `top_link_packer` | 691 | **3,755** of 4,898 (-191) | 0 of 4,898 (39) | 0 of 4,898 (269) | 0 of 4,898 (474) | 0 of 4,898 (604) |
| `top_link_unpacker` | 499 | **10** of 577 (-269) | **1** of 577 (-139) | **1** of 577 (-9) | 0 of 577 (121) | 0 of 577 (251) |
| `top_tile_nic` | 1,253 | **20,132** of 23,860 (-753) | **15,555** of 23,860 (-523) | **2,509** of 23,860 (-299) | **152** of 23,860 (-119) | 0 of 23,860 (61) |
| `top_noc_router` | 822 | **6,124** of 21,777 (-322) | **5,426** of 21,777 (-92) | 0 of 21,777 (138) | 0 of 21,777 (339) | 0 of 21,777 (505) |
| `top_tile_dma` | 519 | **3** of 194 (-19) | 0 of 194 (185) | 0 of 194 (365) | 0 of 194 (529) | 0 of 194 (659) |
| `top_neo_tile` | 2,450 | **10,586** of 17,066 (-1,950) | **7,917** of 17,066 (-1,720) | **3,296** of 17,066 (-1,490) | **1,207** of 17,066 (-1,260) | **59** of 17,066 (-1,030) |
| `noc_axi_bridge` | 1,481 | **8,929** of 10,114 (-981) | **7,767** of 10,114 (-757) | **5,277** of 10,114 (-577) | **2,928** of 10,114 (-397) | **1,266** of 10,114 (-217) |
| `noc_axi_bridge_mem` | 1,319 | **7,241** of 7,883 (-884) | **5,332** of 7,883 (-704) | **3,628** of 7,883 (-524) | **1,318** of 7,883 (-344) | **1,168** of 7,883 (-164) |
| `top_ctrl` | 1,082 | **2,207** of 2,947 (-582) | **212** of 2,947 (-352) | **19** of 2,947 (-122) | 0 of 2,947 (108) | 0 of 2,947 (338) |
| `mesh_ctrl` | 716 | **768** of 2,800 (-292) | **321** of 2,800 (-112) | 0 of 2,800 (68) | 0 of 2,800 (248) | 0 of 2,800 (407) |
| `neo_top` | 892 | **291,022** of 329,770 (-392) | **479** of 329,770 (-162) | 0 of 329,770 (68) | 0 of 329,770 (298) | 0 of 329,770 (528) |

## The tile: area by unit

Total 16,909,815 µm² = 16.910 mm² (cells 4,046,569 µm², macros 12,863,246 µm²); 2,022,934 cells, 273,284 flops, 1152 PEs.

| Unit | Instances in the tile | Own cells (one) | Area µm² (one, with subtree) | Area µm² (all instances, own logic + own macros only) | Share of the tile |
| --- | --- | --- | --- | --- | --- |
| `sram_bank` | 1 | 3,830 | 11,401,833 | 11,401,833 | 67.4 % |
| `mac_pe` | 1,024 | 1,121 | 2,105 | 2,155,102 | 12.7 % |
| `vec_unit` | 1 | 236,323 | 721,208 | 721,208 | 4.3 % |
| `tile_nic` | 1 | 126,543 | 600,748 | 600,748 | 3.6 % |
| `mac_pe_chk` | 128 | 1,301 | 2,633 | 337,075 | 2.0 % |
| `acc_bank` | 1 | 16,297 | 334,648 | 334,648 | 2.0 % |
| `requant` | 2 | 74,678 | 163,718 | 327,436 | 1.9 % |
| `act_feeder` | 1 | 7,414 | 305,015 | 305,015 | 1.8 % |
| `wbuf_mem` | 1 | 5,229 | 275,295 | 275,295 | 1.6 % |
| `neo_mac_core` | 1 | 37,919 | 2,682,349 | 190,131 | 1.1 % |
| `noc_router` | 1 | 76,558 | 160,226 | 160,226 | 0.9 % |
| `link_packer` | 1 | 18,500 | 36,769 | 36,769 | 0.2 % |
| `prog_mem` | 1 | 8,902 | 22,126 | 22,126 | 0.1 % |
| `core_seq` | 2 | 2,992 | 5,106 | 10,212 | 0.1 % |
| `tile_telem` | 1 | 3,647 | 7,775 | 7,775 | 0.0 % |
| `host_if` | 1 | 2,112 | 5,608 | 5,608 | 0.0 % |
| `neo_mac_core_v02` | 1 | 960 | 3,327,119 | 5,107 | 0.0 % |
| `link_unpacker` | 1 | 2,879 | 3,363 | 3,363 | 0.0 % |
| `neo_core` | 1 | 1,654 | 4,664,132 | 2,862 | 0.0 % |
| `tile_dma` | 2 | 506 | 1,074 | 2,147 | 0.0 % |
| `neo_tile` | 1 | 1,448 | 16,909,815 | 1,560 | 0.0 % |
| `mbist` | 1 | 940 | 1,537 | 1,537 | 0.0 % |
| `bank_bist_wrap` | 1 | 623 | 11,404,779 | 1,409 | 0.0 % |
| `esm` | 1 | 334 | 582 | 582 | 0.0 % |
| `systolic_array` | 1 | 38 | 2,492,218 | 40 | 0.0 % |

| Macro | Instances in the tile | Word × depth | Bits (all) | Area µm² (all) | Ports used | Model factor |
| --- | --- | --- | --- | --- | --- | --- |
| `neo_mem_sp_w624_d32768_g16` | 1 | 624 × 32768 | 20,447,232 | 11,396,329 | 1W1R (registered read) | 1.0 |
| `neo_mem_rf_w1068_d512_g1_rb1` | 1 | 1068 × 512 | 546,816 | 304,770 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w1056_d512_g1_rb1` | 1 | 1056 × 512 | 540,672 | 301,345 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w512_d1024_g2_rb1` | 1 | 512 × 1024 | 524,288 | 292,214 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w936_d512_g2_rb1` | 1 | 936 × 512 | 479,232 | 267,101 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w33_d256_g1_rb1` | 33 | 33 × 256 | 278,784 | 155,381 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w512_d256_g2_rb1` | 1 | 512 × 256 | 131,072 | 73,053 | 1W1R (registered read, port B) | 1.0 |
| `neo_mem_rf_w1024_d64_g32_rb0` | 1 | 1024 × 64 | 65,536 | 54,790 | 1W + asynchronous read (port A) | 1.5 |
| `neo_mem_rf_w64_d512_g2_rb1` | 1 | 64 × 512 | 32,768 | 18,263 | 1W1R (registered read, port B) | 1.0 |

Endpoint slack histogram of `neo_tile` (its own registers and its children's inputs; bins of a tenth of the period; 16,971 endpoints, 10,379 violating):

| Slack | Endpoints |
| --- | --- |
| -950..-900 ps | 2 |
| -900..-850 ps | 74 |
| -850..-800 ps | 474 |
| -800..-750 ps | 371 |
| -750..-700 ps | 128 |
| -700..-650 ps | 360 |
| -650..-600 ps | 348 |
| -600..-550 ps | 217 |
| -550..-500 ps | 235 |
| -500..-450 ps | 401 |
| -450..-400 ps | 704 |
| -400..-350 ps | 28 |
| -350..-300 ps | 26 |
| -300..-250 ps | 2,889 |
| -250..-200 ps | 1,919 |
| -200..-150 ps | 1,300 |
| -150..-100 ps | 233 |
| -100..-50 ps | 418 |
| -50..0 ps | 252 |
| 0..50 ps | 1,287 |
| 50..100 ps | 419 |
| 100..150 ps | 36 |
| 150..200 ps | 4,354 |
| 200..250 ps | 43 |
| 250..300 ps | 207 |
| 300..350 ps | 8 |
| 350..400 ps | 4 |
| 400..450 ps | 13 |
| 450..500 ps | 6 |
| 500..550 ps | 4 |
| 550..600 ps | 5 |
| 600..650 ps | 7 |
| 650..700 ps | 8 |
| 700..750 ps | 4 |
| 750..800 ps | 4 |
| 800..850 ps | 2 |
| 950..1000 ps | 1 |
| 1000..1050 ps | 7 |
| 1050..1100 ps | 17 |
| 1100..1150 ps | 17 |
| 1150..1200 ps | 17 |
| 1200..1250 ps | 18 |
| 1250..1300 ps | 27 |
| 1300..1350 ps | 12 |
| 1350..1400 ps | 8 |
| 1400..1450 ps | 4 |
| 1450..1500 ps | 1 |
| 1500..1550 ps | 1 |
| 1550..1600 ps | 3 |
| 1700..1750 ps | 5 |
| 1750..1800 ps | 43 |

Endpoint slack histogram of `neo_core` (its own registers and its children's inputs; bins of a tenth of the period; 8,335 endpoints, 5,352 violating):

| Slack | Endpoints |
| --- | --- |
| -900..-850 ps | 63 |
| -850..-800 ps | 1,983 |
| -800..-750 ps | 2 |
| -650..-600 ps | 89 |
| -600..-550 ps | 230 |
| -550..-500 ps | 61 |
| -500..-450 ps | 7 |
| -450..-400 ps | 7 |
| -400..-350 ps | 13 |
| -350..-300 ps | 14 |
| -300..-250 ps | 71 |
| -250..-200 ps | 349 |
| -200..-150 ps | 554 |
| -150..-100 ps | 194 |
| -100..-50 ps | 1,391 |
| -50..0 ps | 324 |
| 0..50 ps | 115 |
| 50..100 ps | 53 |
| 100..150 ps | 13 |
| 150..200 ps | 4 |
| 200..250 ps | 1,032 |
| 250..300 ps | 1,362 |
| 300..350 ps | 41 |
| 350..400 ps | 6 |
| 400..450 ps | 25 |
| 450..500 ps | 11 |
| 500..550 ps | 4 |
| 550..600 ps | 4 |
| 600..650 ps | 5 |
| 650..700 ps | 9 |
| 700..750 ps | 6 |
| 750..800 ps | 3 |
| 800..850 ps | 3 |
| 850..900 ps | 1 |
| 950..1000 ps | 1 |
| 1000..1050 ps | 6 |
| 1050..1100 ps | 21 |
| 1100..1150 ps | 22 |
| 1150..1200 ps | 30 |
| 1200..1250 ps | 29 |
| 1250..1300 ps | 34 |
| 1300..1350 ps | 20 |
| 1350..1400 ps | 17 |
| 1400..1450 ps | 5 |
| 1450..1500 ps | 1 |
| 1500..1550 ps | 1 |
| 1550..1600 ps | 3 |
| 1700..1750 ps | 6 |
| 1750..1800 ps | 90 |

