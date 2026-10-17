# Fault campaign report (`faults` job; 02_dv/dv/cocotb/faults/)

Faults injected into the flops of the cocotb builds through the model's VPI (--public-flat-rw), each run classified against the fault-free run of the same driver: **detected** (a sticky cause or the watchdog; the latency is the first cycle the cause was seen after the injection), **masked** (results bit-exact, no cause), **dangerous** (results wrong, no cause), **hang** (the run did not end; hang_detected when the watchdog or the fetch timeout fired). DC = detected / (detected + dangerous), masked faults (safe in this workload) excluded; the interval is the 95 % Wilson interval of that fraction. The FTTI budget (10 ms, 20,000,000 cycles at 2 GHz) exceeds every run, so every cause seen is within it.

## The core build (16 x 8 neo_core, NCHK = 4, workload C1)

Seed 215, 25000 of 25000 faults (20000 sampled over the flops, 5000 over the memory arrays) run by 4 workers in 3215.4 s (510.3 ms per fault); the fault-free run 1698 cycles (0.265 s); population 3403 flop elements, 47581 bits.

| Outcome | Faults |
| --- | --- |
| detected | 8907 |
| masked | 15256 |
| dangerous | 814 |
| hang_detected | 23 |
| hang | 0 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 93.2 %, dangerous 2.8 % of all faults, masked 58.1 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 35240 | 14578 | 6904 | 7196 | 478 | 0 / 0 | 93.5 % (92.9 % .. 94.1 %) | 3.3 % (3.0 % .. 3.6 %) | 27 / 431 | array_abft 6904 |
| neo_core | 6331 | 2640 | 2 | 2638 | 0 | 0 / 0 | 100.0 % (34.2 % .. 100.0 %) | 0.0 % (0.0 % .. 0.1 %) | 11 / 21 | seq_err 2 |
| requant | 3368 | 1416 | 421 | 995 | 0 | 0 / 0 | 100.0 % (99.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 782 / 1667 | rq_err 347, rq_tbl_perr 74 |
| neo_core.macro | 617 | 280 | 0 | 280 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 1.4 %) | - |  |
| acc_bank | 601 | 273 | 119 | 154 | 0 | 0 / 0 | 100.0 % (96.9 % .. 100.0 %) | 0.0 % (0.0 % .. 1.4 %) | 724 / 1625 | acc_abft 110, seq_err 9 |
| core_seq | 412 | 195 | 74 | 104 | 0 | 17 / 0 | 100.0 % (95.9 % .. 100.0 %) | 0.0 % (0.0 % .. 1.9 %) | 2 / 1643 | seq_err 91 |
| act_feeder | 380 | 182 | 98 | 78 | 0 | 6 / 0 | 100.0 % (96.4 % .. 100.0 %) | 0.0 % (0.0 % .. 2.1 %) | 14 / 566 | ctrl_err 104 |
| neo_mac_core_v02 | 280 | 141 | 0 | 42 | 99 | 0 / 0 | 0.0 % (0.0 % .. 3.7 %) | 70.2 % (62.2 % .. 77.1 %) | - |  |
| acc_bank.abft_checker | 164 | 93 | 44 | 49 | 0 | 0 / 0 | 100.0 % (92.0 % .. 100.0 %) | 0.0 % (0.0 % .. 4.0 %) | 742 / 1658 | acc_abft 44 |
| neo_mac_core.abft_checker | 164 | 93 | 66 | 27 | 0 | 0 / 0 | 100.0 % (94.5 % .. 100.0 %) | 0.0 % (0.0 % .. 4.0 %) | 3 / 49 | array_abft 66 |
| wbuf_mem.macro | 8 | 28 | 16 | 12 | 0 | 0 / 0 | 100.0 % (80.6 % .. 100.0 %) | 0.0 % (0.0 % .. 12.1 %) | 4 / 109 | seq_err 16 |
| act_feeder.macro | 7 | 28 | 23 | 5 | 0 | 0 / 0 | 100.0 % (85.7 % .. 100.0 %) | 0.0 % (0.0 % .. 12.1 %) | 4 / 40 | ctrl_err 23 |
| acc_bank.macro | 6 | 27 | 21 | 6 | 0 | 0 / 0 | 100.0 % (84.5 % .. 100.0 %) | 0.0 % (0.0 % .. 12.5 %) | 2 / 50 | seq_err 21 |
| wbuf_mem | 3 | 26 | 15 | 11 | 0 | 0 / 0 | 100.0 % (79.6 % .. 100.0 %) | 0.0 % (0.0 % .. 12.9 %) | 1 / 8 | wbuf_ce 9, wbuf_ue 4, seq_err 2 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| wbuf_mem.sram | 99840 | 1250 | 420 | 830 | 0 | 0 / 0 | 100.0 % (99.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 521 / 1546 | wbuf_ce 420 |
| neo_core.sram | 88320 | 1250 | 0 | 1250 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.3 %) | - |  |
| act_feeder.sram | 32768 | 1250 | 0 | 1013 | 237 | 0 / 0 | 0.0 % (0.0 % .. 1.6 %) | 19.0 % (16.9 % .. 21.2 %) | - |  |
| acc_bank.sram | 18432 | 1250 | 684 | 566 | 0 | 0 / 0 | 100.0 % (99.4 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 828 / 1694 | acc_abft 684 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): neo_core 1072 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core 84 bits, requant 64 bits.

## The tile build (2 x 2 mesh of 16 x 8 tiles, workload M1)

Seed 216, 24998 of 24998 faults (20000 sampled over the flops, 4998 over the memory arrays) run by 4 workers in 12942.3 s (2064.1 ms per fault); the fault-free run 3864 cycles (0.99 s), watchdog window 7728 cycles; population 3533 flop elements, 60592 bits.

| Outcome | Faults |
| --- | --- |
| detected | 5335 |
| masked | 19258 |
| dangerous | 348 |
| hang_detected | 57 |
| hang | 0 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 97.3 %, dangerous 0.7 % of all faults, masked 75.0 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 18296 | 5860 | 3099 | 2757 | 4 | 0 / 0 | 99.9 % (99.7 % .. 99.9 %) | 0.1 % (0.0 % .. 0.2 %) | 48 / 2070 | array_abft 3099 |
| tile_nic | 14192 | 4551 | 179 | 4338 | 28 | 6 / 0 | 86.9 % (81.7 % .. 90.7 %) | 0.6 % (0.4 % .. 0.9 %) | 855 / 6803 | crc_err 116, array_abft 52, lost_err 10 |
| neo_core | 6331 | 2044 | 26 | 2017 | 0 | 1 / 0 | 100.0 % (87.5 % .. 100.0 %) | 0.0 % (0.0 % .. 0.2 %) | 3 / 1831 | seq_err 23, rq_tbl_perr 3, rq_err 1 |
| host_if | 3756 | 1223 | 640 | 569 | 0 | 14 / 0 | 100.0 % (99.4 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 3 / 1016 | ctrl_path_err 650, iso_err 3, watchdog 1 |
| requant | 3368 | 1099 | 263 | 836 | 0 | 0 / 0 | 100.0 % (98.6 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 1614 / 3533 | rq_err 263 |
| neo_tile | 3080 | 1007 | 0 | 1007 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.4 %) | - |  |
| sram_bank.macro | 2496 | 821 | 63 | 758 | 0 | 0 / 0 | 100.0 % (94.3 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 21 / 225 | ecc_ce 63 |
| neo_core.macro | 2468 | 812 | 0 | 812 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.5 %) | - |  |
| noc_router | 1904 | 632 | 37 | 579 | 1 | 15 / 0 | 98.1 % (90.1 % .. 99.7 %) | 0.2 % (0.0 % .. 0.9 %) | 9 / 7345 | parity_err 37, crc_err 12, lost_err 2 |
| tile_nic.macro | 1180 | 401 | 3 | 352 | 46 | 0 / 0 | 6.1 % (2.1 % .. 16.5 %) | 11.5 % (8.7 % .. 15.0 %) | 209 / 308 | crc_err 3 |
| tile_dma | 848 | 296 | 167 | 126 | 0 | 3 / 0 | 100.0 % (97.8 % .. 100.0 %) | 0.0 % (0.0 % .. 1.3 %) | 3 / 181 | ctrl_path_err 170 |
| acc_bank | 601 | 217 | 65 | 152 | 0 | 0 / 0 | 100.0 % (94.4 % .. 100.0 %) | 0.0 % (0.0 % .. 1.7 %) | 1895 / 3532 | acc_abft 61, seq_err 4 |
| core_seq | 412 | 157 | 57 | 91 | 0 | 9 / 0 | 100.0 % (94.5 % .. 100.0 %) | 0.0 % (0.0 % .. 2.4 %) | 3 / 2782 | seq_err 66 |
| act_feeder | 380 | 146 | 57 | 86 | 0 | 3 / 0 | 100.0 % (94.0 % .. 100.0 %) | 0.0 % (0.0 % .. 2.6 %) | 24 / 2002 | ctrl_err 60 |
| neo_mac_core_v02 | 280 | 114 | 0 | 50 | 64 | 0 / 0 | 0.0 % (0.0 % .. 5.7 %) | 56.1 % (47.0 % .. 64.9 %) | - |  |
| prog_mem | 264 | 109 | 3 | 105 | 1 | 0 / 0 | 75.0 % (30.1 % .. 95.4 %) | 0.9 % (0.2 % .. 5.0 %) | 2 / 2 | ctrl_path_err 2, prog_ce 1 |
| esm | 176 | 81 | 28 | 53 | 0 | 0 / 0 | 100.0 % (87.9 % .. 100.0 %) | 0.0 % (0.0 % .. 4.5 %) | 1 / 2 | rq_err 4, watchdog 3, ctrl_path_err 3 |
| mbist | 168 | 79 | 0 | 76 | 3 | 0 / 0 | 0.0 % (0.0 % .. 56.2 %) | 3.8 % (1.3 % .. 10.6 %) | - |  |
| acc_bank.abft_checker | 164 | 77 | 32 | 45 | 0 | 0 / 0 | 100.0 % (89.3 % .. 100.0 %) | 0.0 % (0.0 % .. 4.8 %) | 1857 / 3500 | acc_abft 32 |
| neo_mac_core.abft_checker | 164 | 77 | 44 | 33 | 0 | 0 / 0 | 100.0 % (92.0 % .. 100.0 %) | 0.0 % (0.0 % .. 4.8 %) | 28 / 1938 | array_abft 44 |
| link_packer | 20 | 32 | 1 | 23 | 2 | 6 / 0 | 77.8 % (45.3 % .. 93.7 %) | 6.2 % (1.7 % .. 20.1 %) | 5491 / 7640 | watchdog 6, crc_err 1, lost_err 1 |
| sram_bank | 12 | 29 | 16 | 13 | 0 | 0 / 0 | 100.0 % (80.6 % .. 100.0 %) | 0.0 % (0.0 % .. 11.7 %) | 2 / 2 | ecc_ue 10, ecc_ce 6 |
| link_unpacker | 8 | 28 | 0 | 28 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 12.1 %) | - |  |
| wbuf_mem.macro | 8 | 28 | 12 | 16 | 0 | 0 / 0 | 100.0 % (75.7 % .. 100.0 %) | 0.0 % (0.0 % .. 12.1 %) | 34 / 1630 | seq_err 12 |
| act_feeder.macro | 7 | 27 | 12 | 15 | 0 | 0 / 0 | 100.0 % (75.7 % .. 100.0 %) | 0.0 % (0.0 % .. 12.5 %) | 904 / 1649 | ctrl_err 12 |
| acc_bank.macro | 6 | 27 | 17 | 10 | 0 | 0 / 0 | 100.0 % (81.6 % .. 100.0 %) | 0.0 % (0.0 % .. 12.5 %) | 11 / 1875 | seq_err 17 |
| wbuf_mem | 3 | 26 | 20 | 6 | 0 | 0 / 0 | 100.0 % (83.9 % .. 100.0 %) | 0.0 % (0.0 % .. 12.9 %) | 2 / 1330 | wbuf_ce 10, seq_err 8, wbuf_ue 2 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| sram_bank.sram | 638976 | 833 | 17 | 816 | 0 | 0 / 0 | 100.0 % (81.6 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 683 / 1352 | ecc_ce 17 |
| tile_nic.sram | 79616 | 833 | 0 | 822 | 11 | 0 / 0 | 0.0 % (0.0 % .. 25.9 %) | 1.3 % (0.7 % .. 2.3 %) | - |  |
| wbuf_mem.sram | 59904 | 833 | 236 | 597 | 0 | 0 / 0 | 100.0 % (98.4 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 843 / 2860 | wbuf_ce 236 |
| act_feeder.sram | 32768 | 833 | 0 | 645 | 188 | 0 / 0 | 0.0 % (0.0 % .. 2.0 %) | 22.6 % (19.9 % .. 25.5 %) | - |  |
| neo_core.sram | 20736 | 833 | 0 | 833 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.5 %) | - |  |
| acc_bank.sram | 18432 | 833 | 241 | 592 | 0 | 0 / 0 | 100.0 % (98.4 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 908 / 3550 | acc_abft 241 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): prog_mem 9984 bits, neo_core 4288 bits, tile_nic 4096 bits, noc_router 3360 bits; idle core (tiles other than the computing one): neo_core.sram 264960 bits, wbuf_mem.sram 179712 bits, act_feeder.sram 98304 bits, neo_mac_core 65544 bits, acc_bank.sram 55296 bits, neo_core 19245 bits, requant 10296 bits, acc_bank 1803 bits, core_seq 1236 bits, act_feeder 1140 bits, neo_mac_core_v02 840 bits, acc_bank.abft_checker 492 bits, neo_mac_core.abft_checker 492 bits, wbuf_mem.macro 24 bits, act_feeder.macro 21 bits, acc_bank.macro 18 bits, wbuf_mem 9 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core.sram 67584 bits, neo_mac_core 3552 bits, neo_core 84 bits, requant 64 bits, noc_router 60 bits, tile_nic 8 bits.

## The comparator storage of drop 0.41 (item 2) in the sample

| Register | Faults | Detected | Masked | Dangerous | Hang | Latency max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| feeder inb_q | 1 | 0 | 1 | 0 | 0 | - |
| feeder inb_dq (copy) | 2 | 2 | 0 | 0 | 0 | 13 |
| feeder a0_dq (copy) | 1 | 0 | 1 | 0 | 0 | - |
| feeder first_oq | 1 | 0 | 1 | 0 | 0 | - |
| feeder addr_dq (copy) | 5 | 2 | 3 | 0 | 0 | 3 |
| activation buffer port-B latch | 55 | 35 | 20 | 0 | 0 | 1649 |
| core d_w_load_q (copy) | 1 | 1 | 0 | 0 | 0 | 2 |
| core d_w_raddr_q (copy) | 4 | 1 | 3 | 0 | 0 | 21 |
| weight buffer port-B latch | 56 | 28 | 28 | 0 | 0 | 1630 |
| weight buffer ra0_q | 15 | 10 | 5 | 0 | 0 | 1330 |
| acc first1 | 1 | 1 | 0 | 0 | 0 | 2 |
| acc idx1 | 5 | 5 | 0 | 0 | 0 | 6 |
| acc v1_n (copy) | 1 | 1 | 0 | 0 | 0 | 2 |
| acc first1_n (copy) | 2 | 2 | 0 | 0 | 0 | 3 |
| acc idx1_n (copy) | 2 | 2 | 0 | 0 | 0 | 2 |
| acc rbq_n (copy) | 7 | 2 | 5 | 0 | 0 | 6 |
| accumulator port-B latch | 54 | 38 | 16 | 0 | 0 | 1875 |

The undetected faults' flops, grouped by block: `undetected.md` (1162 faults).
