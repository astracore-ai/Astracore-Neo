# Coverage report

Sources: 4 Verilator coverage file(s) (coverage_core.dat, coverage_tile.dat, coverage_tile32.dat, coverage_tile8.dat), 4 functional-coverage file(s) (funcov_neo_core.yml, funcov_tile_mesh.yml, funcov_tile_mesh32.yml, funcov_two_layer.yml).

## Line and toggle coverage per RTL file (Verilator --coverage, every test of the build)

| File | branch covered / points | branch % | line covered / points | line % | toggle covered / points | toggle % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| abft_checker.sv | 4 / 4 | 100.0 | 13 / 13 | 100.0 | 15 / 15 | 100.0 |
| acc_bank.sv | 8 / 8 | 100.0 | 10 / 10 | 100.0 | 37 / 38 | 97.4 |
| act_feeder.sv | 46 / 46 | 100.0 | 28 / 28 | 100.0 | 146 / 148 | 98.6 |
| bank_bist_wrap.sv | — | — | — | — | 33 / 35 | 94.3 |
| core_seq.sv | 29 / 30 | 96.7 | 15 / 16 | 93.8 | 54 / 54 | 100.0 |
| crc16_beat.sv | — | — | — | — | 21 / 21 | 100.0 |
| delay_line.sv | 4 / 4 | 100.0 | 2 / 2 | 100.0 | 5 / 5 | 100.0 |
| deskew_out.sv | — | — | — | — | 2 / 2 | 100.0 |
| ecc39.sv | 8 / 8 | 100.0 | 8 / 8 | 100.0 | 13 / 13 | 100.0 |
| esm.sv | 8 / 8 | 100.0 | 4 / 4 | 100.0 | 12 / 12 | 100.0 |
| host_if.sv | 20 / 20 | 100.0 | 32 / 34 | 94.1 | 49 / 50 | 98.0 |
| link_pack.sv | 34 / 34 | 100.0 | 31 / 31 | 100.0 | 35 / 35 | 100.0 |
| mac_pe.sv | 22 / 22 | 100.0 | 4 / 4 | 100.0 | 22 / 22 | 100.0 |
| mbist.sv | 21 / 24 | 87.5 | 17 / 17 | 100.0 | 29 / 35 | 82.9 |
| neo_core.sv | 14 / 14 | 100.0 | 13 / 13 | 100.0 | 167 / 169 | 98.8 |
| neo_icg.sv | 2 / 2 | 100.0 | 1 / 1 | 100.0 | 5 / 5 | 100.0 |
| neo_iso.sv | — | — | — | — | 2 / 3 | 66.7 |
| neo_mac_core.sv | 4 / 4 | 100.0 | 4 / 4 | 100.0 | 29 / 29 | 100.0 |
| neo_mac_core_v02.sv | 2 / 2 | 100.0 | 3 / 3 | 100.0 | 76 / 76 | 100.0 |
| neo_mem.sv | 17 / 18 | 94.4 | 12 / 12 | 100.0 | 26 / 28 | 92.9 |
| neo_tile.sv | 8 / 8 | 100.0 | 1 / 1 | 100.0 | 205 / 234 | 87.6 |
| noc_router.sv | 16 / 20 | 80.0 | 16 / 16 | 100.0 | 40 / 51 | 78.4 |
| prog_mem.sv | 13 / 14 | 92.9 | 4 / 4 | 100.0 | 18 / 21 | 85.7 |
| requant.sv | 12 / 12 | 100.0 | 23 / 23 | 100.0 | 33 / 33 | 100.0 |
| skew_in.sv | 8 / 8 | 100.0 | 12 / 12 | 100.0 | 9 / 9 | 100.0 |
| sram_bank.sv | 8 / 8 | 100.0 | 3 / 3 | 100.0 | 28 / 29 | 96.6 |
| systolic_array.sv | — | — | — | — | 15 / 15 | 100.0 |
| tile_dma.sv | 30 / 32 | 93.8 | 23 / 24 | 95.8 | 39 / 40 | 97.5 |
| tile_mesh.sv | — | — | — | — | 43 / 52 | 82.7 |
| tile_nic.sv | 171 / 176 | 97.2 | 125 / 126 | 99.2 | 318 / 324 | 98.1 |
| tile_telem.sv | 8 / 8 | 100.0 | 6 / 6 | 100.0 | 7 / 7 | 100.0 |
| wbuf_mem.sv | 8 / 8 | 100.0 | 17 / 17 | 100.0 | 45 / 47 | 95.7 |
| **all** | 525 / 542 | 96.9 | 427 / 432 | 98.8 | 1578 / 1657 | 95.2 |

## Uncovered line points (what no test reached)

### core_seq.sv — 1 uncovered line point(s)

- line 226: `default: state <= S_IDLE;`

### host_if.sv — 2 uncovered line point(s)

- line 130: `8'h0C: prio <= h_wdata[15:0];`
- line 154: `8'h0C: h_rdata <= {16'd0, prio};`

### tile_dma.sv — 1 uncovered line point(s)

- line 189: `default: state <= S_IDLE;`

### tile_nic.sv — 1 uncovered line point(s)

- line 1438: `default: tx_state <= X_IDLE;`

## Functional coverage (cocotb-coverage covergroups sampled by the tests)

| File | Covergroup | Coverage | Bins hit / bins | Empty bins |
| --- | --- | ---: | ---: | --- |
| funcov_neo_core.yml | neo | 42.9 % | 39 / 91 | — |
| funcov_neo_core.yml | neo.desc | 45.5 % | 10 / 22 | — |
| funcov_neo_core.yml | neo.desc.ct_n | 50.0 % | 2 / 4 | 1, 4 |
| funcov_neo_core.yml | neo.desc.k | 33.3 % | 1 / 3 | 1, 5 |
| funcov_neo_core.yml | neo.desc.k_x_s | 16.7 % | 1 / 6 | (1, 1), (1, 2), (3, 2), (5, 1), (5, 2) |
| funcov_neo_core.yml | neo.desc.partial_rows | 50.0 % | 1 / 2 | True |
| funcov_neo_core.yml | neo.desc.role | 66.7 % | 2 / 3 | contrib |
| funcov_neo_core.yml | neo.desc.s | 50.0 % | 1 / 2 | 2 |
| funcov_neo_core.yml | neo.desc.sparse | 100.0 % | 2 / 2 | — |
| funcov_neo_core.yml | neo.dma | 0.0 % | 0 / 11 | — |
| funcov_neo_core.yml | neo.dma.op | 0.0 % | 0 / 11 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 |
| funcov_neo_core.yml | neo.drain | 0.0 % | 0 / 8 | — |
| funcov_neo_core.yml | neo.drain.layout | 0.0 % | 0 / 5 | entry, grouped, pad, rows, transpose |
| funcov_neo_core.yml | neo.drain.mode | 0.0 % | 0 / 3 | int32, int8, psum |
| funcov_neo_core.yml | neo.fetch | 0.0 % | 0 / 4 | — |
| funcov_neo_core.yml | neo.fetch.format | 0.0 % | 0 / 4 | act, rbuf, w_int4, w_int8 |
| funcov_neo_core.yml | neo.flag | 42.3 % | 11 / 26 | acc_abft, array_abft, bist_fail, crc, ctrl_path, drain_ovf, ecc_ce, ecc_ue, iso, lost, parity, pk_perr, prog_ce, timeout, watchdog |
| funcov_neo_core.yml | neo.sparse | 100.0 % | 4 / 4 | — |
| funcov_neo_core.yml | neo.sparse.index | 100.0 % | 4 / 4 | — |
| funcov_neo_core.yml | neo.vec | 87.5 % | 14 / 16 | — |
| funcov_neo_core.yml | neo.vec.op | 87.5 % | 14 / 16 | END, RSST |
| funcov_tile_mesh.yml | neo | 78.0 % | 71 / 91 | — |
| funcov_tile_mesh.yml | neo.desc | 95.5 % | 21 / 22 | — |
| funcov_tile_mesh.yml | neo.desc.ct_n | 100.0 % | 4 / 4 | — |
| funcov_tile_mesh.yml | neo.desc.k | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh.yml | neo.desc.k_x_s | 100.0 % | 6 / 6 | — |
| funcov_tile_mesh.yml | neo.desc.partial_rows | 100.0 % | 2 / 2 | — |
| funcov_tile_mesh.yml | neo.desc.role | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh.yml | neo.desc.s | 100.0 % | 2 / 2 | — |
| funcov_tile_mesh.yml | neo.desc.sparse | 50.0 % | 1 / 2 | 1 |
| funcov_tile_mesh.yml | neo.dma | 100.0 % | 11 / 11 | — |
| funcov_tile_mesh.yml | neo.dma.op | 100.0 % | 11 / 11 | — |
| funcov_tile_mesh.yml | neo.drain | 100.0 % | 8 / 8 | — |
| funcov_tile_mesh.yml | neo.drain.layout | 100.0 % | 5 / 5 | — |
| funcov_tile_mesh.yml | neo.drain.mode | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh.yml | neo.fetch | 100.0 % | 4 / 4 | — |
| funcov_tile_mesh.yml | neo.fetch.format | 100.0 % | 4 / 4 | — |
| funcov_tile_mesh.yml | neo.flag | 50.0 % | 13 / 26 | abuf_ce, abuf_ue, act_perr, ctrl, drain_ovf, pk_perr, rq, seq, tbl_perr, vec_err, vec_perr, wbuf_ce, wbuf_ue |
| funcov_tile_mesh.yml | neo.sparse | 0.0 % | 0 / 4 | — |
| funcov_tile_mesh.yml | neo.sparse.index | 0.0 % | 0 / 4 | 0, 1, 2, 3 |
| funcov_tile_mesh.yml | neo.vec | 87.5 % | 14 / 16 | — |
| funcov_tile_mesh.yml | neo.vec.op | 87.5 % | 14 / 16 | CLAMP, RELOAD |
| funcov_tile_mesh32.yml | neo | 30.8 % | 28 / 91 | — |
| funcov_tile_mesh32.yml | neo.desc | 45.5 % | 10 / 22 | — |
| funcov_tile_mesh32.yml | neo.desc.ct_n | 25.0 % | 1 / 4 | 1, 2, 4 |
| funcov_tile_mesh32.yml | neo.desc.k | 33.3 % | 1 / 3 | 1, 5 |
| funcov_tile_mesh32.yml | neo.desc.k_x_s | 16.7 % | 1 / 6 | (1, 1), (1, 2), (3, 2), (5, 1), (5, 2) |
| funcov_tile_mesh32.yml | neo.desc.partial_rows | 100.0 % | 2 / 2 | — |
| funcov_tile_mesh32.yml | neo.desc.role | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh32.yml | neo.desc.s | 50.0 % | 1 / 2 | 2 |
| funcov_tile_mesh32.yml | neo.desc.sparse | 50.0 % | 1 / 2 | 1 |
| funcov_tile_mesh32.yml | neo.dma | 100.0 % | 11 / 11 | — |
| funcov_tile_mesh32.yml | neo.dma.op | 100.0 % | 11 / 11 | — |
| funcov_tile_mesh32.yml | neo.drain | 37.5 % | 3 / 8 | — |
| funcov_tile_mesh32.yml | neo.drain.layout | 0.0 % | 0 / 5 | entry, grouped, pad, rows, transpose |
| funcov_tile_mesh32.yml | neo.drain.mode | 100.0 % | 3 / 3 | — |
| funcov_tile_mesh32.yml | neo.fetch | 0.0 % | 0 / 4 | — |
| funcov_tile_mesh32.yml | neo.fetch.format | 0.0 % | 0 / 4 | act, rbuf, w_int4, w_int8 |
| funcov_tile_mesh32.yml | neo.flag | 15.4 % | 4 / 26 | abuf_ce, abuf_ue, acc_abft, act_perr, array_abft, bist_fail, ctrl, ctrl_path, drain_ovf, ecc_ce, ecc_ue, parity, pk_perr, prog_ce, rq, seq, tbl_perr, vec_err, vec_perr, watchdog, wbuf_ce, wbuf_ue |
| funcov_tile_mesh32.yml | neo.sparse | 0.0 % | 0 / 4 | — |
| funcov_tile_mesh32.yml | neo.sparse.index | 0.0 % | 0 / 4 | 0, 1, 2, 3 |
| funcov_tile_mesh32.yml | neo.vec | 0.0 % | 0 / 16 | — |
| funcov_tile_mesh32.yml | neo.vec.op | 0.0 % | 0 / 16 | ADD_ROW, BMUL, BSUB, CLAMP, EMIT, END, LUT, MUL_SCALE, RED, RELOAD, RSST, SALU, SLD, SLUT, SMUL, SQR |
| funcov_two_layer.yml | neo | 15.4 % | 14 / 91 | — |
| funcov_two_layer.yml | neo.desc | 31.8 % | 7 / 22 | — |
| funcov_two_layer.yml | neo.desc.ct_n | 25.0 % | 1 / 4 | 2, 3, 4 |
| funcov_two_layer.yml | neo.desc.k | 33.3 % | 1 / 3 | 1, 5 |
| funcov_two_layer.yml | neo.desc.k_x_s | 16.7 % | 1 / 6 | (1, 1), (1, 2), (3, 2), (5, 1), (5, 2) |
| funcov_two_layer.yml | neo.desc.partial_rows | 50.0 % | 1 / 2 | True |
| funcov_two_layer.yml | neo.desc.role | 33.3 % | 1 / 3 | contrib, owner |
| funcov_two_layer.yml | neo.desc.s | 50.0 % | 1 / 2 | 2 |
| funcov_two_layer.yml | neo.desc.sparse | 50.0 % | 1 / 2 | 1 |
| funcov_two_layer.yml | neo.dma | 54.5 % | 6 / 11 | — |
| funcov_two_layer.yml | neo.dma.op | 54.5 % | 6 / 11 | 4, 6, 9, 10, 11 |
| funcov_two_layer.yml | neo.drain | 12.5 % | 1 / 8 | — |
| funcov_two_layer.yml | neo.drain.layout | 0.0 % | 0 / 5 | entry, grouped, pad, rows, transpose |
| funcov_two_layer.yml | neo.drain.mode | 33.3 % | 1 / 3 | int32, psum |
| funcov_two_layer.yml | neo.fetch | 0.0 % | 0 / 4 | — |
| funcov_two_layer.yml | neo.fetch.format | 0.0 % | 0 / 4 | act, rbuf, w_int4, w_int8 |
| funcov_two_layer.yml | neo.flag | 0.0 % | 0 / 26 | abuf_ce, abuf_ue, acc_abft, act_perr, array_abft, bist_fail, crc, ctrl, ctrl_path, drain_ovf, ecc_ce, ecc_ue, iso, lost, parity, pk_perr, prog_ce, rq, seq, tbl_perr, timeout, vec_err, vec_perr, watchdog, wbuf_ce, wbuf_ue |
| funcov_two_layer.yml | neo.sparse | 0.0 % | 0 / 4 | — |
| funcov_two_layer.yml | neo.sparse.index | 0.0 % | 0 / 4 | 0, 1, 2, 3 |
| funcov_two_layer.yml | neo.vec | 0.0 % | 0 / 16 | — |
| funcov_two_layer.yml | neo.vec.op | 0.0 % | 0 / 16 | ADD_ROW, BMUL, BSUB, CLAMP, EMIT, END, LUT, MUL_SCALE, RED, RELOAD, RSST, SALU, SLD, SLUT, SMUL, SQR |

### Merged over all files (a bin counts when any build hit it)

| Covergroup | Coverage | Bins hit / bins | Empty bins |
| --- | ---: | ---: | --- |
| neo.desc.ct_n | 100.0 % | 4 / 4 | — |
| neo.desc.k | 100.0 % | 3 / 3 | — |
| neo.desc.k_x_s | 100.0 % | 6 / 6 | — |
| neo.desc.partial_rows | 100.0 % | 2 / 2 | — |
| neo.desc.role | 100.0 % | 3 / 3 | — |
| neo.desc.s | 100.0 % | 2 / 2 | — |
| neo.desc.sparse | 100.0 % | 2 / 2 | — |
| neo.dma.op | 100.0 % | 11 / 11 | — |
| neo.drain.layout | 100.0 % | 5 / 5 | — |
| neo.drain.mode | 100.0 % | 3 / 3 | — |
| neo.fetch.format | 100.0 % | 4 / 4 | — |
| neo.flag | 92.3 % | 24 / 26 | drain_ovf, pk_perr |
| neo.sparse.index | 100.0 % | 4 / 4 | — |
| neo.vec.op | 100.0 % | 16 / 16 | — |
| **all leaf covergroups** | 97.8 % | 89 / 91 | |
