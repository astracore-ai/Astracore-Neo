# Stream A1c -- the buffer reads: synchronous (registered) reads of the activation, weight and accumulator arrays, an
# incremental address generator in the feeder, and the sequencer's cycle counts re-derived

Branch `a1c-reg-reads`. Read COMMON.md first. Files: `01_rtl/act_feeder.sv`, `01_rtl/wbuf_mem.sv`, `01_rtl/acc_bank.sv`
(the read-modify-write only -- stream A1b edits the same file's ABFT checker instantiation and adds an `err_valid` port;
keep to the accumulate block and expect a small rebase when A1b merges first), `01_rtl/neo_core.sv` (the `w_load`
alignment), `01_rtl/core_seq.sv` (the cycle counts), `01_rtl/neo_mac_core_v02.sv` only if an alignment needs it, tests
under `02_dv/`. Do NOT touch `neo_mem.sv` (the leaves' interface is fixed: port A asynchronous, port B registered with
`rb_en`), `tile_nic.sv`, `crc16_*`, `requant.sv`, `abft_checker.sv`, `noc_router.sv`, `link_pack.sv`, `synth.py` -- the
other A1 streams own those and run beside you.

## Why (drop 0.40 synthesis, ASAP7 SS, 500 ps budget; `09_synthesis/synth_report_drop040_sandbox.md`)

| Unit | Worst path | Arrival | What it is |
|---|---|---|---|
| `act_feeder` | `cfg_s[2] -> x_vec[*]` | 3,005 ps | the address is a combinational function of the (oy, ox) counters and the configuration: `iy = oy*s + ky - p`, `ix = ox*s + kx - p`, `addr = (ct & regions_m1)*tile_pixels + (iy - iy0)*w + ix` -- three 18-bit multipliers and a chain of adders, then the asynchronous read of the leaf (218 ps model) and the entry select, straight into the array's skew registers |
| `neo_core` | `cfg_s[2] -> u_dp` | 3,011 ps (min period 3,430) | the same path seen from the core |
| `wbuf_mem` | `raddr[9] -> rdata[*]` | 883 ps | the asynchronous read of the 1,024-row weight buffer, fed by the sequencer's `w_raddr` register and feeding the array's shadow registers in the same cycle |
| `acc_bank` | `u_mem -> abft_err` | 1,644 ps | the drain read into the (combinational) ABFT checker -- A1b pipelines the checker; what remains yours is the accumulate: asynchronous read at the written row, 32-bit add, write, in one cycle (`u_mem -> row_new -> u_mem`) |

Beyond the picoseconds there is a silicon reason: these three arrays (1 Mbit activation buffer, 273 kbit weight buffer,
540 kbit accumulator) are SRAM macros, and SRAM macros have synchronous reads -- address at the edge, data the next
cycle. The leaves' port B (`rb_en`, `rb_addr` -> `rb_data` one cycle later) is that port; port A's asynchronous read is a
register-file read that no compiler delivers at these depths. After this stream every read of the three arrays is a port-B
read, and `09_synthesis/memories.md`'s macro rows say so (edit the three rows' "ports" column; do not change the model).

## 1. `act_feeder`: incremental addresses, then a synchronous read

Keep the module's ports. Inside:

- Address generation without multipliers in the per-pixel path. Registers `iy`, `ix` (18 bits signed), `rowb` (the
  address of pixel (iy, 0) in the region: `region_base + (iy - iy0) * w`, AW bits), and the run constants `sw = s * w`
  (registered at `start`). At `start` (the cycle today's FSM loads `oy <= cfg_oy0`): `iy <= oy0*s + ky - p`,
  `ix <= kx - p`, `rowb <= (ct & regions_m1)*tile_pixels + (oy0*s + ky - p - iy0)*w` -- these multiplies are from the
  configuration inputs only (static by protocol, and A1b constrains them as multicycle paths) into registers; they are
  not in the per-cycle path. Per output pixel (the `running && !tok` branch): `ix <= ix + s`; at a row end
  (`ox == wo - 1`): `ix <= kx - p`, `iy <= iy + s`, `rowb <= rowb + sw`. `inb = iy in [0, h) && ix in [0, w)` (compares
  against static cfg), `addr = inb ? rowb + ix : 0` (one add). Keep `oy`/`ox` as the loop counters if they are the
  simplest way to detect the row end and the run end (they are not in the address any more). Mirror every register in the
  duplicate generator (R7) -- it must stay an independent copy with its own registers, and the comparator keeps comparing
  `addr`, `inb`, `valid`, `swap`, `row_idx` between the two. The fault hook stays on the primary's `addr`.
- The read: port B of the leaf (`rb_en = valid_gen`, `rb_addr = addr[AW-1:1]`), so the entry arrives one cycle after the
  address. Every output of the feeder moves one cycle with it: `valid`, `swap`, `row_idx`, `first` are the generated
  values delayed by one register (`x_vec` is `rb_data`'s entry, selected by the delayed `addr[0]`, zeroed when the delayed
  `inb` is 0 or the delayed valid is 0); `busy = running || out_pending` so the sequencer sees the run end when the last
  row has left. Port A (`ra_addr`) is tied off and `ra_data` goes to the unused sink. The token therefore leaves two cycles
  after `start` (today: one) and the first row the cycle after it; the data stream, the token and the index/first
  pipeline in `neo_mac_core_v02` (which start from the feeder's outputs) stay aligned with each other by construction.
- The comparator: `valid`/`swap`/`row_idx` compared on the delayed outputs of both copies, `addr`/`inb` in the generation
  cycle (both copies), as today.
- Expected: the unit's worst path becomes an 18-bit add or compare (~400-600 ps); `x_vec` leaves the leaf's `rb_data`
  through one mux; the core's `cfg_s -> u_dp` path is gone.

## 2. `wbuf_mem`: synchronous read

The sequencer presents `w_load`/`w_raddr` for ROWS cycles; the array loads `w_in`/`wc_in` into the shadow chain under
`w_load`. Make the read port B (`rb_en = 1` or the read enable the module has, `rb_addr = raddr`), so `rdata` is one
cycle after `raddr`, and in `neo_core.sv` delay `w_load` by one register (`w_load_q`) on its way to `u_dp` so that the
array sees `w_load` in the cycle the data is valid. (The lockstep comparator in `neo_core` compares the two sequencers'
`w_load`/`w_raddr`, not the delayed copy -- unchanged.) The SECDED decode stays behind the read (`rb_data` -> the
`ecc39_dec` lanes -> `rdata`, with the entry selected by `raddr[0]` delayed one cycle to match), and the `ecc_ce/ue`
flags now rise one cycle later -- check the DV hook tests (`dv_fi_*`, neosim C9, the cocotb weight-ECC test) still find
their fault at the cycle they now expect. Expected: `wbuf_mem`'s path is the leaf's clock-to-output plus the decoder
(~600 ps) instead of address -> array -> decoder.

## 3. `acc_bank`: two-cycle read-modify-write with forwarding, one synchronous read port shared with the drain

Today: port A reads row `acc_idx` asynchronously, the adders add, the write stores -- all in one cycle; port B is the
drain's registered read. New:

- Stage 0 (the cycle `wr_valid` / `ext_valid` arrives): register the operation (`v0`, `idx0`, `first0`, the COLS+1
  addends as one `RW`-bit vector `add0`) and issue the read on port B at `acc_idx`.
- Stage 1: `old = fwd ? new_q : rb_data`, where `fwd = v1_prev && (idx1_prev == idx1)` -- the previous operation wrote
  this row in this very cycle and the registered read did not see it; `new = (first1 ? 0 : old) + add1` per column (the
  COLS+1 adders as today), written to row `idx1` (`we`, `waddr`, `wdata`) and kept in `new_q` for the next forward.
  One forwarding stage is enough: an operation two cycles back has completed its write before this one's read.
- The drain: `rd_en`/`rd_idx` use the same port B (`rb_addr = rd_en ? rd_idx : acc_idx`, `rb_en = rd_en || acc_we`), and
  `rd_valid` stays `rd_en` delayed by one. The two never overlap by protocol (the sequencer drains in S_DRAIN after
  S_WAIT, and the reduce port's writes happen in S_REDUCE before the drain; state in the header comment that a drain read
  in the cycle after an accumulate would read the row before that accumulate's write, and that the sequencer's wait
  covers it -- see §4). Port A is tied off and goes to an unused sink: the macro is a one-write-one-read synchronous SRAM.
- `ext_ready = !wr_valid` stays; a local write at t and a reduce write at t+1 to the same row are handled by the forward.
- The ABFT checker instantiation and the `rd_data`/`rd_chk` unpacking stay as they are (A1b's lines).
- Expected: `acc_bank`'s accumulate path is `rb_data -> 32-bit adder -> wdata` (~500 ps); the leaf's ports show the
  macro as 1W1R synchronous.

## 4. `core_seq`: the cycle counts re-derived

The feeder's output is one cycle later than today and the accumulator write one cycle later than the core's `valid_out`.
Re-derive, write down in the header as a small table, and set: the shadow load of the next tile (`tcnt == LAT + 1`: the
load may start when the previous token has cleared the array -- check whether the extra feeder cycle moves it), the
run-to-run start condition (`!f_busy && loaded && tcnt > 2`), the wait before the drain (`wcnt == LAT + 3`: the last row's
result must be written -- it now lands one cycle later -- before `rd_en` reads row 0; add exactly what the pipeline needs,
with a one-line derivation per constant, not a guess), and the reduce-before-drain transition. Both sequencer copies
change identically (one module). The `LAT` localparam itself (the array's own latency) does not change. If the
`neo_mac_core_v02` comment "start >= ROWS+COLS cycles after that run's start" needs the new figure, update it.

## 5. Verification and report

Measure before you change: unit synthesis of `act_feeder` (11 s), `wbuf_mem` (13 s), `acc_bank` (15 s) from your flat
layout (`python3 synth/synth.py --rtl rtl --blocks act_feeder,wbuf_mem,acc_bank --out <dir> --jobs 1` with the env of
COMMON.md; then `report.py`), and keep the paths. After the change, the same three plus `core_seq` (5 s) and `neo_core`
(whose children must be synthesized in the same `--out`: `--blocks neo_core` pulls the subtree, ~6 min and ~1.5 GB with
`requant` -- run it alone, with `ps` checked first) -- report the before/after min period, WNS, worst path, cells, flops.

Simulation (COMMON.md's limits; A1a, A1b and D1 share the machine): `make lint` 0 warnings; `make iverilog_core`; `make
verilator` (tb_neo_mac_core is unaffected, tb_neo_core and tb_tile_mesh are not); neosim `sim/run_mac_core.py --vectors
...`, `sim/run_conv_core.py`, `sim/run_conv_v02.py`, `sim/run_ksplit.py`, `sim/run_tile_test.py M3`; cocotb `make -j1 -C
dv/cocotb core` (3 tests, 7,890.00 ns today) and `tile` (17 + M14 skipped, 46,282.52 ns today). Every end time moves: give
the new figures with the cycle accounting (per run: +1 feeder cycle; per drain: +N from §4; the shadow-load shift if any).
The fault-injection tests (array fault, accumulator bit flip, feeder control fault via `ctrl_fault_inject`, weight-buffer
faults) must find exactly what they find today. Then the PR (`gh pr create` works, `gh pr list` does not -- use `gh api
repos/astracore-ai/Astracore-Neo/pulls`; runs via `gh run list --branch a1c-reg-reads`, `gh run view <id> --json jobs`,
`gh run view <id> --log-failed --job <jobid>`), iterate to green, and report: what changed per module, the synthesis
before/after table, the end-time table with the accounting, the sequencer's new constants with their derivation, anything
unresolved, the PR number.
