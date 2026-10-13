# Coverage report

Sources: 4 Verilator coverage file(s) (coverage_core.dat, coverage_tile.dat, coverage_tile32.dat, coverage_tile8.dat), 4 functional-coverage file(s) (funcov_neo_core.yml, funcov_tile_mesh.yml, funcov_tile_mesh32.yml, funcov_two_layer.yml).

## Line and toggle coverage per RTL file (Verilator --coverage, every test of the build)

| File | branch covered / points | branch % | line covered / points | line % | toggle covered / points | toggle % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| abft_checker.sv | 4 / 4 | 100.0 | 13 / 13 | 100.0 | 15 / 15 | 100.0 |
| acc_bank.sv | 8 / 8 | 100.0 | 10 / 10 | 100.0 | 37 / 38 | 97.4 |
| act_feeder.sv | 32 / 32 | 100.0 | 19 / 19 | 100.0 | 111 / 112 | 99.1 |
| bank_bist_wrap.sv | — | — | — | — | 33 / 35 | 94.3 |
| core_seq.sv | 29 / 30 | 96.7 | 15 / 16 | 93.8 | 54 / 54 | 100.0 |
| crc16_beat.sv | — | — | — | — | 21 / 21 | 100.0 |
| delay_line.sv | 4 / 4 | 100.0 | 2 / 2 | 100.0 | 5 / 5 | 100.0 |
| deskew_out.sv | — | — | — | — | 2 / 2 | 100.0 |
| ecc39.sv | 8 / 8 | 100.0 | 8 / 8 | 100.0 | 13 / 13 | 100.0 |
| esm.sv | 8 / 8 | 100.0 | 4 / 4 | 100.0 | 12 / 12 | 100.0 |
| host_if.sv | 20 / 20 | 100.0 | 32 / 34 | 94.1 | 49 / 50 | 98.0 |
| link_pack.sv | 32 / 32 | 100.0 | 28 / 28 | 100.0 | 33 / 33 | 100.0 |
| mac_pe.sv | 22 / 22 | 100.0 | 4 / 4 | 100.0 | 22 / 22 | 100.0 |
| mbist.sv | 19 / 22 | 86.4 | 16 / 17 | 94.1 | 27 / 33 | 81.8 |
| neo_core.sv | 14 / 14 | 100.0 | 13 / 13 | 100.0 | 157 / 159 | 98.7 |
| neo_icg.sv | 2 / 2 | 100.0 | 1 / 1 | 100.0 | 5 / 5 | 100.0 |
| neo_iso.sv | — | — | — | — | 2 / 3 | 66.7 |
| neo_mac_core.sv | 2 / 2 | 100.0 | 1 / 1 | 100.0 | 21 / 21 | 100.0 |
| neo_mac_core_v02.sv | — | — | — | — | 61 / 61 | 100.0 |
| neo_mem.sv | 17 / 18 | 94.4 | 12 / 12 | 100.0 | 26 / 28 | 92.9 |
| neo_tile.sv | 8 / 8 | 100.0 | 1 / 1 | 100.0 | 198 / 221 | 89.6 |
| noc_router.sv | 16 / 20 | 80.0 | 16 / 16 | 100.0 | 40 / 51 | 78.4 |
| prog_mem.sv | 13 / 14 | 92.9 | 4 / 4 | 100.0 | 16 / 18 | 88.9 |
| requant.sv | 12 / 12 | 100.0 | 21 / 21 | 100.0 | 32 / 32 | 100.0 |
| skew_in.sv | 4 / 4 | 100.0 | 6 / 6 | 100.0 | 7 / 7 | 100.0 |
| sram_bank.sv | 8 / 8 | 100.0 | 3 / 3 | 100.0 | 28 / 29 | 96.6 |
| systolic_array.sv | — | — | — | — | 15 / 15 | 100.0 |
| tile_dma.sv | 30 / 32 | 93.8 | 23 / 24 | 95.8 | 38 / 39 | 97.4 |
| tile_mesh.sv | — | — | — | — | 41 / 47 | 87.2 |
| tile_nic.sv | 167 / 172 | 97.1 | 125 / 126 | 99.2 | 315 / 321 | 98.1 |
| tile_telem.sv | 8 / 8 | 100.0 | 6 / 6 | 100.0 | 7 / 7 | 100.0 |
| wbuf_mem.sv | 8 / 8 | 100.0 | 17 / 17 | 100.0 | 44 / 46 | 95.7 |
| **all** | 495 / 512 | 96.7 | 400 / 406 | 98.5 | 1487 / 1555 | 95.6 |

## Uncovered line points (what no test reached)

### core_seq.sv — 1 uncovered line point(s)

- line 221: `default: state <= S_IDLE;`

### host_if.sv — 2 uncovered line point(s)

- line 128: `8'h0C: prio <= h_wdata[15:0];`
- line 152: `8'h0C: h_rdata <= {16'd0, prio};`

### mbist.sv — 1 uncovered line point(s)

- line 121: `default: state <= S_IDLE;`

### tile_dma.sv — 1 uncovered line point(s)

- line 187: `default: state <= S_IDLE;`

### tile_nic.sv — 1 uncovered line point(s)

- line 1422: `default: tx_state <= X_IDLE;`

## Functional coverage (cocotb-coverage covergroups sampled by the tests)

| File | Covergroup | Coverage | Bins hit / bins | Empty bins |
| --- | --- | ---: | ---: | --- |
| funcov_neo_core.yml | neo | 41.9 % | 36 / 86 | — |
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
| funcov_neo_core.yml | neo.flag | 38.1 % | 8 / 21 | acc_abft, array_abft, bist_fail, crc, ctrl_path, ecc_ce, ecc_ue, iso, lost, parity, prog_ce, timeout, watchdog |
| funcov_neo_core.yml | neo.sparse | 100.0 % | 4 / 4 | — |
| funcov_neo_core.yml | neo.sparse.index | 100.0 % | 4 / 4 | — |
| funcov_neo_core.yml | neo.vec | 87.5 % | 14 / 16 | — |
| funcov_neo_core.yml | neo.vec.op | 87.5 % | 14 / 16 | END, RSST |
| funcov_tile_mesh.yml | neo | 82.6 % | 71 / 86 | — |
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
| funcov_tile_mesh.yml | neo.flag | 61.9 % | 13 / 21 | ctrl, rq, seq, tbl_perr, vec_err, vec_perr, wbuf_ce, wbuf_ue |
| funcov_tile_mesh.yml | neo.sparse | 0.0 % | 0 / 4 | — |
| funcov_tile_mesh.yml | neo.sparse.index | 0.0 % | 0 / 4 | 0, 1, 2, 3 |
| funcov_tile_mesh.yml | neo.vec | 87.5 % | 14 / 16 | — |
| funcov_tile_mesh.yml | neo.vec.op | 87.5 % | 14 / 16 | CLAMP, RELOAD |
| funcov_tile_mesh32.yml | neo | 32.6 % | 28 / 86 | — |
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
| funcov_tile_mesh32.yml | neo.flag | 19.1 % | 4 / 21 | acc_abft, array_abft, bist_fail, ctrl, ctrl_path, ecc_ce, ecc_ue, parity, prog_ce, rq, seq, tbl_perr, vec_err, vec_perr, watchdog, wbuf_ce, wbuf_ue |
| funcov_tile_mesh32.yml | neo.sparse | 0.0 % | 0 / 4 | — |
| funcov_tile_mesh32.yml | neo.sparse.index | 0.0 % | 0 / 4 | 0, 1, 2, 3 |
| funcov_tile_mesh32.yml | neo.vec | 0.0 % | 0 / 16 | — |
| funcov_tile_mesh32.yml | neo.vec.op | 0.0 % | 0 / 16 | ADD_ROW, BMUL, BSUB, CLAMP, EMIT, END, LUT, MUL_SCALE, RED, RELOAD, RSST, SALU, SLD, SLUT, SMUL, SQR |
| funcov_two_layer.yml | neo | 16.3 % | 14 / 86 | — |
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
| funcov_two_layer.yml | neo.flag | 0.0 % | 0 / 21 | acc_abft, array_abft, bist_fail, crc, ctrl, ctrl_path, ecc_ce, ecc_ue, iso, lost, parity, prog_ce, rq, seq, tbl_perr, timeout, vec_err, vec_perr, watchdog, wbuf_ce, wbuf_ue |
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
| neo.flag | 100.0 % | 21 / 21 | — |
| neo.sparse.index | 100.0 % | 4 / 4 | — |
| neo.vec.op | 100.0 % | 16 / 16 | — |
| **all leaf covergroups** | 100.0 % | 86 / 86 | |
