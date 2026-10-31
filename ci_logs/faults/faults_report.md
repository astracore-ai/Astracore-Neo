# Fault campaign report (`faults` job; 02_dv/dv/cocotb/faults/)

Faults injected into the flops of the cocotb builds through the model's VPI (--public-flat-rw), each run classified against the fault-free run of the same driver: **detected** (a sticky cause or the watchdog; the latency is the first cycle the cause was seen after the injection), **masked** (results bit-exact, no cause), **dangerous** (results wrong, no cause), **hang** (the run did not end; hang_detected when the watchdog or the fetch timeout fired). DC = detected / (detected + dangerous), masked faults (safe in this workload) excluded; the interval is the 95 % Wilson interval of that fraction. The FTTI budget (10 ms, 20,000,000 cycles at 2 GHz) exceeds every run, so every cause seen is within it.

## The core build (16 x 8 neo_core, NCHK = 4, workload C1)

Seed 259, 21128 of 25000 faults (20000 sampled over the flops, 5000 over the memory arrays) run by 4 workers in 4507.6 s (851.5 ms per fault); the fault-free run 1698 cycles (0.397 s); population 3403 flop elements, 47581 bits.

| Outcome | Faults |
| --- | --- |
| detected | 7207 |
| masked | 13216 |
| dangerous | 686 |
| hang_detected | 19 |
| hang | 0 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 92.4 %, dangerous 3.1 % of all faults, masked 59.6 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 35240 | 12287 | 5600 | 6248 | 439 | 0 / 0 | 92.7 % (92.0 % .. 93.4 %) | 3.6 % (3.3 % .. 3.9 %) | 27 / 666 | array_abft 5600 |
| neo_core | 6331 | 2243 | 0 | 2243 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.2 %) | - |  |
| requant | 3368 | 1190 | 328 | 862 | 0 | 0 / 0 | 100.0 % (98.8 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 896 / 1652 | rq_err 270, rq_tbl_perr 58 |
| neo_core.macro | 617 | 228 | 0 | 228 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 1.7 %) | - |  |
| acc_bank | 601 | 235 | 84 | 150 | 1 | 0 / 0 | 98.8 % (93.6 % .. 99.8 %) | 0.4 % (0.1 % .. 2.4 %) | 782 / 1653 | acc_abft 78, seq_err 6 |
| core_seq | 412 | 168 | 59 | 96 | 0 | 13 / 0 | 100.0 % (94.9 % .. 100.0 %) | 0.0 % (0.0 % .. 2.2 %) | 2 / 1568 | seq_err 72 |
| act_feeder | 380 | 154 | 68 | 80 | 0 | 6 / 0 | 100.0 % (95.1 % .. 100.0 %) | 0.0 % (0.0 % .. 2.4 %) | 7 / 293 | ctrl_err 74 |
| neo_mac_core_v02 | 280 | 118 | 0 | 36 | 82 | 0 / 0 | 0.0 % (0.0 % .. 4.5 %) | 69.5 % (60.7 % .. 77.1 %) | - |  |
| acc_bank.abft_checker | 164 | 81 | 41 | 40 | 0 | 0 / 0 | 100.0 % (91.4 % .. 100.0 %) | 0.0 % (0.0 % .. 4.5 %) | 472 / 1532 | acc_abft 41 |
| neo_mac_core.abft_checker | 164 | 80 | 57 | 23 | 0 | 0 / 0 | 100.0 % (93.7 % .. 100.0 %) | 0.0 % (0.0 % .. 4.6 %) | 3 / 30 | array_abft 57 |
| wbuf_mem.macro | 8 | 22 | 10 | 12 | 0 | 0 / 0 | 100.0 % (72.2 % .. 100.0 %) | 0.0 % (0.0 % .. 14.9 %) | 4 / 30 | seq_err 10 |
| act_feeder.macro | 7 | 22 | 19 | 3 | 0 | 0 / 0 | 100.0 % (83.2 % .. 100.0 %) | 0.0 % (0.0 % .. 14.9 %) | 2 / 51 | ctrl_err 19 |
| acc_bank.macro | 6 | 25 | 21 | 4 | 0 | 0 / 0 | 100.0 % (84.5 % .. 100.0 %) | 0.0 % (0.0 % .. 13.3 %) | 3 / 30 | seq_err 21 |
| wbuf_mem | 3 | 25 | 17 | 8 | 0 | 0 / 0 | 100.0 % (81.6 % .. 100.0 %) | 0.0 % (0.0 % .. 13.3 %) | 1 / 32 | wbuf_ce 9, wbuf_ue 5, seq_err 3 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| wbuf_mem.sram | 99840 | 1084 | 371 | 713 | 0 | 0 / 0 | 100.0 % (99.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.4 %) | 442 / 1526 | wbuf_ce 371 |
| neo_core.sram | 88320 | 1046 | 0 | 1046 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.4 %) | - |  |
| act_feeder.sram | 32768 | 1063 | 0 | 899 | 164 | 0 / 0 | 0.0 % (0.0 % .. 2.3 %) | 15.4 % (13.4 % .. 17.7 %) | - |  |
| acc_bank.sram | 18432 | 1057 | 532 | 525 | 0 | 0 / 0 | 100.0 % (99.3 % .. 100.0 %) | 0.0 % (0.0 % .. 0.4 %) | 808 / 1698 | acc_abft 532 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): neo_core 1072 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core 84 bits, requant 64 bits.

## The tile build (2 x 2 mesh of 16 x 8 tiles, workload M1)

Seed 260, 17582 of 24998 faults (20000 sampled over the flops, 4998 over the memory arrays) run by 4 workers in 13370.3 s (3038.9 ms per fault); the fault-free run 3864 cycles (1.511 s), watchdog window 7728 cycles; population 3533 flop elements, 60592 bits.

| Outcome | Faults |
| --- | --- |
| detected | 3733 |
| masked | 13559 |
| dangerous | 236 |
| hang_detected | 54 |
| hang | 0 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 97.7 %, dangerous 0.6 % of all faults, masked 75.2 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 18296 | 4158 | 2145 | 2010 | 3 | 0 / 0 | 99.9 % (99.6 % .. 100.0 %) | 0.1 % (0.0 % .. 0.2 %) | 47 / 2109 | array_abft 2145 |
| tile_nic | 14192 | 3227 | 155 | 3054 | 12 | 6 / 0 | 93.1 % (88.3 % .. 96.0 %) | 0.4 % (0.2 % .. 0.6 %) | 946 / 7583 | crc_err 110, array_abft 33, lost_err 9 |
| neo_core | 6331 | 1424 | 19 | 1404 | 0 | 1 / 0 | 100.0 % (83.9 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 3 / 2836 | seq_err 19, rq_tbl_perr 1 |
| host_if | 3756 | 889 | 444 | 437 | 0 | 8 / 0 | 100.0 % (99.2 % .. 100.0 %) | 0.0 % (0.0 % .. 0.4 %) | 3 / 3422 | ctrl_path_err 448, iso_err 3, array_abft 1 |
| requant | 3368 | 763 | 201 | 562 | 0 | 0 / 0 | 100.0 % (98.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 1905 / 3511 | rq_err 201 |
| neo_tile | 3080 | 691 | 3 | 688 | 0 | 0 / 0 | 100.0 % (43.8 % .. 100.0 %) | 0.0 % (0.0 % .. 0.6 %) | 2 / 2 | ctrl_path_err 3 |
| sram_bank.macro | 2496 | 590 | 31 | 559 | 0 | 0 / 0 | 100.0 % (89.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.6 %) | 17 / 111 | ecc_ce 31 |
| neo_core.macro | 2468 | 584 | 0 | 584 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.7 %) | - |  |
| noc_router | 1904 | 420 | 26 | 374 | 1 | 19 / 0 | 97.8 % (88.7 % .. 99.6 %) | 0.2 % (0.0 % .. 1.3 %) | 15 / 7380 | parity_err 31, crc_err 11, lost_err 5 |
| tile_nic.macro | 1180 | 279 | 0 | 250 | 29 | 0 / 0 | 0.0 % (0.0 % .. 11.7 %) | 10.4 % (7.3 % .. 14.5 %) | - |  |
| tile_dma | 848 | 199 | 104 | 92 | 0 | 3 / 0 | 100.0 % (96.5 % .. 100.0 %) | 0.0 % (0.0 % .. 1.9 %) | 3 / 545 | ctrl_path_err 107 |
| acc_bank | 601 | 145 | 40 | 104 | 1 | 0 / 0 | 97.6 % (87.4 % .. 99.6 %) | 0.7 % (0.1 % .. 3.8 %) | 1537 / 3407 | acc_abft 37, seq_err 3 |
| core_seq | 412 | 129 | 40 | 79 | 0 | 10 / 0 | 100.0 % (92.9 % .. 100.0 %) | 0.0 % (0.0 % .. 2.9 %) | 3 / 3372 | seq_err 50 |
| act_feeder | 380 | 99 | 33 | 62 | 0 | 4 / 0 | 100.0 % (90.6 % .. 100.0 %) | 0.0 % (0.0 % .. 3.7 %) | 88 / 1754 | ctrl_err 37 |
| neo_mac_core_v02 | 280 | 81 | 0 | 39 | 42 | 0 / 0 | 0.0 % (0.0 % .. 8.4 %) | 51.9 % (41.1 % .. 62.4 %) | - |  |
| prog_mem | 264 | 81 | 0 | 80 | 0 | 1 / 0 | 100.0 % (20.7 % .. 100.0 %) | 0.0 % (0.0 % .. 4.5 %) | 6779 / 6779 | watchdog 1 |
| esm | 176 | 56 | 23 | 33 | 0 | 0 / 0 | 100.0 % (85.7 % .. 100.0 %) | 0.0 % (0.0 % .. 6.4 %) | 1 / 2 | watchdog 3, seq_err 3, lost_err 3 |
| mbist | 168 | 57 | 1 | 56 | 0 | 0 / 0 | 100.0 % (20.7 % .. 100.0 %) | 0.0 % (0.0 % .. 6.3 %) | 2 / 2 | bist_fail 1 |
| acc_bank.abft_checker | 164 | 63 | 26 | 37 | 0 | 0 / 0 | 100.0 % (87.1 % .. 100.0 %) | 0.0 % (0.0 % .. 5.7 %) | 1778 / 3441 | acc_abft 26 |
| neo_mac_core.abft_checker | 164 | 54 | 37 | 17 | 0 | 0 / 0 | 100.0 % (90.6 % .. 100.0 %) | 0.0 % (0.0 % .. 6.6 %) | 12 / 1915 | array_abft 37 |
| link_packer | 20 | 22 | 2 | 16 | 2 | 2 / 0 | 66.7 % (30.0 % .. 90.3 %) | 9.1 % (2.5 % .. 27.8 %) | 3341 / 6595 | watchdog 2, crc_err 2, lost_err 2 |
| sram_bank | 12 | 21 | 11 | 10 | 0 | 0 / 0 | 100.0 % (74.1 % .. 100.0 %) | 0.0 % (0.0 % .. 15.5 %) | 2 / 2 | ecc_ce 8, ecc_ue 3 |
| link_unpacker | 8 | 15 | 0 | 15 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 20.4 %) | - |  |
| wbuf_mem.macro | 8 | 17 | 8 | 9 | 0 | 0 / 0 | 100.0 % (67.6 % .. 100.0 %) | 0.0 % (0.0 % .. 18.4 %) | 20 / 480 | seq_err 8 |
| act_feeder.macro | 7 | 12 | 7 | 5 | 0 | 0 / 0 | 100.0 % (64.6 % .. 100.0 %) | 0.0 % (0.0 % .. 24.3 %) | 71 / 1494 | ctrl_err 7 |
| acc_bank.macro | 6 | 21 | 16 | 5 | 0 | 0 / 0 | 100.0 % (80.6 % .. 100.0 %) | 0.0 % (0.0 % .. 15.5 %) | 367 / 1824 | seq_err 16 |
| wbuf_mem | 3 | 17 | 13 | 4 | 0 | 0 / 0 | 100.0 % (77.2 % .. 100.0 %) | 0.0 % (0.0 % .. 18.4 %) | 2 / 825 | wbuf_ce 6, wbuf_ue 5, seq_err 2 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| sram_bank.sram | 638976 | 583 | 11 | 572 | 0 | 0 / 0 | 100.0 % (74.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 645 / 1270 | ecc_ce 11 |
| tile_nic.sram | 79616 | 566 | 0 | 560 | 6 | 0 / 0 | 0.0 % (0.0 % .. 39.0 %) | 1.1 % (0.5 % .. 2.3 %) | - |  |
| wbuf_mem.sram | 59904 | 577 | 175 | 402 | 0 | 0 / 0 | 100.0 % (97.9 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 764 / 2310 | wbuf_ce 175 |
| act_feeder.sram | 32768 | 593 | 0 | 453 | 140 | 0 / 0 | 0.0 % (0.0 % .. 2.7 %) | 23.6 % (20.4 % .. 27.2 %) | - |  |
| neo_core.sram | 20736 | 573 | 0 | 573 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.7 %) | - |  |
| acc_bank.sram | 18432 | 576 | 162 | 414 | 0 | 0 / 0 | 100.0 % (97.7 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 1012 / 3472 | acc_abft 162 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): prog_mem 9984 bits, neo_core 4288 bits, tile_nic 4096 bits, noc_router 3360 bits; idle core (tiles other than the computing one): neo_core.sram 264960 bits, wbuf_mem.sram 179712 bits, act_feeder.sram 98304 bits, neo_mac_core 65544 bits, acc_bank.sram 55296 bits, neo_core 19245 bits, requant 10296 bits, acc_bank 1803 bits, core_seq 1236 bits, act_feeder 1140 bits, neo_mac_core_v02 840 bits, acc_bank.abft_checker 492 bits, neo_mac_core.abft_checker 492 bits, wbuf_mem.macro 24 bits, act_feeder.macro 21 bits, acc_bank.macro 18 bits, wbuf_mem 9 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core.sram 67584 bits, neo_mac_core 3552 bits, neo_core 84 bits, requant 64 bits, noc_router 60 bits, tile_nic 8 bits.

## The comparator storage of drop 0.41 (item 2) in the sample

| Register | Faults | Detected | Masked | Dangerous | Hang | Latency max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| feeder inb_q | 1 | 1 | 0 | 0 | 0 | 15 |
| feeder inb_dq (copy) | 1 | 1 | 0 | 0 | 0 | 3 |
| feeder a0_q | 1 | 1 | 0 | 0 | 0 | 2 |
| feeder first_oq | 1 | 0 | 1 | 0 | 0 | - |
| feeder addr_dq (copy) | 4 | 3 | 1 | 0 | 0 | 2 |
| activation buffer port-B latch | 34 | 26 | 8 | 0 | 0 | 1494 |
| core d_w_raddr_q (copy) | 4 | 2 | 2 | 0 | 0 | 1292 |
| weight buffer port-B latch | 39 | 18 | 21 | 0 | 0 | 480 |
| weight buffer ra0_q | 12 | 5 | 7 | 0 | 0 | 825 |
| acc idx1 | 2 | 2 | 0 | 0 | 0 | 32 |
| acc v1_n (copy) | 1 | 1 | 0 | 0 | 0 | 1503 |
| acc idx1_n (copy) | 3 | 3 | 0 | 0 | 0 | 47 |
| acc fwd1_n (copy) | 1 | 0 | 1 | 0 | 0 | - |
| acc rbq_n (copy) | 4 | 3 | 1 | 0 | 0 | 13 |
| accumulator port-B latch | 46 | 37 | 9 | 0 | 0 | 1824 |

The undetected faults' flops, grouped by block: `undetected.md` (922 faults).
