# Stream S1 -- safety closure: SECDED on the activation buffer, checks on the five undetected-fault classes, the campaign
# again, FMEDA v3 meeting the ASIL-B(D) target

Branch `s1-safety-closure`. Read COMMON.md first, then `05_safety/fmeda_v2.md` (D2's result and the undetected list),
`02_dv/dv/cocotb/faults/` (the campaign: `run_faults.py`, `weights.json`, the report scripts), `05_safety/build_fmeda.py`,
the spec §8, the RTL `01_rtl/act_feeder.sv` (the activation buffer: a `neo_mem_rf` leaf of ABUF_DEPTH / 2 pair words of
2 x ROWS x 8 bits, written by the DMA in 16-word beats, read on port B as a pair word per pixel; after C1 a sparse pixel's
64 channels are one pair word), `wbuf_mem.sv` (the model to copy: SECDED lanes around a leaf, `ecc39_enc` / `ecc39_dec`,
`ce` / `ue` stickies), `neo_mac_core_v02.sv` (the index / first delay lines), `skew_in.sv`, `tile_nic.sv` (the drain FIFO's
output register), `mbist.sv`, `neo_tile.sv` (the flags), `09_synthesis/memories.md`.

## Why

FMEDA v2 (drop 0.43, measured by the fault campaign): chip PMHF 1,931 FIT against the 100 FIT ASIL-B(D) target; 28.5 of the
core's 29.6 FIT come from the activation buffer, whose cells have no error code although FMEDA v1 credited it with SECDED
(a 1 Mbit SRAM at 600 FIT / Mbit soft-error rate with 0 % coverage); the rest from four flop groups the comparators and ABFT
do not see: the index / first delay lines of `neo_mac_core_v02` (a wrong accumulator row or a wrong overwrite flag), the
`skew_in` pipes (a wrong activation reaching every column, the check column included), the NIC's drain-FIFO output
register, the MBIST state.

## The items

1. **SECDED on the activation buffer.** Lanes of (39,32) around the leaf as `wbuf_mem` does: the pair word 2 x ROWS x 8 =
   512 bits as 16 lanes of 32 data + 7 check bits = 624 bits; the DMA's write path encodes each lane (a 16-word beat is
   exactly one pair word -- the encoders sit on the beat); the read path decodes after the registered read (`rb_data` ->
   16 `ecc39_dec` -> the entry select -> `x_vec`), `ce` corrected, `ue` sticky (`abuf_ce` / `abuf_ue` as new tile flags
   beside `wbuf_*`: `host_if`'s STATUS, `err_pin`'s causes, the funcov bins, `neo_host.h`). The feeder's timing: the decode
   adds the ~400 ps `wbuf_mem` showed behind the macro's 218 ps -- A1's registered read left room (the feeder is at 898 ps
   with the output through one mux); if the decode puts `x_vec` over the period, register the decoded entry (one more cycle
   of feeder latency: the sequencer's constants and the end times move -- report the accounting). Area: 624 / 512 = +22 %
   of the buffer's macro (22,045 -> ~26,900 µm² at the ports-priced model), 16 encoders + 16 decoders per core; the
   `memories.md` row. Tests: the existing weight-ECC pattern (C9 / cocotb C3) for the activation buffer -- single-bit
   flips corrected bit-exact, double-bit flagged -- through the DV hook or the campaign's injector; the dense and sparse
   baselines unchanged but for the latency you report.
2. **The index / first delay lines** (`neo_mac_core_v02`: `idx_d`, `first_d`, LATENCY deep): protect by duplication with a
   compare at the output (the R7 pattern: a second delay line, `ctrl_err` on a mismatch) or by a parity bit travelling with
   the value (cheaper; a single flip is caught at the accumulator's write) -- choose by area, say why; the fault test flips a
   bit in flight and expects the flag.
3. **The `skew_in` pipes**: a wrong activation byte reaches every column *and* the check column, so ABFT is blind by design.
   Options: (a) parity per byte through the skew chain, checked at the array's row input (a parity bit per row per stage:
   ROWS x PE_LAT x ROWS / 2 flops... measure), (b) the activation buffer's SECDED extended through the chain (the parity of
   the decoded byte travels with it), (c) accept with a measured residual and the FTTI argument (the next layer's ABFT does
   not see it either -- state honestly). Implement (a) or (b) unless the campaign's numbers make (c) defensible; the fault
   test flips a skew register and expects the flag.
4. **The NIC's drain-FIFO output register and the MBIST state**: the drain beat's parity exists on the link (even parity per
   flit); cover the register by computing the flit's parity from the FIFO's stored parity bits (A2a stored three per row)
   rather than from the register, so a flipped register bit is a parity error at the receiver; the MBIST state machine as a
   one-hot with an illegal-state check into `bist_fail` (or `esm`'s FSM error).
5. **The campaign again**: `workflow_dispatch` of `faults` on the branch with the nightly sample size (20 k + 5 k per build);
   the per-block table before / after, the undetected list; then `build_fmeda.py --v3`: FMEDA v3 (`fmeda_v3.md`,
   `neo_fmeda_v3.xlsx`) with the same base rates as v1 / v2 -- the target is **chip PMHF ≤ 100 FIT with the measured
   coverage**; if a residual stays above it, the FMEDA says where and the spec's §8 claim is restated to the measured figure
   (the integrator writes the spec; give the numbers). Also fix v1's base-rate mismatches D2 noted (the activation buffer is
   0.5 Mbit per core at the silicon depth... check `memories.md`'s bits per leaf against the FMEDA's rows) in v3, stating
   the change.

## Verification and report

Local: `make lint` 0 warnings; `make iverilog` / `make verilator`; neosim `run_conv_core.py` (the ECC cases), `run_mac_core.py`;
cocotb `core` and `tile`; `make formal` (the lockstep harnesses list the registers); unit synthesis of `act_feeder` and
`neo_mac_core_v02` before / after (area, min period, the worst path); the campaign on a few hundred faults locally
(`make faults FAULTS_N=...` from the flat layout, one build at a time). Then the PR, green on every job, the `faults`
dispatch read from `ci-logs-faults`. Report: the mechanisms built and their costs (area, timing, latency), the campaign
table before / after with the undetected list, FMEDA v3's SPFM / LFM / PMHF against v2 and the target, the end times with
their accounting, anything unresolved, the PR number.
