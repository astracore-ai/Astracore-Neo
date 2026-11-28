# Fault campaign report (`faults` job; 02_dv/dv/cocotb/faults/)

Faults injected into the flops of the cocotb builds through the model's VPI (--public-flat-rw), each run classified against the fault-free run of the same driver: **detected** (a sticky cause or the watchdog; the latency is the first cycle the cause was seen after the injection), **masked** (results bit-exact, no cause), **dangerous** (results wrong, no cause), **hang** (the run did not end; hang_detected when the watchdog or the fetch timeout fired). DC = detected / (detected + dangerous), masked faults (safe in this workload) excluded; the interval is the 95 % Wilson interval of that fraction. The FTTI budget (10 ms, 20,000,000 cycles at 2 GHz) exceeds every run, so every cause seen is within it.

## The core build (16 x 8 neo_core, NCHK = 4, workload C1)

Seed 291, 22641 of 25000 faults (20000 sampled over the flops, 5000 over the memory arrays) run by 4 workers in 4507.8 s (794.6 ms per fault); the fault-free run 1698 cycles (0.371 s); population 3403 flop elements, 47581 bits.

| Outcome | Faults |
| --- | --- |
| detected | 7876 |
| masked | 13977 |
| dangerous | 762 |
| hang_detected | 25 |
| hang | 1 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 92.1 %, dangerous 3.2 % of all faults, masked 58.8 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 35240 | 13204 | 6071 | 6628 | 505 | 0 / 0 | 92.3 % (91.7 % .. 92.9 %) | 3.8 % (3.5 % .. 4.2 %) | 27 / 608 | array_abft 6071 |
| neo_core | 6331 | 2399 | 2 | 2396 | 0 | 0 / 1 | 66.7 % (20.8 % .. 93.9 %) | 0.0 % (0.0 % .. 0.2 %) | 37 / 45 | seq_err 2 |
| requant | 3368 | 1292 | 379 | 913 | 0 | 0 / 0 | 100.0 % (99.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 761 / 1649 | rq_err 322, rq_tbl_perr 57 |
| neo_core.macro | 617 | 251 | 0 | 251 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 1.5 %) | - |  |
| acc_bank | 601 | 240 | 89 | 149 | 2 | 0 / 0 | 97.8 % (92.3 % .. 99.4 %) | 0.8 % (0.2 % .. 3.0 %) | 762 / 1659 | acc_abft 82, seq_err 7 |
| core_seq | 412 | 176 | 72 | 89 | 0 | 15 / 0 | 100.0 % (95.8 % .. 100.0 %) | 0.0 % (0.0 % .. 2.1 %) | 4 / 1296 | seq_err 87 |
| act_feeder | 380 | 170 | 80 | 80 | 0 | 10 / 0 | 100.0 % (95.9 % .. 100.0 %) | 0.0 % (0.0 % .. 2.2 %) | 16 / 654 | ctrl_err 90 |
| neo_mac_core_v02 | 280 | 124 | 0 | 37 | 87 | 0 / 0 | 0.0 % (0.0 % .. 4.2 %) | 70.2 % (61.6 % .. 77.5 %) | - |  |
| acc_bank.abft_checker | 164 | 83 | 38 | 45 | 0 | 0 / 0 | 100.0 % (90.8 % .. 100.0 %) | 0.0 % (0.0 % .. 4.4 %) | 886 / 1657 | acc_abft 38 |
| neo_mac_core.abft_checker | 164 | 83 | 69 | 14 | 0 | 0 / 0 | 100.0 % (94.7 % .. 100.0 %) | 0.0 % (0.0 % .. 4.4 %) | 3 / 31 | array_abft 69 |
| wbuf_mem.macro | 8 | 22 | 13 | 9 | 0 | 0 / 0 | 100.0 % (77.2 % .. 100.0 %) | 0.0 % (0.0 % .. 14.9 %) | 10 / 96 | seq_err 13 |
| act_feeder.macro | 7 | 28 | 22 | 6 | 0 | 0 / 0 | 100.0 % (85.1 % .. 100.0 %) | 0.0 % (0.0 % .. 12.1 %) | 2 / 42 | ctrl_err 22 |
| acc_bank.macro | 6 | 25 | 21 | 4 | 0 | 0 / 0 | 100.0 % (84.5 % .. 100.0 %) | 0.0 % (0.0 % .. 13.3 %) | 5 / 54 | seq_err 21 |
| wbuf_mem | 3 | 24 | 14 | 10 | 0 | 0 / 0 | 100.0 % (78.5 % .. 100.0 %) | 0.0 % (0.0 % .. 13.8 %) | 1 / 41 | seq_err 7, wbuf_ue 5, wbuf_ce 2 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| wbuf_mem.sram | 99840 | 1115 | 405 | 710 | 0 | 0 / 0 | 100.0 % (99.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 519 / 1546 | wbuf_ce 405 |
| neo_core.sram | 88320 | 1128 | 0 | 1128 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.3 %) | - |  |
| act_feeder.sram | 32768 | 1134 | 0 | 966 | 168 | 0 / 0 | 0.0 % (0.0 % .. 2.2 %) | 14.8 % (12.9 % .. 17.0 %) | - |  |
| acc_bank.sram | 18432 | 1143 | 601 | 542 | 0 | 0 / 0 | 100.0 % (99.4 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 762 / 1645 | acc_abft 601 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): neo_core 1072 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core 84 bits, requant 64 bits.

## The tile build (2 x 2 mesh of 16 x 8 tiles, workload M1)

Seed 292, 18042 of 24998 faults (20000 sampled over the flops, 4998 over the memory arrays) run by 4 workers in 13393.1 s (2966.5 ms per fault); the fault-free run 3864 cycles (1.39 s), watchdog window 7728 cycles; population 4289 flop elements, 64144 bits.

| Outcome | Faults |
| --- | --- |
| detected | 3747 |
| masked | 13826 |
| dangerous | 423 |
| hang_detected | 46 |
| hang | 0 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 92.3 %, dangerous 2.0 % of all faults, masked 74.4 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 21848 | 4765 | 2238 | 2332 | 195 | 0 / 0 | 92.0 % (90.8 % .. 93.0 %) | 4.1 % (3.6 % .. 4.7 %) | 45 / 1968 | array_abft 2238 |
| tile_nic | 14192 | 3100 | 131 | 2940 | 24 | 5 / 0 | 85.0 % (78.7 % .. 89.7 %) | 0.8 % (0.5 % .. 1.1 %) | 1336 / 7614 | crc_err 89, array_abft 33, lost_err 12 |
| neo_core | 6331 | 1389 | 10 | 1378 | 0 | 1 / 0 | 100.0 % (74.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 3 / 3047 | seq_err 10, rq_tbl_perr 1 |
| host_if | 3756 | 827 | 406 | 414 | 0 | 7 / 0 | 100.0 % (99.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 3 / 2597 | ctrl_path_err 408, watchdog 4, iso_err 1 |
| requant | 3368 | 762 | 183 | 579 | 0 | 0 / 0 | 100.0 % (97.9 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 1696 / 3528 | rq_err 183 |
| neo_tile | 3080 | 712 | 5 | 707 | 0 | 0 / 0 | 100.0 % (56.6 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 2 / 2 | ctrl_path_err 5 |
| sram_bank.macro | 2496 | 578 | 37 | 541 | 0 | 0 / 0 | 100.0 % (90.6 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 16 / 185 | ecc_ce 37 |
| neo_core.macro | 2468 | 555 | 0 | 555 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.7 %) | - |  |
| noc_router | 1904 | 416 | 20 | 382 | 3 | 11 / 0 | 91.2 % (77.0 % .. 97.0 %) | 0.7 % (0.2 % .. 2.1 %) | 24 / 7146 | parity_err 19, crc_err 6, iso_err 3 |
| tile_nic.macro | 1180 | 282 | 1 | 254 | 27 | 0 / 0 | 3.6 % (0.6 % .. 17.7 %) | 9.6 % (6.7 % .. 13.6 %) | 49 / 49 | crc_err 1 |
| tile_dma | 848 | 208 | 114 | 92 | 0 | 2 / 0 | 100.0 % (96.8 % .. 100.0 %) | 0.0 % (0.0 % .. 1.8 %) | 3 / 289 | ctrl_path_err 116 |
| acc_bank | 601 | 146 | 42 | 104 | 0 | 0 / 0 | 100.0 % (91.6 % .. 100.0 %) | 0.0 % (0.0 % .. 2.6 %) | 1259 / 3507 | acc_abft 35, seq_err 7 |
| core_seq | 412 | 115 | 33 | 73 | 0 | 9 / 0 | 100.0 % (91.6 % .. 100.0 %) | 0.0 % (0.0 % .. 3.2 %) | 3 / 1720 | seq_err 42 |
| act_feeder | 380 | 99 | 37 | 56 | 0 | 6 / 0 | 100.0 % (91.8 % .. 100.0 %) | 0.0 % (0.0 % .. 3.7 %) | 32 / 1847 | ctrl_err 43 |
| neo_mac_core_v02 | 280 | 65 | 0 | 32 | 33 | 0 / 0 | 0.0 % (0.0 % .. 10.4 %) | 50.8 % (38.9 % .. 62.5 %) | - |  |
| prog_mem | 264 | 72 | 2 | 67 | 2 | 1 / 0 | 60.0 % (23.1 % .. 88.2 %) | 2.8 % (0.8 % .. 9.6 %) | 2084 / 7185 | watchdog 1, array_abft 1, prog_ce 1 |
| esm | 176 | 54 | 19 | 35 | 0 | 0 / 0 | 100.0 % (83.2 % .. 100.0 %) | 0.0 % (0.0 % .. 6.6 %) | 1 / 2 | ecc_ue 3, prog_ce 3, rq_err 3 |
| mbist | 168 | 50 | 1 | 48 | 1 | 0 / 0 | 50.0 % (9.5 % .. 90.5 %) | 2.0 % (0.4 % .. 10.5 %) | 5 / 5 | bist_fail 1 |
| acc_bank.abft_checker | 164 | 60 | 28 | 32 | 0 | 0 / 0 | 100.0 % (87.9 % .. 100.0 %) | 0.0 % (0.0 % .. 6.0 %) | 2004 / 3470 | acc_abft 28 |
| neo_mac_core.abft_checker | 164 | 50 | 23 | 27 | 0 | 0 / 0 | 100.0 % (85.7 % .. 100.0 %) | 0.0 % (0.0 % .. 7.1 %) | 26 / 1892 | array_abft 23 |
| link_packer | 20 | 23 | 3 | 14 | 2 | 4 / 0 | 77.8 % (45.3 % .. 93.7 %) | 8.7 % (2.4 % .. 26.8 %) | 5894 / 7129 | watchdog 4, crc_err 3, lost_err 3 |
| sram_bank | 12 | 24 | 12 | 12 | 0 | 0 / 0 | 100.0 % (75.7 % .. 100.0 %) | 0.0 % (0.0 % .. 13.8 %) | 2 / 2 | ecc_ue 8, ecc_ce 4 |
| link_unpacker | 8 | 22 | 0 | 22 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 14.9 %) | - |  |
| wbuf_mem.macro | 8 | 19 | 10 | 9 | 0 | 0 / 0 | 100.0 % (72.2 % .. 100.0 %) | 0.0 % (0.0 % .. 16.8 %) | 14 / 89 | seq_err 10 |
| act_feeder.macro | 7 | 16 | 10 | 6 | 0 | 0 / 0 | 100.0 % (72.2 % .. 100.0 %) | 0.0 % (0.0 % .. 19.4 %) | 839 / 1893 | ctrl_err 10 |
| acc_bank.macro | 6 | 23 | 14 | 9 | 0 | 0 / 0 | 100.0 % (78.5 % .. 100.0 %) | 0.0 % (0.0 % .. 14.3 %) | 38 / 1949 | seq_err 14 |
| wbuf_mem | 3 | 22 | 11 | 11 | 0 | 0 / 0 | 100.0 % (74.1 % .. 100.0 %) | 0.0 % (0.0 % .. 14.9 %) | 2 / 1448 | wbuf_ue 8, wbuf_ce 2, seq_err 1 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| sram_bank.sram | 638976 | 574 | 9 | 565 | 0 | 0 / 0 | 100.0 % (70.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 765 / 1416 | ecc_ce 9 |
| neo_core.sram | 88320 | 606 | 0 | 606 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.6 %) | - |  |
| tile_nic.sram | 79616 | 614 | 0 | 599 | 15 | 0 / 0 | 0.0 % (0.0 % .. 20.4 %) | 2.4 % (1.5 % .. 4.0 %) | - |  |
| wbuf_mem.sram | 59904 | 595 | 189 | 406 | 0 | 0 / 0 | 100.0 % (98.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.6 %) | 718 / 3383 | wbuf_ce 189 |
| act_feeder.sram | 32768 | 610 | 0 | 489 | 121 | 0 / 0 | 0.0 % (0.0 % .. 3.1 %) | 19.8 % (16.9 % .. 23.2 %) | - |  |
| acc_bank.sram | 18432 | 589 | 159 | 430 | 0 | 0 / 0 | 100.0 % (97.6 % .. 100.0 %) | 0.0 % (0.0 % .. 0.6 %) | 1038 / 3287 | acc_abft 159 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): prog_mem 9984 bits, neo_core 4288 bits, tile_nic 4096 bits, noc_router 3360 bits; idle core (tiles other than the computing one): neo_core.sram 264960 bits, wbuf_mem.sram 179712 bits, act_feeder.sram 98304 bits, neo_mac_core 65544 bits, acc_bank.sram 55296 bits, neo_core 19245 bits, requant 10296 bits, acc_bank 1803 bits, core_seq 1236 bits, act_feeder 1140 bits, neo_mac_core_v02 840 bits, acc_bank.abft_checker 492 bits, neo_mac_core.abft_checker 492 bits, wbuf_mem.macro 24 bits, act_feeder.macro 21 bits, acc_bank.macro 18 bits, wbuf_mem 9 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core 84 bits, requant 64 bits, noc_router 60 bits, tile_nic 8 bits.

## The comparator storage of drop 0.41 (item 2) in the sample

| Register | Faults | Detected | Masked | Dangerous | Hang | Latency max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| feeder inb_q | 3 | 1 | 2 | 0 | 0 | 2 |
| feeder inb_dq (copy) | 1 | 1 | 0 | 0 | 0 | 2 |
| feeder a0_dq (copy) | 1 | 0 | 1 | 0 | 0 | - |
| feeder first_q | 1 | 1 | 0 | 0 | 0 | 32 |
| feeder first_dq (copy) | 1 | 1 | 0 | 0 | 0 | 8 |
| feeder addr_dq (copy) | 6 | 4 | 2 | 0 | 0 | 49 |
| activation buffer port-B latch | 44 | 32 | 12 | 0 | 0 | 1893 |
| core d_w_raddr_q (copy) | 6 | 3 | 3 | 0 | 0 | 143 |
| weight buffer port-B latch | 41 | 23 | 18 | 0 | 0 | 96 |
| weight buffer ra0_q | 15 | 8 | 7 | 0 | 0 | 1448 |
| acc v1 | 1 | 1 | 0 | 0 | 0 | 2 |
| acc idx1 | 6 | 6 | 0 | 0 | 0 | 13 |
| acc first1_n (copy) | 1 | 0 | 1 | 0 | 0 | - |
| acc idx1_n (copy) | 3 | 3 | 0 | 0 | 0 | 3 |
| acc fwd1_n (copy) | 2 | 2 | 0 | 0 | 0 | 2 |
| acc rbq_n (copy) | 3 | 2 | 1 | 0 | 0 | 1208 |
| accumulator port-B latch | 48 | 35 | 13 | 0 | 0 | 1949 |

The undetected faults' flops, grouped by block: `undetected.md` (1186 faults).
