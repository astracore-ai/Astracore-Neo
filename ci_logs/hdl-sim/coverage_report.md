# Coverage report

Sources: 4 Verilator coverage file(s) (coverage_core.dat, coverage_tile.dat, coverage_tile32.dat, coverage_tile8.dat), 4 functional-coverage file(s) (funcov_neo_core.yml, funcov_tile_mesh.yml, funcov_tile_mesh32.yml, funcov_two_layer.yml).

## Line and toggle coverage per RTL file (Verilator --coverage, every test of the build)

| File | branch covered / points | branch % | line covered / points | line % | toggle covered / points | toggle % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| abft_checker.sv | 2 / 2 | 100.0 | 5 / 5 | 100.0 | 9 / 9 | 100.0 |
| acc_bank.sv | 6 / 6 | 100.0 | 7 / 7 | 100.0 | 20 / 20 | 100.0 |
| act_feeder.sv | 22 / 22 | 100.0 | 10 / 10 | 100.0 | 55 / 55 | 100.0 |
| bank_bist_wrap.sv | — | — | — | — | 33 / 35 | 94.3 |
| core_seq.sv | 27 / 28 | 96.4 | 14 / 15 | 93.3 | 51 / 51 | 100.0 |
| crc16_beat.sv | — | — | — | — | 3 / 3 | 100.0 |
| crc16_word.sv | — | — | 2 / 2 | 100.0 | 5 / 5 | 100.0 |
| delay_line.sv | 4 / 4 | 100.0 | 2 / 2 | 100.0 | 5 / 5 | 100.0 |
| deskew_out.sv | — | — | — | — | 2 / 2 | 100.0 |
| ecc39.sv | 8 / 8 | 100.0 | 16 / 16 | 100.0 | 15 / 15 | 100.0 |
| esm.sv | 8 / 8 | 100.0 | 4 / 4 | 100.0 | 12 / 12 | 100.0 |
| host_if.sv | 18 / 18 | 100.0 | 28 / 28 | 100.0 | 43 / 43 | 100.0 |
| link_pack.sv | 18 / 18 | 100.0 | 13 / 13 | 100.0 | 36 / 36 | 100.0 |
| mac_pe.sv | 10 / 10 | 100.0 | 1 / 1 | 100.0 | 21 / 21 | 100.0 |
| mbist.sv | 17 / 20 | 85.0 | 16 / 17 | 94.1 | 25 / 29 | 86.2 |
| neo_core.sv | 6 / 6 | 100.0 | 8 / 8 | 100.0 | 114 / 115 | 99.1 |
| neo_mac_core.sv | 2 / 2 | 100.0 | 1 / 1 | 100.0 | 19 / 19 | 100.0 |
| neo_mac_core_v02.sv | — | — | — | — | 56 / 56 | 100.0 |
| neo_tile.sv | 8 / 8 | 100.0 | 1 / 1 | 100.0 | 149 / 158 | 94.3 |
| noc_router.sv | 15 / 16 | 93.8 | 15 / 15 | 100.0 | 27 / 27 | 100.0 |
| prog_mem.sv | 11 / 12 | 91.7 | 3 / 3 | 100.0 | 16 / 18 | 88.9 |
| requant.sv | 13 / 14 | 92.9 | 15 / 16 | 93.8 | 25 / 25 | 100.0 |
| skew_in.sv | — | — | — | — | 6 / 6 | 100.0 |
| sram_bank.sv | 16 / 16 | 100.0 | 6 / 6 | 100.0 | 25 / 25 | 100.0 |
| systolic_array.sv | — | — | — | — | 12 / 12 | 100.0 |
| tile_dma.sv | 26 / 26 | 100.0 | 20 / 21 | 95.2 | 33 / 34 | 97.1 |
| tile_mesh.sv | — | — | — | — | 38 / 44 | 86.4 |
| tile_nic.sv | 84 / 90 | 93.3 | 56 / 58 | 96.6 | 173 / 175 | 98.9 |
| wbuf_mem.sv | 14 / 14 | 100.0 | 15 / 15 | 100.0 | 31 / 32 | 96.9 |
| **all** | 335 / 348 | 96.3 | 258 / 264 | 97.7 | 1059 / 1087 | 97.4 |

## Uncovered line points (what no test reached)

### core_seq.sv — 1 uncovered line point(s)

- line 192: `default: state <= S_IDLE;`

### mbist.sv — 1 uncovered line point(s)

- line 106: `default: state <= S_IDLE;`

### requant.sv — 1 uncovered line point(s)

- line 113: `else if (v < -128) out_q[j] <= -8'sd128;`

### tile_dma.sv — 1 uncovered line point(s)

- line 154: `default: state <= S_IDLE;`

### tile_nic.sv — 2 uncovered line point(s)

- line 661: `if (drain_open) begin`
- line 716: `default: tx_state <= X_IDLE;`

## Functional coverage (cocotb-coverage covergroups sampled by the tests)

| File | Covergroup | Coverage | Bins hit / bins | Empty bins |
| --- | --- | ---: | ---: | --- |
| funcov_neo_core.yml | neo | 22.6 % | 12 / 53 | — |
| funcov_neo_core.yml | neo.desc | 30.0 % | 6 / 20 | — |
| funcov_neo_core.yml | neo.desc.ct_n | 25.0 % | 1 / 4 | 1, 2, 4 |
| funcov_neo_core.yml | neo.desc.k | 33.3 % | 1 / 3 | 1, 5 |
| funcov_neo_core.yml | neo.desc.k_x_s | 16.7 % | 1 / 6 | (1, 1), (1, 2), (3, 2), (5, 1), (5, 2) |
| funcov_neo_core.yml | neo.desc.partial_rows | 50.0 % | 1 / 2 | True |
| funcov_neo_core.yml | neo.desc.role | 33.3 % | 1 / 3 | contrib, owner |
| funcov_neo_core.yml | neo.desc.s | 50.0 % | 1 / 2 | 2 |
| funcov_neo_core.yml | neo.dma | 0.0 % | 0 / 11 | — |
| funcov_neo_core.yml | neo.dma.op | 0.0 % | 0 / 11 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 |
| funcov_neo_core.yml | neo.drain | 0.0 % | 0 / 3 | — |
| funcov_neo_core.yml | neo.drain.mode | 0.0 % | 0 / 3 | int32, int8, psum |
| funcov_neo_core.yml | neo.flag | 31.6 % | 6 / 19 | acc_abft, array_abft, bist_fail, crc, ctrl_path, ecc_ce, ecc_ue, iso, lost, parity, prog_ce, timeout, watchdog |
| funcov_tile_mesh.yml | neo | 88.7 % | 47 / 53 | — |
| funcov_tile_mesh.yml | neo.desc | 100.0 % | 20 / 20 | — |
| funcov_tile_mesh.yml | neo.desc.ct_n | 100.0 % | 4 / 4 | — |
| funcov_tile_mesh.yml | neo.desc.k | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh.yml | neo.desc.k_x_s | 100.0 % | 6 / 6 | — |
| funcov_tile_mesh.yml | neo.desc.partial_rows | 100.0 % | 2 / 2 | — |
| funcov_tile_mesh.yml | neo.desc.role | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh.yml | neo.desc.s | 100.0 % | 2 / 2 | — |
| funcov_tile_mesh.yml | neo.dma | 100.0 % | 11 / 11 | — |
| funcov_tile_mesh.yml | neo.dma.op | 100.0 % | 11 / 11 | — |
| funcov_tile_mesh.yml | neo.drain | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh.yml | neo.drain.mode | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh.yml | neo.flag | 68.4 % | 13 / 19 | ctrl, rq, seq, tbl_perr, wbuf_ce, wbuf_ue |
| funcov_tile_mesh32.yml | neo | 50.9 % | 27 / 53 | — |
| funcov_tile_mesh32.yml | neo.desc | 45.0 % | 9 / 20 | — |
| funcov_tile_mesh32.yml | neo.desc.ct_n | 25.0 % | 1 / 4 | 1, 2, 4 |
| funcov_tile_mesh32.yml | neo.desc.k | 33.3 % | 1 / 3 | 1, 5 |
| funcov_tile_mesh32.yml | neo.desc.k_x_s | 16.7 % | 1 / 6 | (1, 1), (1, 2), (3, 2), (5, 1), (5, 2) |
| funcov_tile_mesh32.yml | neo.desc.partial_rows | 100.0 % | 2 / 2 | — |
| funcov_tile_mesh32.yml | neo.desc.role | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh32.yml | neo.desc.s | 50.0 % | 1 / 2 | 2 |
| funcov_tile_mesh32.yml | neo.dma | 100.0 % | 11 / 11 | — |
| funcov_tile_mesh32.yml | neo.dma.op | 100.0 % | 11 / 11 | — |
| funcov_tile_mesh32.yml | neo.drain | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh32.yml | neo.drain.mode | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh32.yml | neo.flag | 21.1 % | 4 / 19 | acc_abft, array_abft, bist_fail, ctrl, ctrl_path, ecc_ce, ecc_ue, parity, prog_ce, rq, seq, tbl_perr, watchdog, wbuf_ce, wbuf_ue |
| funcov_two_layer.yml | neo | 24.5 % | 13 / 53 | — |
| funcov_two_layer.yml | neo.desc | 30.0 % | 6 / 20 | — |
| funcov_two_layer.yml | neo.desc.ct_n | 25.0 % | 1 / 4 | 2, 3, 4 |
| funcov_two_layer.yml | neo.desc.k | 33.3 % | 1 / 3 | 1, 5 |
| funcov_two_layer.yml | neo.desc.k_x_s | 16.7 % | 1 / 6 | (1, 1), (1, 2), (3, 2), (5, 1), (5, 2) |
| funcov_two_layer.yml | neo.desc.partial_rows | 50.0 % | 1 / 2 | True |
| funcov_two_layer.yml | neo.desc.role | 33.3 % | 1 / 3 | contrib, owner |
| funcov_two_layer.yml | neo.desc.s | 50.0 % | 1 / 2 | 2 |
| funcov_two_layer.yml | neo.dma | 54.5 % | 6 / 11 | — |
| funcov_two_layer.yml | neo.dma.op | 54.5 % | 6 / 11 | 4, 6, 9, 10, 11 |
| funcov_two_layer.yml | neo.drain | 33.3 % | 1 / 3 | — |
| funcov_two_layer.yml | neo.drain.mode | 33.3 % | 1 / 3 | int32, psum |
| funcov_two_layer.yml | neo.flag | 0.0 % | 0 / 19 | acc_abft, array_abft, bist_fail, crc, ctrl, ctrl_path, ecc_ce, ecc_ue, iso, lost, parity, prog_ce, rq, seq, tbl_perr, timeout, watchdog, wbuf_ce, wbuf_ue |

### Merged over all files (a bin counts when any build hit it)

| Covergroup | Coverage | Bins hit / bins | Empty bins |
| --- | ---: | ---: | --- |
| neo.desc.ct_n | 100.0 % | 4 / 4 | — |
| neo.desc.k | 100.0 % | 3 / 3 | — |
| neo.desc.k_x_s | 100.0 % | 6 / 6 | — |
| neo.desc.partial_rows | 100.0 % | 2 / 2 | — |
| neo.desc.role | 100.0 % | 3 / 3 | — |
| neo.desc.s | 100.0 % | 2 / 2 | — |
| neo.dma.op | 100.0 % | 11 / 11 | — |
| neo.drain.mode | 100.0 % | 3 / 3 | — |
| neo.flag | 100.0 % | 19 / 19 | — |
| **all leaf covergroups** | 100.0 % | 53 / 53 | |
