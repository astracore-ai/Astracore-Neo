# Synthesis report — asap7 SS, 500 ps clock (2026-10-11 21:44:06 UTC)

Tools: Yosys 0.69+274 (git sha1 7513f8df8, Release, Clang /usr/bin/clang++ 21.1.8) (slang front end); OpenSTA 3.1.0. Libraries: asap7sc7p5t_AO_RVT_SS_nldm_211120.lib, asap7sc7p5t_INVBUF_RVT_SS_nldm_220122.lib, asap7sc7p5t_OA_RVT_SS_nldm_211120.lib, asap7sc7p5t_SIMPLE_RVT_SS_nldm_211120.lib, asap7sc7p5t_SEQ_RVT_SS_nldm_220123.lib. Run time 20.0 min.

Macro model for the black-boxed memory leaves (`neo_mem_sp` / `neo_mem_rf`): FakeRAM2.0 7 nm (ORFS asap7/fakeram.cfg): 0.042 um2/bit, 218 ps access, 50 ps setup/hold; area factors {'neo_mem_sp': 1.0, 'neo_mem_rf': 1.5, 'neo_mem_rf_rb': 2.0} (register-file style reads cost bit-cell area). ASAP7 is an academic 7 nm predictive PDK: no foundry data; the figures are a proxy for the N5A target. Every figure here is a proxy for the N5A target: the paths and the shares are the design's, the absolute picoseconds and square microns are the open library's. A unit is one module synthesized at its silicon parameters with its children black-boxed; 'with subtree' sums the children in (instances × unit).

## Gates

| Unit | Synthesized | Latches | Multiple drivers | Hierarchy check | PE count | DV_HOOKS = 0 |
| --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` | ✓ | none | none | ✓ | — | — |
| `mac_pe_chk` | ✓ | none | none | ✓ | — | — |
| `systolic_array` | ✓ | none | none | ✓ | 1056 = 1056 ✓ | — |
| `neo_mac_core` | ✓ | none | none | ✓ | — | — |
| `act_feeder` | ✓ | none | none | ✓ | — | — |
| `acc_bank` | ✓ | none | none | ✓ | — | — |
| `neo_mac_core_v02` | ✓ | none | none | ✓ | — | — |
| `wbuf_mem` | ✓ | none | none | ✓ | — | ✓ (4 in, 0 out) |
| `requant` | ✓ | none | none | ✓ | — | — |
| `core_seq` | ✓ | none | none | ✓ | — | — |
| `neo_core` | ✓ | none | none | ✓ | 1056 = 1056 ✓ | ✓ (4 in, 0 out) |
| `sram_bank` | ✓ | none | none | ✓ | — | ✓ (6 in, 1 out) |
| `mbist` | ✓ | none | none | ✓ | — | — |
| `bank_bist_wrap` | ✓ | none | none | ✓ | — | ✓ (6 in, 1 out) |
| `crc16_beat` | ✓ | none | none | ✓ | — | — |
| `ecc39_enc` | ✓ | none | none | ✓ | — | — |
| `ecc39_dec` | ✓ | none | none | ✓ | — | — |
| `link_packer` | ✓ | none | none | ✓ | — | — |
| `link_unpacker` | ✓ | none | none | ✓ | — | — |
| `tile_nic` | ✓ | none | none | ✓ | — | ✓ (1 in, 0 out) |
| `noc_router` | ✓ | none | none | ✓ | — | ✓ (4 in, 0 out) |
| `tile_dma` | ✓ | none | none | ✓ | — | ✓ (2 in, 0 out) |
| `prog_mem` | ✓ | none | none | ✓ | — | ✓ (3 in, 0 out) |
| `host_if` | ✓ | none | none | ✓ | — | ✓ (3 in, 0 out) |
| `esm` | ✓ | none | none | ✓ | — | — |
| `neo_tile` | ✓ | none | none | ✓ | 1056 = 1056 ✓ | ✓ (7 in, 3 out) — tree ✓ |
| `c16_mac_pe` | ✓ | none | none | ✓ | — | — |
| `c16_mac_pe_chk` | ✓ | none | none | ✓ | — | — |
| `c16_systolic_array` | ✓ | none | none | ✓ | 144 = 144 ✓ | — |
| `c16_neo_mac_core` | ✓ | none | none | ✓ | — | — |
| `c16_act_feeder` | ✓ | none | none | ✓ | — | — |
| `c16_acc_bank` | ✓ | none | none | ✓ | — | — |
| `c16_neo_mac_core_v02` | ✓ | none | none | ✓ | — | — |
| `c16_wbuf_mem` | ✓ | none | none | ✓ | — | ✓ (4 in, 0 out) |
| `c16_requant` | ✓ | none | none | ✓ | — | — |
| `c16_core_seq` | ✓ | none | none | ✓ | — | — |
| `c16_neo_core` | ✓ | none | none | ✓ | 144 = 144 ✓ | ✓ (4 in, 0 out) |

## Units

Own cells = the unit's library cells; cells, flops and area with its subtree (one instance of the unit); macro area from the model. Timing is the unit's own logic with its child units as extracted timing models (a child's inside is the child's row): WNS against the 500 ps clock; minimum period from OpenSTA's `report_clock_min_period` (register-to-register paths of the clock, a child's clock-to-output and setup arcs included); violating endpoints of the unit's timing endpoints; the Yosys time of the unit alone.

| Unit | Own cells | Cells | Flops | Cell area µm² | Macro area µm² | Total area µm² | WNS ps | Min period ps (Fmax) | Violating endpoints | Yosys s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` | 1,124 | 1,124 | 107 | 138 | 0 | 138 | -531 | 1,031 (970 MHz) | 41 of 157 | 2 |
| `mac_pe_chk` | 1,576 | 1,576 | 125 | 187 | 0 | 187 | -914 | 1,093 (915 MHz) | 47 of 181 | 2 |
| `systolic_array` | 35 | 1,201,443 | 113,568 | 147,705 | 0 | 147,705 | -911 | 1,411 (708 MHz) | 9,503 of 54,049 | 4 |
| `neo_mac_core` | 55,730 | 1,257,173 | 138,559 | 158,853 | 0 | 158,853 | -1,087 | 1,505 (664 MHz) | 259 of 26,642 | 12 |
| `act_feeder` | 8,770 | 8,770 | 88 | 917 | 33,068 | 33,985 | -2,645 | 502 (1,994 MHz) | 335 of 893 | 11 |
| `acc_bank` | 15,060 | 15,060 | 2 | 1,559 | 45,468 | 47,028 | -1,284 | 1,732 (577 MHz) | 1,067 of 2,147 | 15 |
| `neo_mac_core_v02` | 1,920 | 1,282,923 | 139,609 | 161,735 | 78,536 | 240,271 | -3,368 | 1,425 (702 MHz) | 2,375 of 5,417 | 1 |
| `wbuf_mem` | 4,790 | 4,790 | 2 | 594 | 22,670 | 23,263 | -523 | — | 333 of 958 | 13 |
| `requant` | 192,872 | 192,872 | 3,235 | 20,461 | 0 | 20,461 | -2,539 | 3,039 (329 MHz) | 1,782 of 3,493 | 278 |
| `core_seq` | 2,942 | 2,942 | 181 | 349 | 0 | 349 | -1,562 | 1,095 (913 MHz) | 176 of 275 | 5 |
| `neo_core` | 466 | 1,679,807 | 146,445 | 204,016 | 101,206 | 305,222 | -3,368 | 3,430 (292 MHz) | 4,237 of 6,382 | 3 |
| `sram_bank` | 3,742 | 3,742 | 3 | 533 | 859,765 | 860,298 | -341 | 767 (1,304 MHz) | 546 of 1,205 | 14 |
| `mbist` | 986 | 986 | 44 | 111 | 0 | 111 | -530 | 1,030 (971 MHz) | 586 of 611 | 12 |
| `bank_bist_wrap` | 1,245 | 5,973 | 47 | 757 | 859,765 | 860,523 | -845 | 1,345 (743 MHz) | 1,584 of 1,629 | 1 |
| `crc16_beat` | 3,275 | 3,275 | 0 | 501 | 0 | 501 | -4,223 | — | 16 of 16 | 11 |
| `ecc39_enc` | 66 | 66 | 0 | 11 | 0 | 11 | 202 | — | 0 of 7 | 1 |
| `ecc39_dec` | 163 | 163 | 0 | 24 | 0 | 24 | 105 | — | 0 of 34 | 1 |
| `link_packer` | 11,085 | 11,085 | 1,077 | 1,428 | 0 | 1,428 | -681 | — | 2,156 of 2,156 | 20 |
| `link_unpacker` | 2,906 | 2,906 | 2 | 330 | 0 | 330 | -750 | 756 (1,322 MHz) | 482 of 569 | 4 |
| `tile_nic` | 477,805 | 477,805 | 71,999 | 62,918 | 34,392 | 97,310 | -7,162 | 7,662 (131 MHz) | 74,618 of 76,350 | 783 |
| `noc_router` | 108,904 | 108,904 | 16,196 | 15,170 | 0 | 15,170 | -2,071 | 2,571 (389 MHz) | 10,721 of 21,592 | 510 |
| `tile_dma` | 563 | 563 | 104 | 80 | 0 | 80 | -59 | 558 (1,790 MHz) | 12 of 189 | 1 |
| `prog_mem` | 13,939 | 13,939 | 2,498 | 1,933 | 0 | 1,933 | -512 | — | 432 of 2,564 | 10 |
| `host_if` | 3,051 | 3,051 | 553 | 426 | 0 | 426 | -27 | — | 405 of 1,073 | 4 |
| `esm` | 305 | 305 | 44 | 43 | 0 | 43 | -40 | 540 (1,852 MHz) | 15 of 64 | 1 |
| `neo_tile` | 118 | 2,305,019 | 239,071 | 287,198 | 995,363 | 1,282,561 | -5,944 | 6,444 (155 MHz) | 5,889 of 16,124 | 2 |
| `c16_mac_pe` | 1,124 | 1,124 | 107 | 138 | 0 | 138 | -531 | 1,031 (970 MHz) | 41 of 157 | 2 |
| `c16_mac_pe_chk` | 1,523 | 1,523 | 122 | 181 | 0 | 181 | -751 | 1,251 (799 MHz) | 44 of 177 | 2 |
| `c16_systolic_array` | 5 | 168,245 | 15,648 | 20,608 | 0 | 20,608 | -727 | 1,227 (815 MHz) | 1,280 of 7,425 | 1 |
| `c16_neo_mac_core` | 7,676 | 175,921 | 18,791 | 22,081 | 0 | 22,081 | -754 | 1,168 (856 MHz) | 130 of 3,673 | 2 |
| `c16_act_feeder` | 7,368 | 7,368 | 82 | 797 | 2,067 | 2,863 | -2,321 | 440 (2,273 MHz) | 203 of 494 | 10 |
| `c16_acc_bank` | 4,140 | 4,140 | 2 | 430 | 1,550 | 1,980 | -915 | 1,329 (752 MHz) | 302 of 602 | 5 |
| `c16_neo_mac_core_v02` | 560 | 187,989 | 19,155 | 23,434 | 3,617 | 27,051 | -3,044 | 1,378 (726 MHz) | 868 of 1,832 | 1 |
| `c16_wbuf_mem` | 1,434 | 1,434 | 2 | 179 | 3,778 | 3,957 | -476 | — | 102 of 293 | 4 |
| `c16_requant` | 48,224 | 48,224 | 811 | 5,119 | 0 | 5,119 | -2,287 | 2,787 (359 MHz) | 442 of 877 | 64 |
| `c16_core_seq` | 2,819 | 2,819 | 178 | 336 | 0 | 336 | -1,537 | 1,151 (869 MHz) | 173 of 268 | 6 |
| `c16_neo_core` | 238 | 291,747 | 21,137 | 34,554 | 7,395 | 41,949 | -3,044 | 3,164 (316 MHz) | 1,397 of 2,250 | 1 |

## Worst paths

| Unit | Start | End | Arrival ps | Slack ps |
| --- | --- | --- | --- | --- |
| `mac_pe` | `w_q[1]_reg` | `prod_q[14]_reg` | 956 | -531 |
| `mac_pe` | `w_q[1]_reg` | `prod_q[12]_reg` | 954 | -528 |
| `mac_pe_chk` | `x_in[7]` | `prod_q[20]_reg` | 1,340 | -914 |
| `mac_pe_chk` | `x_in[7]` | `prod_q[21]_reg` | 1,296 | -873 |
| `systolic_array` | `g_row[0].g_col[31].g_data.u_pe` | `g_row[0].g_col[32].g_check.u_pe` | 98 | -912 |
| `systolic_array` | `g_row[10].g_col[31].g_data.u_pe` | `g_row[10].g_col[32].g_check.u_pe` | 98 | -912 |
| `neo_mac_core` | `y[962]_reg` | `abft_err` | 1,447 | -1,087 |
| `neo_mac_core` | `y[965]_reg` | `abft_err_sticky_reg` | 1,428 | -1,005 |
| `act_feeder` | `cfg_s[2]` | `x_vec[0]` | 3,005 | -2,645 |
| `act_feeder` | `cfg_s[2]` | `x_vec[100]` | 3,005 | -2,645 |
| `acc_bank` | `u_mem` | `abft_err` | 1,644 | -1,284 |
| `acc_bank` | `u_mem` | `abft_err_sticky_reg` | 1,680 | -1,232 |
| `neo_mac_core_v02` | `cfg_s[2]` | `u_core` | 3,011 | -3,368 |
| `neo_mac_core_v02` | `cfg_s[2]` | `u_core` | 3,015 | -3,331 |
| `wbuf_mem` | `raddr[9]` | `rdata[160]` | 883 | -523 |
| `wbuf_mem` | `raddr[9]` | `rdata[172]` | 883 | -523 |
| `requant` | `sh1[113]_reg` | `out_q[176]_reg` | 2,966 | -2,539 |
| `requant` | `sh1[113]_reg` | `out_q[177]_reg` | 2,966 | -2,539 |
| `core_seq` | `cfg_m[3]` | `rd_idx[2]_reg` | 1,982 | -1,562 |
| `core_seq` | `cfg_m[3]` | `rd_idx[3]_reg` | 1,982 | -1,562 |
| `neo_core` | `cfg_s[2]` | `u_dp` | 100 | -3,368 |
| `neo_core` | `cfg_s[3]` | `u_dp` | 100 | -3,364 |
| `sram_bank` | `u_mem` | `rdata[46]` | 701 | -341 |
| `sram_bank` | `u_mem` | `rdata[50]` | 701 | -341 |
| `mbist` | `elem[0]_reg` | `state[1]_reg` | 979 | -530 |
| `mbist` | `elem[0]_reg` | `state[0]_reg` | 948 | -524 |
| `bank_bist_wrap` | `u_bank` | `u_bist` | 683 | -845 |
| `bank_bist_wrap` | `u_bank` | `u_bist` | 684 | -844 |
| `crc16_beat` | `words[21]` | `crc_out[6]` | 4,723 | -4,223 |
| `crc16_beat` | `words[21]` | `crc_out[7]` | 4,718 | -4,218 |
| `ecc39_enc` | `d[24]` | `op` | 298 | 202 |
| `ecc39_enc` | `d[20]` | `p[1]` | 245 | 255 |
| `ecc39_dec` | `d[2]` | `d_out[29]` | 395 | 105 |
| `ecc39_dec` | `d[2]` | `d_out[30]` | 395 | 105 |
| `link_packer` | `core_flit[520]` | `link_flit[100]` | 1,041 | -681 |
| `link_packer` | `core_flit[520]` | `link_flit[101]` | 1,041 | -681 |
| `link_unpacker` | `base[4]_reg` | `core_flit[564]` | 1,110 | -750 |
| `link_unpacker` | `base[4]_reg` | `core_flit[522]` | 893 | -533 |
| `tile_nic` | `drain_int8_reg` | `tx_crc[5]_reg` | 7,581 | -7,162 |
| `tile_nic` | `drain_int8_reg` | `tx_crc[7]_reg` | 7,590 | -7,142 |
| `noc_router` | `rr[12]_reg` | `count[5]_reg` | 2,496 | -2,071 |
| `noc_router` | `rr[12]_reg` | `count[1]_reg` | 2,452 | -2,026 |
| `tile_dma` | `tiles_freed[0]_reg` | `tiles_freed[14]_reg` | 485 | -58 |
| `tile_dma` | `tiles_freed[0]_reg` | `state[0]_reg` | 455 | -32 |
| `prog_mem` | `raddr[0]` | `rdata[55]` | 872 | -512 |
| `prog_mem` | `raddr[0]` | `rdata[56]` | 872 | -512 |
| `host_if` | `h_addr[4]` | `cfg[147]_reg` | 453 | -27 |
| `host_if` | `h_addr[4]` | `cfg[148]_reg` | 453 | -27 |
| `esm` | `wd_cnt[0]_reg` | `wd_cnt[20]_reg` | 458 | -40 |
| `esm` | `wd_cnt[0]_reg` | `wd_cnt[22]_reg` | 458 | -40 |
| `neo_tile` | `u_bank` | `u_nic` | 702 | -5,944 |
| `neo_tile` | `u_bank` | `u_nic` | 696 | -5,928 |
| `c16_mac_pe` | `w_q[1]_reg` | `prod_q[14]_reg` | 956 | -531 |
| `c16_mac_pe` | `w_q[1]_reg` | `prod_q[12]_reg` | 954 | -528 |
| `c16_mac_pe_chk` | `w_q[3]_reg` | `prod_q[20]_reg` | 1,199 | -751 |
| `c16_mac_pe_chk` | `w_q[3]_reg` | `prod_q[17]_reg` | 1,175 | -749 |
| `c16_systolic_array` | `g_row[0].g_col[7].g_data.u_pe` | `g_row[0].g_col[8].g_check.u_pe` | 98 | -727 |
| `c16_systolic_array` | `g_row[10].g_col[7].g_data.u_pe` | `g_row[10].g_col[8].g_check.u_pe` | 98 | -727 |
| `c16_neo_mac_core` | `y[206]_reg` | `abft_err` | 1,114 | -754 |
| `c16_neo_mac_core` | `y[206]_reg` | `abft_err_sticky_reg` | 1,115 | -668 |
| `c16_act_feeder` | `cfg_s[0]` | `x_vec[0]` | 2,681 | -2,321 |
| `c16_act_feeder` | `cfg_s[0]` | `x_vec[100]` | 2,681 | -2,321 |
| `c16_acc_bank` | `u_mem` | `abft_err` | 1,275 | -915 |
| `c16_acc_bank` | `wr_valid` | `u_mem` | 1,286 | -876 |
| `c16_neo_mac_core_v02` | `cfg_s[0]` | `u_core` | 2,687 | -3,044 |
| `c16_neo_mac_core_v02` | `cfg_s[0]` | `u_core` | 2,682 | -3,009 |
| `c16_wbuf_mem` | `raddr[8]` | `rdata[38]` | 836 | -476 |
| `c16_wbuf_mem` | `raddr[8]` | `rdata[39]` | 836 | -476 |
| `c16_requant` | `sh1[36]_reg` | `out_q[56]_reg` | 2,711 | -2,287 |
| `c16_requant` | `sh1[36]_reg` | `out_q[57]_reg` | 2,711 | -2,286 |
| `c16_core_seq` | `cfg_m[3]` | `rd_idx[4]_reg` | 1,958 | -1,537 |
| `c16_core_seq` | `cfg_m[3]` | `state_dbg[0]_reg` | 1,953 | -1,528 |
| `c16_neo_core` | `cfg_s[0]` | `u_dp` | 100 | -3,044 |
| `c16_neo_core` | `cfg_s[3]` | `u_dp` | 100 | -3,031 |

## The tile: area by unit

Total 1,282,561 µm² = 1.283 mm² (cells 287,198 µm², macros 995,363 µm²); 2,305,019 cells, 239,071 flops, 1056 PEs.

| Unit | Instances in the tile | Own cells (one) | Area µm² (one, with subtree) | Area µm² (all instances, own logic + own macros only) | Share of the tile |
| --- | --- | --- | --- | --- | --- |
| `sram_bank` | 1 | 3,742 | 860,298 | 860,298 | 67.1 % |
| `mac_pe` | 1,024 | 1,124 | 138 | 141,715 | 11.0 % |
| `tile_nic` | 1 | 477,805 | 97,310 | 97,310 | 7.6 % |
| `acc_bank` | 1 | 15,060 | 47,028 | 47,028 | 3.7 % |
| `requant` | 2 | 192,872 | 20,461 | 40,922 | 3.2 % |
| `act_feeder` | 1 | 8,770 | 33,985 | 33,985 | 2.6 % |
| `wbuf_mem` | 1 | 4,790 | 23,263 | 23,263 | 1.8 % |
| `noc_router` | 1 | 108,904 | 15,170 | 15,170 | 1.2 % |
| `neo_mac_core` | 1 | 55,730 | 158,853 | 11,147 | 0.9 % |
| `mac_pe_chk` | 32 | 1,576 | 187 | 5,988 | 0.5 % |
| `prog_mem` | 1 | 13,939 | 1,933 | 1,933 | 0.2 % |
| `link_packer` | 1 | 11,085 | 1,428 | 1,428 | 0.1 % |
| `core_seq` | 2 | 2,942 | 349 | 698 | 0.1 % |
| `host_if` | 1 | 3,051 | 426 | 426 | 0.0 % |
| `neo_mac_core_v02` | 1 | 1,920 | 240,271 | 406 | 0.0 % |
| `link_unpacker` | 1 | 2,906 | 330 | 330 | 0.0 % |
| `tile_dma` | 2 | 563 | 80 | 159 | 0.0 % |
| `bank_bist_wrap` | 1 | 1,245 | 860,523 | 114 | 0.0 % |
| `mbist` | 1 | 986 | 111 | 111 | 0.0 % |
| `neo_core` | 1 | 466 | 305,222 | 67 | 0.0 % |
| `esm` | 1 | 305 | 43 | 43 | 0.0 % |
| `neo_tile` | 1 | 118 | 1,282,561 | 17 | 0.0 % |
| `systolic_array` | 1 | 35 | 147,705 | 3 | 0.0 % |

| Macro | Instances in the tile | Word × depth | Bits (all) | Area µm² (all) | Model factor |
| --- | --- | --- | --- | --- | --- |
| `neo_mem_sp_w624_d32768_g16` | 1 | 624 × 32768 | 20,447,232 | 859,765 | 1.0 |
| `neo_mem_rf_w1065_d512_g1_rb0` | 1 | 1065 × 512 | 545,280 | 34,392 | 1.5 |
| `neo_mem_rf_w1056_d512_g1_rb1` | 1 | 1056 × 512 | 540,672 | 45,468 | 2.0 |
| `neo_mem_rf_w512_d1024_g2_rb0` | 1 | 512 × 1024 | 524,288 | 33,068 | 1.5 |
| `neo_mem_rf_w702_d512_g2_rb0` | 1 | 702 × 512 | 359,424 | 22,670 | 1.5 |

Endpoint slack histogram of `neo_tile` (its own registers and its children's inputs; bins of a tenth of the period; 16,124 endpoints, 5,889 violating):

| Slack | Endpoints |
| --- | --- |
| -5950..-5900 ps | 34 |
| -5900..-5850 ps | 115 |
| -5850..-5800 ps | 176 |
| -5800..-5750 ps | 136 |
| -5750..-5700 ps | 53 |
| -5700..-5650 ps | 13 |
| -5650..-5600 ps | 10 |
| -5600..-5550 ps | 8 |
| -5550..-5500 ps | 6 |
| -5500..-5450 ps | 5 |
| -5450..-5400 ps | 11 |
| -5400..-5350 ps | 7 |
| -5350..-5300 ps | 4 |
| -5300..-5250 ps | 3 |
| -5250..-5200 ps | 4 |
| -5200..-5150 ps | 5 |
| -5150..-5100 ps | 9 |
| -5100..-5050 ps | 10 |
| -5050..-5000 ps | 8 |
| -5000..-4950 ps | 3 |
| -4950..-4900 ps | 1 |
| -4900..-4850 ps | 1 |
| -4850..-4800 ps | 5 |
| -4800..-4750 ps | 5 |
| -4750..-4700 ps | 9 |
| -4700..-4650 ps | 5 |
| -4650..-4600 ps | 4 |
| -4600..-4550 ps | 4 |
| -4550..-4500 ps | 5 |
| -4500..-4450 ps | 6 |
| -4450..-4400 ps | 2 |
| -4400..-4350 ps | 4 |
| -4350..-4300 ps | 4 |
| -4300..-4250 ps | 4 |
| -4250..-4200 ps | 10 |
| -4200..-4150 ps | 5 |
| -4150..-4100 ps | 8 |
| -4100..-4050 ps | 6 |
| -4050..-4000 ps | 8 |
| -4000..-3950 ps | 1 |
| -3950..-3900 ps | 2 |
| -3900..-3850 ps | 8 |
| -3850..-3800 ps | 7 |
| -3800..-3750 ps | 7 |
| -3750..-3700 ps | 3 |
| -3700..-3650 ps | 3 |
| -3650..-3600 ps | 4 |
| -3600..-3550 ps | 5 |
| -3550..-3500 ps | 9 |
| -3500..-3450 ps | 13 |
| -3450..-3400 ps | 4 |
| -3400..-3350 ps | 5 |
| -3350..-3300 ps | 6 |
| -3300..-3250 ps | 8 |
| -3250..-3200 ps | 7 |
| -3200..-3150 ps | 9 |
| -3150..-3100 ps | 14 |
| -3100..-3050 ps | 1,023 |
| -3050..-3000 ps | 78 |
| -3000..-2950 ps | 16 |
| -2950..-2900 ps | 16 |
| -2900..-2850 ps | 14 |
| -2850..-2800 ps | 19 |
| -2800..-2750 ps | 16 |
| -2750..-2700 ps | 26 |
| -2700..-2650 ps | 25 |
| -2650..-2600 ps | 28 |
| -2600..-2550 ps | 24 |
| -2550..-2500 ps | 26 |
| -2500..-2450 ps | 19 |
| -2450..-2400 ps | 18 |
| -2400..-2350 ps | 21 |
| -2350..-2300 ps | 35 |
| -2300..-2250 ps | 50 |
| -2250..-2200 ps | 57 |
| -2200..-2150 ps | 86 |
| -2150..-2100 ps | 81 |
| -2100..-2050 ps | 68 |
| -2050..-2000 ps | 27 |
| -2000..-1950 ps | 9 |
| -1950..-1900 ps | 4 |
| -1900..-1850 ps | 2 |
| -1850..-1800 ps | 2 |
| -1800..-1750 ps | 7 |
| -1750..-1700 ps | 6 |
| -1700..-1650 ps | 9 |
| -1650..-1600 ps | 10 |
| -1600..-1550 ps | 33 |
| -1550..-1500 ps | 116 |
| -1500..-1450 ps | 202 |
| -1450..-1400 ps | 121 |
| -1400..-1350 ps | 67 |
| -1350..-1300 ps | 22 |
| -1300..-1250 ps | 14 |
| -1250..-1200 ps | 24 |
| -1200..-1150 ps | 37 |
| -1150..-1100 ps | 99 |
| -1100..-1050 ps | 125 |
| -1050..-1000 ps | 140 |
| -1000..-950 ps | 100 |
| -950..-900 ps | 89 |
| -900..-850 ps | 50 |
| -850..-800 ps | 25 |
| -800..-750 ps | 124 |
| -750..-700 ps | 114 |
| -700..-650 ps | 117 |
| -650..-600 ps | 108 |
| -600..-550 ps | 175 |
| -550..-500 ps | 254 |
| -500..-450 ps | 304 |
| -450..-400 ps | 146 |
| -400..-350 ps | 127 |
| -350..-300 ps | 97 |
| -300..-250 ps | 63 |
| -250..-200 ps | 59 |
| -200..-150 ps | 106 |
| -150..-100 ps | 68 |
| -100..-50 ps | 43 |
| -50..0 ps | 67 |
| 0..50 ps | 40 |
| 50..100 ps | 20 |
| 100..150 ps | 1,099 |
| 150..200 ps | 8,695 |
| 200..250 ps | 65 |
| 250..300 ps | 305 |
| 300..350 ps | 11 |

Endpoint slack histogram of `neo_core` (its own registers and its children's inputs; bins of a tenth of the period; 6,382 endpoints, 4,237 violating):

| Slack | Endpoints |
| --- | --- |
| -3400..-3350 ps | 2 |
| -3350..-3300 ps | 2 |
| -3300..-3250 ps | 2 |
| -3200..-3150 ps | 3 |
| -3150..-3100 ps | 1 |
| -2950..-2900 ps | 1 |
| -2850..-2800 ps | 3 |
| -2800..-2750 ps | 3 |
| -2750..-2700 ps | 3 |
| -2700..-2650 ps | 4 |
| -2650..-2600 ps | 2 |
| -2600..-2550 ps | 1 |
| -2550..-2500 ps | 4 |
| -2500..-2450 ps | 6 |
| -2450..-2400 ps | 3 |
| -2400..-2350 ps | 2 |
| -2350..-2300 ps | 5 |
| -2300..-2250 ps | 6 |
| -2250..-2200 ps | 15 |
| -2200..-2150 ps | 5 |
| -2150..-2100 ps | 6 |
| -2100..-2050 ps | 7 |
| -2050..-2000 ps | 4 |
| -2000..-1950 ps | 3 |
| -1950..-1900 ps | 2 |
| -1900..-1850 ps | 2 |
| -1850..-1800 ps | 2 |
| -1800..-1750 ps | 6 |
| -1750..-1700 ps | 4 |
| -1700..-1650 ps | 3 |
| -1650..-1600 ps | 2 |
| -1600..-1550 ps | 4 |
| -1550..-1500 ps | 2 |
| -1450..-1400 ps | 9 |
| -1400..-1350 ps | 2 |
| -1350..-1300 ps | 137 |
| -1300..-1250 ps | 434 |
| -1250..-1200 ps | 714 |
| -1200..-1150 ps | 483 |
| -1150..-1100 ps | 260 |
| -1100..-1050 ps | 82 |
| -1050..-1000 ps | 8 |
| -1000..-950 ps | 2 |
| -950..-900 ps | 3 |
| -850..-800 ps | 19 |
| -800..-750 ps | 222 |
| -750..-700 ps | 52 |
| -700..-650 ps | 7 |
| -650..-600 ps | 16 |
| -600..-550 ps | 12 |
| -550..-500 ps | 17 |
| -500..-450 ps | 8 |
| -450..-400 ps | 23 |
| -400..-350 ps | 165 |
| -350..-300 ps | 259 |
| -300..-250 ps | 188 |
| -250..-200 ps | 127 |
| -200..-150 ps | 155 |
| -150..-100 ps | 275 |
| -100..-50 ps | 211 |
| -50..0 ps | 227 |
| 0..50 ps | 68 |
| 50..100 ps | 48 |
| 100..150 ps | 1,120 |
| 150..200 ps | 10 |
| 200..250 ps | 299 |
| 250..300 ps | 578 |
| 300..350 ps | 22 |

