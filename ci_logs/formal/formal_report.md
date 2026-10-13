# Formal report -- SymbiYosys (2026-10-13 19:24:15 UTC)

Tools: Yosys 0.69+274 (git sha1 7513f8df8, Release, Clang /usr/bin/clang++ 21.1.8); sby, yosys-smtbmc, yices, bitwuzla, yosys-abc from /home/runner/neo-synth/oss-cad-suite/bin.

10 tasks, 10 PASS, 0 not PASS; wall time 149 s with 2 at a time, 600 s per task. Mode prove = a full proof (k-induction for smtbmc, the reached induction length k in the column; pdr / BDD collapse for abc); bmc = bounded to the depth given.

| Task | What is proven | Mode | Depth | Engine | Result | Reached | Time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `abft_checker` | a row's verdict two cycles after valid: err == (row sum != y_chk) with err_valid; err_sticky after err | prove | 2 | `smtbmc yices` | PASS | k = 2 | 0.6 s |
| `crc16_beat_bw4` | rtl/crc16_beat == the frozen serial reference, every crc_in, beat and word count n = 0..BW | prove | 1 | `abc collapse -r; muxes; strash; pdr` | PASS | proved (pdr) | 1.2 s |
| `crc16_beat_bw16` | rtl/crc16_beat == the frozen serial reference, every crc_in, beat and word count n = 0..BW | prove | 1 | `abc collapse -r; muxes; strash; pdr` | PASS | proved (pdr) | 19.9 s |
| `ecc39` | (39,32) SECDED: dec(enc(d)) == d clean; every single flip corrected (ce); every double flip detected (ue) | prove | 1 | `abc collapse -r; muxes; strash; pdr` | PASS | proved (pdr) | 0.3 s |
| `link_pack_stream` | packer -> unpacker round trip: same words, order and fields; flit parity and size; CRC flush; progress | prove | 2 | `smtbmc bitwuzla` | PASS | k = 1 | 147.4 s |
| `link_pack_parity` | packer -> unpacker round trip: same words, order and fields; flit parity and size; CRC flush; progress | prove | 2 | `smtbmc bitwuzla` | PASS | k = 1 | 8.7 s |
| `lockstep_core_seq` | two core_seq (16x8), identical inputs from reset: the 12 compared outputs equal every cycle | prove | 2 | `smtbmc yices` | PASS | k = 1 | 0.7 s |
| `lockstep_requant` | two requant (COLS = 4), stream after the table is written: out_valid equal, out_q equal while valid | prove | 2 | `smtbmc yices` | PASS | k = 1 | 9.0 s |
| `noc_router` | FIFO invariants, XY routing, odd parity dropped and flagged, no invention, a flit token kept, fairness | prove | 2 | `smtbmc yices` | PASS | k = 1 | 82.4 s |
| `requant_parity` | table parity: no fault, no flag; the fault on a written entry 0 under in_valid flags the next cycle | prove | 4 | `smtbmc yices` | PASS | k = 1 | 1.0 s |
