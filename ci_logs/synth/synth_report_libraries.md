# Synthesis report — asap7 SS and nangate45 TT side by side (2026-10-13 19:52:20 UTC)

Tools: Yosys 0.69+274 (git sha1 7513f8df8, Release, Clang /usr/bin/clang++ 21.1.8) (slang front end); OpenSTA 3.1.0. Libraries: asap7 SS: asap7sc7p5t_AO_RVT_SS_nldm_211120.lib, asap7sc7p5t_INVBUF_RVT_SS_nldm_220122.lib, asap7sc7p5t_OA_RVT_SS_nldm_211120.lib, asap7sc7p5t_SIMPLE_RVT_SS_nldm_211120.lib, asap7sc7p5t_SEQ_RVT_SS_nldm_220123.lib; nangate45 TT: NangateOpenCellLibrary_typical.lib. Run times asap7 30.3 min, nangate45 27.2 min.

Macro model, asap7: FakeRAM2.0 7 nm (ORFS asap7/fakeram.cfg): 0.042 um2/bit, 218 ps access, 50 ps setup/hold. ASAP7 is an academic 7 nm predictive PDK: no foundry data; the figures are a proxy for the N5A target.

Macro model, nangate45: ORFS FakeRAM45 macros fakeram45_2048x39 (0.569 um2/bit, 0.435 ns access), fakeram45_512x64 (0.528 um2/bit, 0.305 ns access): 0.557 um2/bit (the sum of their areas over the sum of their bits), 0.435 ns access (the slowest), 0.05 ns setup/hold. Nangate45 (FreePDK45) is a 45 nm open library: a second data point, not a 2 GHz node.

**asap7** — **Period sweep (500, 750, 1000, 1250, 1500 ps):** every unit of the tile outside the PE-limited ones (mac_pe, mac_pe_chk, neo_core, neo_mac_core, neo_mac_core_v02, neo_tile, systolic_array) is clean from **1,500 ps**; the PE's own stage (`mac_pe`'s minimum period) is **1,095 ps**; the tile on this proxy: **1,500 ps = 667 MHz** (the larger of the two).

**nangate45** — **Period sweep (500, 750, 1000, 1250, 1500 ps):** no swept period has every unit outside the PE-limited ones clean; still violating at 1500 ps: bank_bist_wrap.

## Units on both libraries

Minimum period (register to register, a child's arcs included), cells and total area (cells + macros, the unit with its subtree) per library; the ratio is the second library's period over the first's.

| Unit | Min period ps asap7 | Min period ps nangate45 | ratio | Cells asap7 | Cells nangate45 | Total area µm² asap7 | Total area µm² nangate45 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `mac_pe` | 1,095 | 1,151 | 1.05 | 1,253 | 1,121 | 159 | 2,105 |
| `mac_pe_chk` | 1,057 | 1,154 | 1.09 | 1,694 | 1,301 | 199 | 2,633 |
| `systolic_array` | 1,061 | 1,124 | 1.06 | 1,499,942 | 1,314,470 | 188,598 | 2,492,218 |
| `neo_mac_core` | 1,050 | 1,090 | 1.04 | 1,573,782 | 1,352,389 | 203,581 | 2,682,349 |
| `act_feeder` | 849 | 968 | 1.14 | 8,761 | 7,414 | 23,032 | 305,015 |
| `acc_bank` | 1,129 | 1,284 | 1.14 | 18,242 | 16,297 | 25,209 | 334,648 |
| `neo_mac_core_v02` | 1,262 | 1,529 | 1.21 | 1,602,705 | 1,377,060 | 252,228 | 3,327,119 |
| `wbuf_mem` | 810 | 1,092 | 1.35 | 6,541 | 5,229 | 20,961 | 275,295 |
| `requant` | 893 | 980 | 1.10 | 111,546 | 74,678 | 12,245 | 163,718 |
| `core_seq` | 922 | 865 | 0.94 | 2,993 | 2,992 | 355 | 5,106 |
| `vec_unit` | 1,082 | 1,142 | 1.06 | 288,843 | 236,323 | 53,423 | 721,208 |
| `neo_core` | 1,180 | 1,396 | 1.18 | 2,129,961 | 1,775,606 | 352,087 | 4,664,132 |
| `sram_bank` | 764 | 1,045 | 1.37 | 3,745 | 3,830 | 860,299 | 11,401,833 |
| `mbist` | 1,051 | 812 | 0.77 | 1,025 | 940 | 120 | 1,537 |
| `bank_bist_wrap` | 1,188 | 1,518 | 1.28 | 6,015 | 5,393 | 860,533 | 11,404,779 |
| `crc16_beat` | — | — | — | 3,430 | 3,579 | 502 | 5,407 |
| `ecc39_enc` | — | — | — | 66 | 66 | 11 | 132 |
| `ecc39_dec` | — | — | — | 163 | 163 | 24 | 274 |
| `link_packer` | 795 | 740 | 0.93 | 24,029 | 18,500 | 3,286 | 36,769 |
| `link_unpacker` | 482 | 494 | 1.03 | 2,876 | 2,879 | 336 | 3,363 |
| `tile_nic` | 1,192 | 1,275 | 1.07 | 146,377 | 126,543 | 46,999 | 600,748 |
| `noc_router` | 839 | 849 | 1.01 | 95,582 | 76,558 | 13,841 | 160,226 |
| `tile_dma` | 547 | 623 | 1.14 | 558 | 506 | 80 | 1,074 |
| `tile_telem` | 611 | 549 | 0.90 | 4,331 | 3,647 | 629 | 7,775 |
| `prog_mem` | — | — | — | 14,082 | 8,902 | 1,960 | 22,126 |
| `host_if` | 504 | — | — | 3,495 | 2,112 | 479 | 5,608 |
| `esm` | 540 | 538 | 1.00 | 305 | 334 | 43 | 582 |
| `neo_tile` | 1,369 | 1,409 | 1.03 | 2,429,627 | 2,022,934 | 1,280,484 | 16,909,815 |
| `c16_mac_pe` | 1,095 | 1,151 | 1.05 | 1,253 | 1,121 | 159 | 2,105 |
| `c16_mac_pe_chk` | 1,057 | 1,160 | 1.10 | 1,634 | 1,354 | 193 | 2,671 |
| `c16_systolic_array` | 1,061 | 1,125 | 1.06 | 264,966 | 230,150 | 32,717 | 440,315 |
| `c16_neo_mac_core` | 1,050 | 1,090 | 1.04 | 278,414 | 237,505 | 35,336 | 473,825 |
| `c16_act_feeder` | 789 | 960 | 1.22 | 6,594 | 5,983 | 2,138 | 28,437 |
| `c16_acc_bank` | 1,096 | 1,162 | 1.06 | 5,187 | 4,584 | 1,504 | 19,974 |
| `c16_neo_mac_core_v02` | 1,262 | 1,529 | 1.21 | 290,755 | 248,352 | 39,104 | 523,725 |
| `c16_wbuf_mem` | 811 | 1,039 | 1.28 | 2,466 | 2,009 | 4,506 | 58,846 |
| `c16_requant` | 845 | 971 | 1.15 | 27,929 | 19,018 | 3,072 | 41,349 |
| `c16_core_seq` | 945 | 834 | 0.88 | 2,687 | 2,704 | 324 | 4,611 |
| `c16_vec_unit` | 1,085 | 1,142 | 1.05 | 82,601 | 67,616 | 13,709 | 184,832 |
| `c16_neo_core` | 1,169 | 1,396 | 1.19 | 437,894 | 361,961 | 64,200 | 860,288 |
| `top_link_packer` | 866 | 691 | 0.80 | 24,085 | 18,382 | 3,291 | 36,857 |
| `top_link_unpacker` | 487 | 499 | 1.02 | 2,880 | 2,880 | 337 | 3,365 |
| `top_tile_nic` | 1,240 | 1,253 | 1.01 | 147,513 | 128,376 | 47,741 | 610,343 |
| `top_noc_router` | 869 | 822 | 0.95 | 92,649 | 83,571 | 13,488 | 163,775 |
| `top_tile_dma` | 547 | 519 | 0.95 | 566 | 531 | 81 | 1,100 |
| `top_neo_tile` | 2,329 | 2,450 | 1.05 | 2,427,907 | 2,031,717 | 1,280,880 | 16,923,106 |
| `noc_axi_bridge` | 1,359 | 1,481 | 1.09 | 73,284 | 65,787 | 11,758 | 142,039 |
| `noc_axi_bridge_mem` | 1,291 | 1,319 | 1.02 | 59,320 | 51,685 | 10,047 | 119,872 |
| `top_ctrl` | 774 | 1,082 | 1.40 | 13,516 | 11,122 | 4,011 | 51,704 |
| `mesh_ctrl` | 696 | 716 | 1.03 | 45,575 | 32,855 | 4,685 | 56,766 |
| `neo_top` | 1,181 | 892 | 0.76 | 157,287,785 | 131,576,855 | 82,258,533 | 1,086,425,269 |

